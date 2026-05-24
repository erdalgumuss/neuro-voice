"""One-job pipeline — the body of `worker.consumer.run_consumer`.

Lives in its own module so tests can call `process_one_job(...)`
directly without spinning up XREADGROUP — the consumer (consumer.py)
is a tiny shim that pulls jobs and hands them here.

Pipeline contract:

    1.  resolve voice (DB, viewer = job.tenant_id)
    2.  resolve reference audio URI → local Path (R2 download in a
        thread so we don't block the event loop)
    3.  engine.synthesize_stream(...) — sync generator, run in thread,
        yields one SynthChunk per Türkçe sentence
    4.  publish each chunk to nqai.tts.results.{rid} as a TtsResult XADD
    5.  publish a `final=True` marker chunk so the gateway knows to stop
    6.  optionally archive concatenated PCM to R2 + presigned URL later
    7.  one DB transaction: IdempotencyRepo.complete(response_uri) +
        UsageRepo.record(...) — keeps billing + idempotency atomic

Failure semantics (D-06 at-least-once + D-05 idempotency):

    *   voice missing       → result-stream `error` chunk + idem.fail()
                                + return cleanly (XACK ok — no retry,
                                  the voice won't appear later)
    *   engine raises        → result-stream `error` chunk + idem.fail()
                                + RE-RAISE so the consumer skips XACK and
                                  XAUTOCLAIM hands the job to another
                                  worker (or DLQ after N retries — Faz C)
    *   R2 archive fails    → still mark complete; client gets a PCM
                                response via result stream chunks even
                                without a snapshot URL
    *   DB commit at end    → if the final commit raises, RE-RAISE
                                (XACK skipped, retry will re-emit chunks;
                                gateway dedupes via request_id)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from redis.asyncio import Redis

from db.session import AsyncSessionLocal
from repos import IdempotencyRepo, UsageRepo, VoiceRepo
from server.queue import (
    DEFAULT_RESULTS_TTL_SECONDS,
    TtsJobPayload,
    TtsResult,
    result_stream_name,
)

from .engine import BaseSynthEngine

logger = logging.getLogger("nqai_voice.worker.pipeline")


# --------------------------------------------------------------------------- #
# VoiceView — engine-facing duck type
# --------------------------------------------------------------------------- #
# Engine wants three attributes (`voice_id`, `engine_params`, `adapter`).
# Pipeline owns this adapter shape so gateway and worker don't have to
# share a Voice contract. Gateway sync /v1/tts imports VoiceView from
# here during the transition (one-way server→worker, allowed; gets
# removed in step 6 when sync becomes a queue proxy).
@dataclass(frozen=True)
class VoiceView:
    voice_id: str
    engine_params: dict[str, Any] = field(default_factory=dict)
    adapter: dict[str, Any] | None = None


def voice_view_from_db(v) -> VoiceView:
    """Project a `db.models.Voice` into the engine-facing duck type.

    Pulled out of `server.main` so the worker pipeline can build the
    same view without depending on the gateway."""
    adapter: dict[str, Any] | None = None
    if v.adapter_uri:
        adapter = {
            "type": v.adapter_type or "lora",
            "path": v.adapter_uri,
        }
    return VoiceView(
        voice_id=v.voice_id,
        engine_params=v.engine_params or {},
        adapter=adapter,
    )


# --------------------------------------------------------------------------- #
# Optional callable types — kept narrow so tests can inject fakes
# --------------------------------------------------------------------------- #
class ReferenceResolver(Protocol):
    """Sync — typically `server.reference_resolver.resolve_reference_uri`.
    Wrapped in `asyncio.to_thread` by the pipeline so I/O doesn't block."""

    def __call__(self, uri: str) -> Path: ...


# (rid, pcm_buf_bytes, sample_rate) → uri string (e.g. s3://bucket/key)
ArchiveCallable = Callable[[uuid.UUID, bytes, int], Awaitable[str | None]]


# --------------------------------------------------------------------------- #
# Result-stream publishers — single source of truth for XADD wire format
# --------------------------------------------------------------------------- #
async def _xadd_result(
    redis: Redis, stream: str, chunk: TtsResult,
    *, ttl_seconds: int = DEFAULT_RESULTS_TTL_SECONDS,
) -> None:
    """XADD the encoded TtsResult and refresh the TTL safety net.

    EXPIRE on every chunk is cheap (Redis O(1)) and keeps the stream
    alive throughout the request even if the worker takes longer than
    the initial TTL window. Gateway DEL's the stream after final."""
    await redis.xadd(stream, chunk.encode())
    await redis.expire(stream, ttl_seconds)


async def publish_chunk(
    redis: Redis, rid: uuid.UUID, *,
    seq: int, pcm: bytes, sentence_text: str | None,
) -> None:
    await _xadd_result(
        redis,
        result_stream_name(rid),
        TtsResult(
            request_id=str(rid), seq=seq, pcm_bytes=pcm,
            sentence_text=sentence_text, final=False,
        ),
    )


async def publish_final(redis: Redis, rid: uuid.UUID, *, seq: int) -> None:
    await _xadd_result(
        redis,
        result_stream_name(rid),
        TtsResult(
            request_id=str(rid), seq=seq, pcm_bytes=b"",
            sentence_text=None, final=True,
        ),
    )


