"""TTS job queue — Redis Streams wrapper.

The gateway enqueues TtsJobPayload messages on a Redis Stream; GPU
workers (Faz B+, src/worker/) consume via XREADGROUP and ack with XACK.
This module is the gateway-side surface — no consumer logic lives here.

Stream layout:
    nqai.tts.jobs               primary job queue
    nqai.tts.jobs.dlq           Faz B+: poisoned messages after N retries

Each XADD entry carries a JSON-encoded TtsJobPayload. Workers parse and
write back via IdempotencyRepo + R2 storage; the gateway polls
IdempotencyRepo for status (DB is the source of truth, the stream is
just the work distribution).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger("nqai_voice.queue")

DEFAULT_STREAM = "nqai.tts.jobs"
DEFAULT_MAXLEN = 10_000  # XADD MAXLEN ~ trims the stream to this approx size


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
