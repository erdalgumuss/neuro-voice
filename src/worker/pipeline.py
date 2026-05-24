"""One-job pipeline — the body of `worker.consumer.run_consumer`.

Lives in its own module so tests can call `process_one_job(...)`
directly without spinning up XREADGROUP — the consumer (consumer.py)
is a tiny shim that pulls jobs and hands them here.

Pipeline contract (commit-before-final ordering):

    1.  resolve voice (DB, viewer = job.tenant_id)
    2.  resolve reference audio URI → local Path (R2 download in a
        thread so we don't block the event loop)
    3.  engine.synthesize_stream(...) — sync generator, run in thread,
        yields one SynthChunk per Türkçe sentence
    4.  publish each chunk to nqai.tts.results.{rid} as a TtsResult XADD
        (NO final marker yet)
    5.  archive concatenated PCM to artifact storage (R2 or local)
        → `response_uri` (REQUIRED for client GET — no audio_url
          dangling-null state)
    6.  ONE DB transaction: IdempotencyRepo.complete(response_uri)
        + UsageRepo.record(...) — billing + idempotency atomic
    7.  ONLY AFTER commit succeeds: publish_final(final=True) — gateway
        sees "done" only when the artifact + idempotency row are durable

Failure semantics (D-06 at-least-once + D-05 idempotency):

    Terminal errors (publish error chunk + idem.fail + raise PoisonJob;
    consumer XACKs to drain):
      *   voice missing
      *   reference missing (artifact never existed and won't reappear)

    Transient errors (NO error chunk, NO idem.fail; raise
    TransientFailure; consumer does NOT XACK — XAUTOCLAIM retries on
    another worker, or DLQ after N retries in Faz C):
      *   engine.synthesize_stream raised
      *   archive_to_r2 raised (network blip, R2 throttled — retry
            re-generates + re-archives; same idempotency row, same
            request_id, no double-billing because UsageRepo.record
            unique-constrains on request_id)
      *   final DB commit raised
      *   final XADD raised after commit (rare; commit already done,
            retry re-emits all chunks + a fresh final — gateway DEL's
            the previous stream so the dedup is the request_id itself)

    These rules + the commit-before-final ordering give the gateway a
    simple invariant: `final=True` chunk ⇒ status endpoint will return
    `complete` with a `response_uri`. No dangling state.
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
    """Sync — typically `storage.reference_resolver.resolve_reference_uri`.
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
        from storage.reference_resolver import resolve_reference_uri
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

    # ---------- 3-4. generate + publish chunks (NO final yet) -----------
    # TODO(faz-b1.5): real streaming bridge.
    #   We currently `list(engine.synthesize_stream(...))` inside a
    #   thread, which serialises generation and only emits chunks AFTER
    #   the full inference finishes — so the gateway sees them in one
    #   burst rather than as they're produced. Once WS / sync proxy
    #   need true low-TTFB, replace `_drain_engine` with a queue.Queue
    #   bridge: producer thread runs the sync generator and pushes
    #   chunks into the queue; this coroutine awaits queue.get_nowait
    #   in a loop and XADDs as each chunk lands. Tracked by
    #   `test_pipeline_chunks_currently_drained_before_publish` below.
    started = time.monotonic()
    seq = 0
    pcm_buffer = bytearray()
    sample_rate = engine.sample_rate

    def _drain_engine() -> list[Any]:
        """Sync generator wrapper — engine is GIL+GPU-bound, push to a
        thread so the event loop stays responsive. See TODO above."""
        return list(engine.synthesize_stream(
            text=job.text, voice=voice_view, reference_path=ref_path,
            language_id=job.language,
        ))

    try:
        chunks = await asyncio.to_thread(_drain_engine)
    except Exception as e:
        # TRANSIENT — DO NOT publish error chunk or fail the idempotency
        # row. The consumer will skip XACK; XAUTOCLAIM hands the same
        # job to another worker after `idle_ms`. If the failure is
        # actually deterministic, Faz C's DLQ catches it after N retries
        # and only THEN converts to a terminal failure visible to client.
        logger.exception("engine.synthesize_stream failed for %s (transient)", rid)
        raise TransientFailure(str(e)) from e

    for chunk in chunks:
        await publish_chunk(
            redis, rid,
            seq=seq, pcm=chunk.pcm_int16,
            sentence_text=chunk.sentence_text,
        )
        pcm_buffer.extend(chunk.pcm_int16)
        seq += 1

    # ---------- 5. archive — REQUIRED for client GET to see audio_url ---
    if archive_to_r2 is None:
        raise TransientFailure(
            "archive_to_r2 callable not configured; refusing to mark "
            "complete without an artifact (would leave audio_url=null)"
        )
    if not pcm_buffer:
        raise PoisonJob(
            "engine produced no PCM — nothing to archive or stream"
        )
    try:
        response_uri = await archive_to_r2(rid, bytes(pcm_buffer), sample_rate)
    except Exception as e:
        # Transient — most R2/network failures are intermittent. Retry
        # re-generates audio (worker is stateless on artifacts) and
        # tries archive again. Idempotency row stays 'processing',
        # client polls keep returning queued until the retry succeeds.
        logger.warning(
            "archive_to_r2 failed for %s (transient retry): %s", rid, e,
        )
        raise TransientFailure(f"archive_failed: {e}") from e
    if not response_uri:
        # Callable returned None — same problem as raise: no artifact.
        raise TransientFailure(
            "archive_to_r2 returned no URI; refusing to mark complete"
        )

    # ---------- 6. idempotency complete + usage record (one TX) ---------
    elapsed_ms = int((time.monotonic() - started) * 1000)
    # int16 mono PCM → samples = bytes/2; duration = samples / sample_rate
    duration_samples = len(pcm_buffer) // 2
    duration_ms = int(duration_samples / max(sample_rate, 1) * 1000)
    rtf = (elapsed_ms / duration_ms) if duration_ms > 0 else None

    try:
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
    except Exception as e:
        # Transient — same retry path. The archive call may have left
        # an orphan object in R2 (TODO: garbage-collect by request_id
        # in Faz C), but client correctness is preserved.
        logger.exception("idempotency.complete + usage.record commit failed for %s", rid)
        raise TransientFailure(f"db_commit_failed: {e}") from e

    # ---------- 7. publish final marker (ONLY after commit succeeded) ---
    # Gateway invariant: seeing final=True ⇒ GET /v1/tts/jobs/{id}
    # WILL return complete + response_uri. If this XADD itself raises,
    # the consumer treats it as transient — retry re-emits all chunks
    # (gateway's result-stream consumer dedups via per-request stream
    # name = the request_id) and a fresh final.
    try:
        await publish_final(redis, rid, seq=seq)
    except Exception as e:
        logger.exception("publish_final XADD failed for %s post-commit", rid)
        raise TransientFailure(f"publish_final_failed: {e}") from e
