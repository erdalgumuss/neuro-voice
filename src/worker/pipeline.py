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
from sqlalchemy import select

from db.models import UsageRecord
from db.session import AsyncSessionLocal
from repos import IdempotencyRepo, UsageRepo, VoiceRepo
from server.queue import (
    DEFAULT_RESULTS_TTL_SECONDS,
    TtsJobPayload,
    TtsResult,
    result_stream_name,
)

from .engine import BaseSynthEngine
from .live import iter_engine_chunks

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
    attempt: int = 0,
) -> None:
    await _xadd_result(
        redis,
        result_stream_name(rid),
        TtsResult(
            request_id=str(rid), seq=seq, pcm_bytes=pcm,
            sentence_text=sentence_text, final=False, attempt=attempt,
        ),
    )


async def publish_final(
    redis: Redis, rid: uuid.UUID, *, seq: int, attempt: int = 0,
) -> None:
    await _xadd_result(
        redis,
        result_stream_name(rid),
        TtsResult(
            request_id=str(rid), seq=seq, pcm_bytes=b"",
            sentence_text=None, final=True, attempt=attempt,
        ),
    )


async def publish_error(
    redis: Redis, rid: uuid.UUID, *, seq: int, message: str,
    attempt: int = 0,
) -> None:
    await _xadd_result(
        redis,
        result_stream_name(rid),
        TtsResult(
            request_id=str(rid), seq=seq, pcm_bytes=b"",
            sentence_text=None, final=False, error=message,
            attempt=attempt,
        ),
    )


def _queue_wait_ms(job: TtsJobPayload, started_wall_ms: int | None = None) -> int | None:
    if job.enqueued_at_ms is None:
        return None
    now_ms = started_wall_ms if started_wall_ms is not None else int(time.time() * 1000)
    return max(0, now_ms - int(job.enqueued_at_ms))