async def publish_error(
    redis: Redis, rid: uuid.UUID, *, seq: int, message: str,
) -> None:
    await _xadd_result(
        redis,
        result_stream_name(rid),
        TtsResult(
            request_id=str(rid), seq=seq, pcm_bytes=b"",
            sentence_text=None, final=False, error=message,
        ),
    )


# --------------------------------------------------------------------------- #
# The pipeline itself
# --------------------------------------------------------------------------- #
class WorkerError(Exception):
    """Raised so the consumer can decide whether to XACK or let
    XAUTOCLAIM retry. Subclasses signal recoverable vs poison."""


class PoisonJob(WorkerError):
    """Job is structurally broken (unknown voice, malformed payload).
    Consumer SHOULD XACK so XAUTOCLAIM doesn't retry forever."""


class TransientFailure(WorkerError):
    """Inference or DB blew up; XAUTOCLAIM should retry on another
    worker. Consumer MUST NOT XACK."""


async def process_one_job(
    job: TtsJobPayload,
    *,
    redis: Redis,
    engine: BaseSynthEngine,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    resolve_reference: ReferenceResolver | None = None,
    archive_to_r2: ArchiveCallable | None = None,
) -> None:
    """Process a single TTS job end-to-end.

    Raises:
      PoisonJob       — caller should XACK (no point retrying)
      TransientFailure — caller should NOT XACK (let XAUTOCLAIM retry)
    """
    if resolve_reference is None:
        # Lazy import — keeps `worker.pipeline` import-light for tests
        # that supply their own resolver.
        from server.reference_resolver import resolve_reference_uri
        resolve_reference = resolve_reference_uri

    rid = uuid.UUID(job.request_id)
    tenant_id = uuid.UUID(job.tenant_id)
    api_key_id = uuid.UUID(job.api_key_id)

    # ---------- 1. resolve voice (DB, viewer = job tenant) ---------------
    async with session_factory() as s:
        voice_row = await VoiceRepo(s, tenant_id).get_accessible(job.voice_id)
        if voice_row is None:
            await publish_error(redis, rid, seq=0, message="voice_not_found")
            await IdempotencyRepo(s, tenant_id).fail(rid)
            await s.commit()
            raise PoisonJob(f"voice {job.voice_id!r} not visible to tenant")
        voice_view = voice_view_from_db(voice_row)
        reference_uri = voice_row.reference_uri

    # ---------- 2. resolve reference audio (R2 download in a thread) -----
    try:
        ref_path = await asyncio.to_thread(resolve_reference, reference_uri)
    except FileNotFoundError as e:
        await publish_error(redis, rid, seq=0, message=f"reference_missing: {e}")
        async with session_factory() as s:
            await IdempotencyRepo(s, tenant_id).fail(rid)
            await s.commit()
        raise PoisonJob(f"reference for {job.voice_id!r} missing") from e

    # ---------- 3-5. generate + publish chunks ---------------------------
    started = time.monotonic()
    seq = 0
    pcm_buffer = bytearray()
    sample_rate = engine.sample_rate

    def _drain_engine() -> list[Any]:
        """Sync generator wrapper — engine is GIL-bound + GPU-bound, we
        push it onto a thread so the event loop stays responsive."""
        return list(engine.synthesize_stream(
            text=job.text, voice=voice_view, reference_path=ref_path,
            language_id=job.language,
        ))

    try:
        chunks = await asyncio.to_thread(_drain_engine)
    except Exception as e:
        logger.exception("engine.synthesize_stream failed for %s", rid)
        await publish_error(redis, rid, seq=0, message=f"inference_error: {e}")
        async with session_factory() as s:
            await IdempotencyRepo(s, tenant_id).fail(rid)
            await s.commit()
        raise TransientFailure(str(e)) from e

    for chunk in chunks:
        await publish_chunk(
            redis, rid,
            seq=seq, pcm=chunk.pcm_int16,
            sentence_text=chunk.sentence_text,
        )
        pcm_buffer.extend(chunk.pcm_int16)
        seq += 1

    await publish_final(redis, rid, seq=seq)

    # ---------- 6. optional R2 archive ----------------------------------
    response_uri: str | None = None
    if archive_to_r2 is not None and pcm_buffer:
        try:
            response_uri = await archive_to_r2(rid, bytes(pcm_buffer), sample_rate)
        except Exception:
            # Archive is best-effort. Chunks already shipped — log and
            # mark complete without a snapshot URL. Status endpoint will
            # return raw PCM via result stream (or no audio_url field).
            logger.warning("R2 archive failed for %s — completing without URI", rid)

    # ---------- 7. idempotency complete + usage record (one TX) ---------
    elapsed_ms = int((time.monotonic() - started) * 1000)
    # int16 mono PCM → samples = bytes/2; duration = samples / sample_rate
    duration_samples = len(pcm_buffer) // 2
    duration_ms = int(duration_samples / max(sample_rate, 1) * 1000)
    rtf = (elapsed_ms / duration_ms) if duration_ms > 0 else None

    async with session_factory() as s:
        await IdempotencyRepo(s, tenant_id).complete(
            rid, response_uri=response_uri,
        )
        await UsageRepo(s, tenant_id).record(
            api_key_id=api_key_id,
            voice_id=voice_view.voice_id,
            request_id=rid,
            text_char_count=len(job.text),
            sentence_count=seq,
            duration_ms=duration_ms,
            elapsed_ms=elapsed_ms,
            rtf=rtf,
            status="ok",
            app_label=job.app_label,
        )
        await s.commit()
