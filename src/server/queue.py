"""TTS job queue — Redis Streams wrapper.

The gateway enqueues TtsJobPayload messages on a Redis Stream; GPU
workers (Faz B+, src/worker/) consume via XREADGROUP and ack with XACK.
This module is the gateway-side surface — no consumer logic lives here.

Stream layout:
    nqai.tts.jobs               primary job queue
    nqai.tts.jobs.dlq           Faz B+: poisoned messages after N retries
    nqai.tts.results.<rid>      per-request result chunk channel (TTL 600s)

Each XADD entry on the job stream carries a JSON-encoded TtsJobPayload.
Workers parse, run inference, and XADD chunks back onto the per-request
results stream as TtsResult entries. The gateway XREAD-loops the
results stream and forwards chunks to the client (WebSocket or HTTP
chunked). Final chunk → gateway DEL the stream; worker also sets
EXPIRE 600 as a safety net in case the gateway crashes mid-read.

Result channel design rationale (decision log 2026-05-24):
  * Per-request stream chosen over shared+filter — no read amplification
    in multi-gateway pods, clean isolation, scale-roadmap §3 diagram
  * Per-request stream chosen over pub/sub — at-least-once (D-06) needs
    persistence; pub/sub drops on momentary gateway disconnect
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger("nqai_voice.queue")

DEFAULT_STREAM = "nqai.tts.jobs"
DEFAULT_MAXLEN = 10_000  # XADD MAXLEN ~ trims the stream to this approx size

RESULTS_STREAM_PREFIX = "nqai.tts.results."
DEFAULT_RESULTS_TTL_SECONDS = 600  # safety net if gateway never DELs


def result_stream_name(request_id: str | uuid.UUID) -> str:
    """Per-request result channel name. Stable across worker/gateway.

    Accepts string or UUID — string path is the wire format the worker
    sees on TtsJobPayload.request_id, the UUID path is the gateway's
    parsed Idempotency-Key.
    """
    return f"{RESULTS_STREAM_PREFIX}{request_id}"


@dataclass(frozen=True)
class TtsJobPayload:
    """Wire-format for a queued synthesis job. Mirrors TtsJobCreateBody
    minus client-only fields (idempotency key lives in the DB row, not
    on the stream).
    """

    request_id: str  # UUID string — also the IdempotencyRepo primary key
    tenant_id: str
    api_key_id: str
    voice_id: str
    text: str
    language: str = "tr"
    audio_format: str = "wav"
    params: dict[str, Any] | None = None  # cfg_value, inference_timesteps overrides
    callback_url: str | None = None  # Faz B+: server-to-server completion hook

    def encode(self) -> dict[str, str]:
        """Render to Redis-friendly field/value pairs. Streams accept
        bytes-or-str maps; JSON-encoded `payload` keeps it readable from
        `redis-cli XRANGE`."""
        return {"payload": json.dumps(asdict(self), ensure_ascii=False)}

    @classmethod
    def decode(cls, fields: dict[bytes | str, bytes | str]) -> TtsJobPayload:
        # Redis returns bytes by default; client code can be either.
        def _s(v: bytes | str) -> str:
            return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v

        raw = _s(fields[b"payload"] if b"payload" in fields else fields["payload"])
        return cls(**json.loads(raw))


# --------------------------------------------------------------------------- #
# Result chunks — worker → gateway per-request stream
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TtsResult:
    """One audio chunk on the per-request result stream.

    Field-level XADD (not JSON-wrapped) keeps each chunk small and lets
    `redis-cli XRANGE` show sane field names. PCM is base64-encoded so
    XRANGE output stays printable — Redis 7 handles raw bytes fine but
    the cost is ~33% wire size, irrelevant at our chunk sizes (5-30 KB
    per sentence at 48 kHz int16).

    `final=True` carries no PCM; it signals end-of-stream so the gateway
    can DEL the stream and close the client connection. `error` set
    means this chunk is a terminal error — pcm is empty, gateway sends
    error frame and DELs.
    """

    request_id: str         # UUID string; matches TtsJobPayload.request_id
    seq: int                # 0-indexed chunk number
    pcm_bytes: bytes        # int16 PCM at engine.sample_rate (48 kHz)
    sentence_text: str | None = None  # None for final/error chunks
    final: bool = False
    error: str | None = None

    def encode(self) -> dict[str, str]:
        """Render to Redis-friendly field/value pairs. All values are
        strings since XADD canonical types are bytes/str — base64 for
        PCM, JSON-style booleans, UTF-8 for text."""
        out: dict[str, str] = {
            "request_id": self.request_id,
            "seq": str(self.seq),
            "pcm_b64": base64.b64encode(self.pcm_bytes).decode("ascii"),
            "final": "true" if self.final else "false",
        }
        if self.sentence_text is not None:
            out["sentence_text"] = self.sentence_text
        if self.error is not None:
            out["error"] = self.error
        return out

    @classmethod
    def decode(cls, fields: dict[bytes | str, bytes | str]) -> TtsResult:
        def _s(v: bytes | str) -> str:
            return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v

        def _get(key: str) -> str | None:
            # Redis returns either bytes or str keys depending on client config;
            # accept both at decode time so workers and gateway tests interop.
            if isinstance(next(iter(fields), None), (bytes, bytearray)):
                k = key.encode()
                return _s(fields[k]) if k in fields else None
            return _s(fields[key]) if key in fields else None

        request_id = _get("request_id") or ""
        seq = int(_get("seq") or "0")
        pcm_b64 = _get("pcm_b64") or ""
        pcm_bytes = base64.b64decode(pcm_b64) if pcm_b64 else b""
        final = (_get("final") or "false") == "true"
        sentence_text = _get("sentence_text")
        error = _get("error")
        return cls(
            request_id=request_id,
            seq=seq,
            pcm_bytes=pcm_bytes,
            sentence_text=sentence_text,
            final=final,
            error=error,
        )


@dataclass(frozen=True)
class TtsResultStreamConfig:
    """Tuning knobs for the per-request result stream — kept in one place
    so worker and gateway agree on TTL + maxlen without env-string parsing
    duplicated at each call site."""

    ttl_seconds: int = DEFAULT_RESULTS_TTL_SECONDS
    maxlen: int = 1024  # ~1024 sentences max per request; long-form caps here
    extra_fields: dict[str, str] = field(default_factory=dict)


class TtsJobQueue:
    """Thin Redis Streams producer + introspection API.

    Construction is explicit so the FastAPI app injects a single
    instance via get_queue() and tests override with fakeredis.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        stream: str = DEFAULT_STREAM,
        maxlen: int = DEFAULT_MAXLEN,
    ) -> None:
        self._redis = redis
        self._stream = stream
        self._maxlen = maxlen

    @property
    def stream_name(self) -> str:
        return self._stream

    async def submit(self, job: TtsJobPayload) -> str:
        """XADD a job onto the stream. Returns the Redis-assigned message
        id (`<ms>-<seq>`). The id is **not** the request_id we expose to
        the client — that one is the job UUID. We don't keep this id
        anywhere; workers learn it from XREADGROUP."""
        msg_id = await self._redis.xadd(
            self._stream,
            job.encode(),
            maxlen=self._maxlen,
            approximate=True,  # MAXLEN ~ — fast trim, off-by-one OK at scale
        )
        # redis-py returns bytes in default decode_responses=False mode.
        if isinstance(msg_id, (bytes, bytearray)):
            msg_id = msg_id.decode("utf-8")
        logger.info(
            "queue submit stream=%s request_id=%s tenant=%s voice=%s msg=%s",
            self._stream, job.request_id, job.tenant_id, job.voice_id, msg_id,
        )
        return msg_id

    async def depth(self) -> int:
        """XLEN — number of un-trimmed messages currently on the stream.
        Used for backpressure decisions (D-14 in scale-roadmap)."""
        return int(await self._redis.xlen(self._stream))