async def mark_terminal_failure(
    job: TtsJobPayload,
    *,
    redis: Redis,
    error_code: str,
    message: str | None = None,
    seq: int = 0,
    worker_id: str | None = None,
    elapsed_ms: int = 0,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    attempt: int = 0,
) -> None:
    """Publish the terminal error surface and persist failed state.

    Used by poison paths in the pipeline and by the consumer once a
    transient/unknown failure exceeds the retry budget. The usage row is
    idempotent by request_id: if an earlier terminal path already wrote
    it, we only keep the idempotency row failed.
    """
    rid = uuid.UUID(job.request_id)
    tenant_id = uuid.UUID(job.tenant_id)
    api_key_id = uuid.UUID(job.api_key_id)

    await publish_error(
        redis, rid, seq=seq, message=message or error_code, attempt=attempt,
    )
    async with session_factory() as s:
        await IdempotencyRepo(s, tenant_id).fail(rid)
        existing_usage = (await s.execute(
            select(UsageRecord).where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.request_id == rid,
            )
        )).scalar_one_or_none()
        if existing_usage is None:
            await UsageRepo(s, tenant_id).record(
                api_key_id=api_key_id,
                voice_id=job.voice_id,
                request_id=rid,
                text_char_count=len(job.text),
                sentence_count=0,
                duration_ms=0,
                elapsed_ms=max(0, elapsed_ms),
                queue_wait_ms=_queue_wait_ms(job),
                inference_ms=None,
                rtf=None,
                status="error",
                error_code=error_code,
                worker_id=worker_id,
                app_label=job.app_label,
            )
        await s.commit()


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
    worker_id: str | None = None,
    worker_pickup_ms: int | None = None,
    attempt: int = 0,
) -> None:
    """Process a single TTS job end-to-end.

    Raises:
      PoisonJob       — caller should XACK (no point retrying)
      TransientFailure — caller should NOT XACK (let XAUTOCLAIM retry)

    `worker_pickup_ms` is the consumer-measured latency from
    `payload.enqueued_at_ms` to the moment the worker began handling
    this job. Threaded down so the usage row carries the same
    millisecond stamp the consumer observed (the pipeline itself never
    sees the XREADGROUP timestamp).
    """
    if resolve_reference is None:
        # Lazy import — keeps `worker.pipeline` import-light for tests
        # that supply their own resolver.
        from storage.reference_resolver import resolve_reference_uri
        resolve_reference = resolve_reference_uri

    rid = uuid.UUID(job.request_id)
    tenant_id = uuid.UUID(job.tenant_id)
    api_key_id = uuid.UUID(job.api_key_id)
    started_wall_ms = int(time.time() * 1000)
    started = time.monotonic()

    # Retry safety: if a previous attempt emitted partial chunks and
    # then failed before final, the next attempt owns a clean per-request
    # stream. Gateway also dedupes by seq, but deleting here removes the
    # most confusing client-visible failure mode at the source.
    await redis.delete(result_stream_name(rid))

    # ---------- 1. resolve voice (DB, viewer = job tenant) ---------------
    async with session_factory() as s:
        voice_row = await VoiceRepo(s, tenant_id).get_accessible(job.voice_id)
    if voice_row is None:
        await mark_terminal_failure(
            job,
            redis=redis,
            error_code="voice_not_found",
            message="voice_not_found",
            seq=0,
            worker_id=worker_id,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            session_factory=session_factory,
            attempt=attempt,
        )
        raise PoisonJob(f"voice {job.voice_id!r} not visible to tenant")
    voice_view = voice_view_from_db(voice_row)
    reference_uri = voice_row.reference_uri

    # ---------- 2. resolve reference audio (R2 download in a thread) -----
    # Faz C step 1: time the reference-resolve hop so we can attribute
    # latency spikes to R2 cold reads vs engine vs archive separately.
    ref_resolve_started = time.monotonic()
    reference_resolve_ms: int | None = None
    try:
        ref_path = await asyncio.to_thread(resolve_reference, reference_uri)
        reference_resolve_ms = int(
            (time.monotonic() - ref_resolve_started) * 1000,
        )
    except FileNotFoundError as e:
        await mark_terminal_failure(
            job,
            redis=redis,
            error_code="reference_missing",
            message=f"reference_missing: {e}",
            seq=0,
            worker_id=worker_id,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            session_factory=session_factory,
            attempt=attempt,
        )
        raise PoisonJob(f"reference for {job.voice_id!r} missing") from e

    # ---------- 3-4. generate + publish chunks (NO final yet) -----------
    # Sentence-level streaming bridge: the engine sync generator runs
    # on a thread, each yielded SynthChunk lands on an asyncio queue,
    # and we publish_chunk immediately. First-byte latency drops from
    # `total_inference_ms` to `first_sentence_inference_ms`. The seq
    # numbering stays "one per sentence" so result-stream consumers
    # (sync proxy, /v1/tts/stream, future WebSocket) keep their
    # dedup-by-seq invariant. See `worker.live.iter_engine_chunks`.
    seq = 0
    pcm_buffer = bytearray()
    sample_rate = engine.sample_rate
    inference_started = time.monotonic()
    # `first_pcm_ms`  = when the engine yielded its first SynthChunk
    #                   (engine-local TTFB)
    # `first_audio_ms` = when publish_chunk(...) returned for that first
    #                   chunk (what the gateway can observe). Difference
    #                   between the two = bridge / publish overhead.
    first_pcm_ms: int | None = None
    first_audio_ms: int | None = None

    # Faz B.5 Dalga 1.2 — resolve model_id preset to engine_overrides.
    # Unknown model_ids surface as PoisonJob (no point retrying — the
    # client sent garbage and a retry would garbage-in/garbage-out).
    # Explicit `params` (cfg_value, inference_timesteps) override the
    # preset when both are present — request-level wins per-key.
    try:
        from server.models import UnknownModelError, resolve_model
        preset = resolve_model(job.model_id)
    except UnknownModelError as e:
        await mark_terminal_failure(
            job,
            redis=redis,
            error_code="unknown_model_id",
            message=str(e),
            worker_id=worker_id,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            session_factory=session_factory,
            attempt=attempt,
        )
        raise PoisonJob(str(e)) from e

    engine_overrides: dict[str, float | int] = {
        "cfg_value": preset.cfg_value,
        "inference_timesteps": preset.inference_timesteps,
    }

    # Faz B.5 Dalga 2.1 — voice_settings → engine knob adjustments.
    # Stability and similarity_boost are vendor-named neutral-0.5 knobs
    # we map onto our two real engine params; the mapping is bounded
    # and clamped to the engine's safe ranges (TTSJobParams limits).
    voice_settings = job.voice_settings or {}
    if voice_settings.get("similarity_boost") is not None:
        # -0.3 at 0.0, +0.5 at 1.0 around the preset baseline.
        engine_overrides["cfg_value"] = float(
            engine_overrides["cfg_value"]
            + (voice_settings["similarity_boost"] - 0.5) * 1.0
        )
    if voice_settings.get("stability") is not None:
        # -4 at 0.0, +8 at 1.0 around the preset baseline.
        engine_overrides["inference_timesteps"] = int(
            engine_overrides["inference_timesteps"]
            + (voice_settings["stability"] - 0.5) * 16
        )
    # Clamp to engine-safe envelopes (matches TTSJobParams Field bounds).
    engine_overrides["cfg_value"] = max(
        1.0, min(3.5, float(engine_overrides["cfg_value"])),
    )
    engine_overrides["inference_timesteps"] = max(
        4, min(40, int(engine_overrides["inference_timesteps"])),
    )

    if job.params:
        # Explicit request-level params win — pre-validated by pydantic
        # via TTSJobParams (cfg_value in [1.0, 3.5], steps in [4, 40]).
        for k in ("cfg_value", "inference_timesteps"):
            v = job.params.get(k)
            if v is not None:
                engine_overrides[k] = v

    # Faz B.5 Dalga 2.1 — speed post-process. We resample per-chunk
    # rather than at the end because /v1/tts/stream needs to forward
    # ALREADY-sped audio to the client in real time; concatenating
    # and resampling at the end would defeat streaming. Linear interp
    # is voice-grade for the 0.7–1.2x range the schema bounds.
    from audio.postprocess import apply_voice_settings as _apply_vs

    try:
        async for chunk in iter_engine_chunks(
            engine,
            text=job.text,
            voice=voice_view,
            reference_path=ref_path,
            language_id=job.language,
            engine_overrides=engine_overrides,
        ):
            if first_pcm_ms is None:
                first_pcm_ms = int(
                    (time.monotonic() - inference_started) * 1000,
                )
            # Apply PCM-side voice_settings (currently `speed` only).
            # Zero-cost fast path when voice_settings is None / empty.
            pcm_out = _apply_vs(
                chunk.pcm_int16,
                sample_rate=chunk.sample_rate,
                voice_settings=voice_settings,
            )
            await publish_chunk(
                redis, rid,
                seq=seq,
                pcm=pcm_out,
                sentence_text=getattr(chunk, "sentence_text", None),
                attempt=attempt,
            )
            if first_audio_ms is None:
                first_audio_ms = int(
                    (time.monotonic() - inference_started) * 1000,
                )
            pcm_buffer.extend(pcm_out)
            seq += 1
    except Exception as e:
        # TRANSIENT — DO NOT publish error chunk or fail the idempotency
        # row. The consumer will skip XACK; XAUTOCLAIM hands the same
        # job to another worker after `idle_ms`. If the failure is
        # actually deterministic, the DLQ path catches it after N
        # retries and only THEN converts to a terminal failure visible
        # to the client.
        logger.exception("engine streaming failed for %s (transient)", rid)
        raise TransientFailure(str(e)) from e
    inference_ms = int((time.monotonic() - inference_started) * 1000)

    # ---------- 5. archive — REQUIRED for client GET to see audio_url ---
    if archive_to_r2 is None:
        raise TransientFailure(
            "archive_to_r2 callable not configured; refusing to mark "
            "complete without an artifact (would leave audio_url=null)"
        )
    if not pcm_buffer:
        await mark_terminal_failure(
            job,
            redis=redis,
            error_code="empty_pcm",
            message="empty_pcm: engine produced no PCM",
            seq=seq,
            worker_id=worker_id,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            session_factory=session_factory,
            attempt=attempt,
        )
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
                queue_wait_ms=_queue_wait_ms(job, started_wall_ms),
                inference_ms=inference_ms,
                worker_pickup_ms=worker_pickup_ms,
                reference_resolve_ms=reference_resolve_ms,
                first_pcm_ms=first_pcm_ms,
                first_audio_ms=first_audio_ms,
                rtf=rtf,
                status="ok",
                worker_id=worker_id,
                # Faz B.5 Dalga 2.3 — model_id (preset slug) persisted so
                # the status response can return which preset ran. Maps
                # to UsageRecord.model_version. preset is the resolved
                # ModelPreset from earlier in this function.
                model_version=preset.model_id,
                app_label=job.app_label,
            )
            await s.commit()
    except Exception as e:
        # Transient — same retry path. The archive call may have left
        # an orphan object in R2 (TODO: garbage-collect by request_id
        # in Faz C), but client correctness is preserved.
        logger.exception("idempotency.complete + usage.record commit failed for %s", rid)
        raise TransientFailure(f"db_commit_failed: {e}") from e

    # ---------- 6b. Prometheus waterfall histograms (Faz C step 2) -------
    # Observe each populated waterfall stage. None values are skipped
    # silently by record_waterfall so retries / partial failures don't
    # pollute the histograms with phantom zeros.
    try:
        from observability import TTS_REQUESTS, record_waterfall

        record_waterfall(
            tenant=str(tenant_id),
            voice=voice_view.voice_id,
            queue_wait_ms=_queue_wait_ms(job, started_wall_ms),
            worker_pickup_ms=worker_pickup_ms,
            reference_resolve_ms=reference_resolve_ms,
            first_pcm_ms=first_pcm_ms,
            first_audio_ms=first_audio_ms,
            inference_ms=inference_ms,
            total_ms=elapsed_ms,
        )
        TTS_REQUESTS.labels(
            tenant=str(tenant_id),
            voice=voice_view.voice_id,
            status="success",
        ).inc()
    except Exception:
        # Metrics MUST NOT break the request path. Observability is
        # best-effort; a misbehaving Prometheus client gets swallowed.
        logger.exception("waterfall metric observation failed for %s", rid)

    # ---------- 7. publish final marker (ONLY after commit succeeded) ---
    # Gateway invariant: seeing final=True ⇒ GET /v1/tts/jobs/{id}
    # WILL return complete + response_uri. If this XADD itself raises,
    # the consumer treats it as transient — retry re-emits all chunks
    # (gateway's result-stream consumer dedups via per-request stream
    # name = the request_id) and a fresh final.
    try:
        await publish_final(redis, rid, seq=seq, attempt=attempt)
    except Exception as e:
        logger.exception("publish_final XADD failed for %s post-commit", rid)
        raise TransientFailure(f"publish_final_failed: {e}") from e