# --------------------------------------------------------------------------- #
# Process-wide accessor (FastAPI dep)
# --------------------------------------------------------------------------- #
_queue_singleton: TtsJobQueue | None = None


def get_queue() -> TtsJobQueue:
    """Lazily build the queue against the process-wide Redis client.

    Tests override via `app.dependency_overrides[get_queue]` with a
    fakeredis-backed queue (mirrors the get_redis pattern).
    """
    global _queue_singleton
    if _queue_singleton is None:
        from .auth import get_redis

        _queue_singleton = TtsJobQueue(get_redis())
    return _queue_singleton


def _reset_queue_singleton() -> None:
    """Test hook — clears the singleton so a new fakeredis takes effect."""
    global _queue_singleton
    _queue_singleton = None


def parse_idempotency_key(header_value: str | None) -> uuid.UUID:
    """Validate the Idempotency-Key header (Stripe convention).

    Reuses our DB schema: `job_idempotency.request_id` is a UUID, so we
    require the client to send a UUID-shaped string. UUIDv4 or UUIDv7
    both work; `uuid.UUID()` accepts hyphenated or bare-hex.
    """
    if not header_value:
        raise ValueError("Idempotency-Key header is required for async jobs")
    try:
        return uuid.UUID(header_value.strip())
    except ValueError as e:
        raise ValueError(
            "Idempotency-Key must be a UUID (canonical or bare-hex form)"
        ) from e
