"""NQAI Voice TTS — FastAPI application.

Run with:
    uvicorn server.main:app --host 0.0.0.0 --port 8000

Auth surface:
    * Bearer API key (DB-backed argon2id) on /v1/* and /admin/warmup
    * JWT cookie (operator) on /admin/*
    * /health is unauthenticated (k8s liveness)

Faz A.6 cutover (this revision): TTS endpoints switched off the legacy
env-list auth and the filesystem voice catalog onto the DB-backed
auth pipeline + VoiceRepo. The legacy registry module still ships for
the migration script but is no longer the source of truth at request
time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import time
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from audio.wav import pcm16_to_wav_bytes
from db.session import get_session
from observability import (
    QUEUE_DEPTH,
    TTS_REQUESTS,
    WORKER_CAPACITY,
    WORKER_COUNT,
    WORKER_INFLIGHT,
    render_metrics,
)
from registry.audio_io import trim_and_resample_to_wav  # still used by enroll
from registry.catalog import (
    ALLOWED_AUDIO_SUFFIXES,
    InvalidVoiceId,
    validate_voice_id,
)
from repos import (
    AuditRepo,
    IdempotencyConflict,
    IdempotencyRepo,
    UsageRepo,
    VoiceRepo,
)

from .admin import admin_router
from .auth import AuthContext, require_auth
from .config import settings
from .heartbeat import read_cluster_capacity
from .queue import TtsJobPayload, TtsJobQueue, get_queue, parse_idempotency_key
from .result_stream import (
    ResultStreamTimeout,
    collect_pcm_until_final,
    consume_result_stream,
)
from .schemas import (
    DeleteResponse,
    EnrollResponse,
    ErrorResponse,
    HealthResponse,
    TTSJobAccepted,
    TTSJobCreate,
    TTSJobMetrics,
    TTSJobOutput,
    TTSJobStatusResponse,
    TTSRequest,
    TTSStreamRequest,
    VoiceListResponse,
    VoicePublic,
)

logger = logging.getLogger("nqai_voice.server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

VERSION = "0.4.0"

# Faz B.1 step 3 cutover: the gateway no longer holds a VoxCPM2 engine.
# Sync /v1/tts and /v1/tts/stream proxy through the same Redis queue
# the async /v1/tts/jobs path uses. Engine + sentence streaming live
# exclusively in `src/worker/`; the gateway is pure I/O + auth + DB.

# Sunset date for the sync endpoints (RFC 8594). When this passes, the
# Deprecation header becomes a hard 410 in a follow-up release.
SYNC_TTS_SUNSET = "Mon, 01 Sep 2026 00:00:00 GMT"
_SYNC_DEPRECATION_HEADERS = {
    "Deprecation": "true",
    "Sunset": SYNC_TTS_SUNSET,
    "Link": '</v1/tts/jobs>; rel="successor-version"',
}


async def _assert_voice_accessible_or_404(
    voice_id: str, tenant_id: uuid.UUID, session: AsyncSession,
):
    """Viewer-scoped accessibility check (refactor R, D-08).

    Returns the db row so callers can read voice_id slug, owner, etc.
    Crucially does NOT resolve the reference audio URI — that's a
    worker-side concern. Gateway-side resolution would (a) trigger an
    R2 download on every metadata/sync request (Codex audit fix) and
    (b) re-introduce a server→storage dependency on a hot path.

    Raises HTTPException(404/400). Existence-leak prevention: a voice
    belonging to another tenant returns the same 404 as a missing one.
    """
    try:
        validate_voice_id(voice_id)
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    voice = await VoiceRepo(session, tenant_id).get_accessible(voice_id)
    if voice is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"voice '{voice_id}' not found"
        )
    return voice


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "nqai-voice gateway %s starting (model=%s device=%s)",
        VERSION,
        settings.model_id,
        settings.device,
    )
    logger.info(
        "reference_dir=%s tenant_rate_limit/min=%d",
        settings.reference_audio_dir,
        settings.tenant_rate_limit_per_minute,
    )
    yield
    # Faz C SIGTERM graceful drain. uvicorn already stops accepting new
    # connections before invoking lifespan shutdown and waits for the
    # in-flight request handlers itself. This extra delay just gives
    # background tasks (audit writes, result-stream consumers) a chance
    # to flush before the loop tears down.
    #
    # Default 0 (opt-in): production deployments set
    # `NQAI_GATEWAY_DRAIN_TIMEOUT_S=10`. CI/tests leave it unset so
    # TestClient teardown stays fast and deterministic.
    drain_s = float(os.environ.get("NQAI_GATEWAY_DRAIN_TIMEOUT_S", "0"))
    if drain_s > 0:
        logger.info("gateway draining (timeout=%.1fs)", drain_s)
        try:
            await asyncio.sleep(min(drain_s, 30.0))
        except asyncio.CancelledError:
            # Hard-kill (SIGKILL or second SIGTERM) — exit immediately.
            logger.warning("gateway drain cancelled — exiting")
            raise
    logger.info("nqai-voice gateway shutting down")


app = FastAPI(
    title="NQAI Voice — Türkçe TTS Platform",
    description=(
        "Türkçe + voice-cloning + streaming TTS API on VoxCPM2 (Apache 2.0). "
        "Catalog-based voices (`/v1/voices`), HTTP synthesis (`/v1/tts`), and "
        "sentence-chunked streaming (`/v1/tts/stream`). "
        "Admin surface (DB-backed JWT) lives under `/admin`."
    ),
    version=VERSION,
    lifespan=lifespan,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "X-NQAI-Sample-Rate",
        "X-NQAI-Voice-Id",
        "X-NQAI-Sentences",
        "X-NQAI-Duration-Seconds",
        "X-NQAI-Elapsed-Seconds",
        "X-NQAI-RTF",
        "X-NQAI-Request-Id",
    ],
)

# Admin (JWT-protected, DB-backed) lives under /admin
app.include_router(admin_router)


# --------------------------------------------------------------------------- #
# /health — unauthenticated liveness (gateway only, no engine state)
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    """Gateway liveness — DB / Redis health is not checked here so the
    probe stays cheap. Worker engine state lives behind metrics in
    Faz C; gateway never knows whether a GPU worker is warmed up.

    `loaded` / `sample_rate` are advisory legacy fields kept for the
    admin UI's existing rendering; they're filled with static settings
    values, not a live engine probe."""
    return HealthResponse(
        status="ok",
        model_id=settings.model_id,
        device="gateway",  # gateway never holds the model after Faz B.1
        sample_rate=settings.target_sample_rate,
        loaded=True,  # gateway is always "loaded" — engine lives in workers
        voice_count=0,
        version=VERSION,
    )


# --------------------------------------------------------------------------- #
# /metrics — Prometheus exposition (Faz C step 2)
# --------------------------------------------------------------------------- #
@app.get("/metrics", tags=["meta"], include_in_schema=False)
async def metrics(
    queue: Annotated[TtsJobQueue, Depends(get_queue)],
) -> Response:
    """Prometheus scrape endpoint. Refreshes cluster gauges (worker count,
    capacity, in-flight, queue depth) from Redis on every scrape so a
    Prometheus pull always sees fresh values without a background task."""
    try:
        cap = await read_cluster_capacity(queue.redis)
        WORKER_COUNT.set(cap.worker_count)
        WORKER_CAPACITY.set(cap.total_capacity)
        WORKER_INFLIGHT.set(cap.total_inflight)
        QUEUE_DEPTH.labels(stream="jobs").set(await queue.depth())
    except Exception:
        # Don't fail the scrape on a Redis blip — Prometheus would alarm
        # on /metrics 500s. Stale gauges are tolerable for one cycle.
        logger.exception("metrics gauge refresh failed; serving stale values")
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# --------------------------------------------------------------------------- #
# Voice catalog (tenant-scoped, DB-backed)
# --------------------------------------------------------------------------- #
def _voice_to_public(v) -> VoicePublic:
    return VoicePublic(
        voice_id=v.voice_id,
        display_name=v.display_name,
        language=v.language,
        gender=v.gender,
        style_tags=list(v.style_tags or []),
        reference_seconds=v.reference_seconds,
        source=v.source,
        license=v.license,
        visibility=v.visibility,
        created_at=v.created_at.isoformat(),
        created_by=str(v.created_by_key_id) if v.created_by_key_id else "system",
    )


@app.get("/v1/voices", response_model=VoiceListResponse, tags=["voices"])
async def list_voices(
    ctx: Annotated[AuthContext, Depends(require_auth("voice:read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VoiceListResponse:
    """Catalog visible to this tenant: owned + shared-with-me + public."""
    repo = VoiceRepo(session, ctx.tenant_id)
    voices = [_voice_to_public(v) for v in await repo.list_accessible()]
    return VoiceListResponse(voices=voices, count=len(voices))


@app.get("/v1/voices/{voice_id}", response_model=VoicePublic, tags=["voices"])
async def get_voice(
    voice_id: str,
    ctx: Annotated[AuthContext, Depends(require_auth("voice:read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VoicePublic:
    db_voice = await _assert_voice_accessible_or_404(
        voice_id, ctx.tenant_id, session,
    )
    return _voice_to_public(db_voice)


@app.post("/v1/voices", response_model=EnrollResponse, tags=["voices"])
async def enroll_voice(
    ctx: Annotated[AuthContext, Depends(require_auth("voice:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    voice_id: Annotated[str, Form(min_length=3, max_length=64)],
    display_name: Annotated[str, Form(min_length=1, max_length=120)],
    reference_audio: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "tr",
    gender: Annotated[str, Form()] = "neutral",
    style_tags: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "user-enroll",
    license: Annotated[str, Form()] = "user-owned",  # noqa: A002 — DB column name
) -> EnrollResponse:
    try:
        validate_voice_id(voice_id)
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    data = await reference_audio.read()
    max_bytes = settings.enroll_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"reference audio exceeds {settings.enroll_max_upload_mb} MB",
        )
    if len(data) < 1024:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="reference audio too small (<1 KB)"
        )

    suffix = (Path(reference_audio.filename or "ref.wav").suffix.lower() or ".wav")
    if suffix not in ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"audio suffix '{suffix}' not allowed; use {sorted(ALLOWED_AUDIO_SUFFIXES)}",
        )

    # Land the trimmed WAV on local disk under data/reference-audio/<tenant>/<voice>.wav
    # for now. Faz B's R2 helper will replace this with bucket upload + s3:// URI.
    tenant_dir = settings.reference_audio_dir / "tenants" / str(ctx.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    target = tenant_dir / f"{voice_id}.wav"
    try:
        duration_seconds = trim_and_resample_to_wav(
            src_bytes=data,
            dst_path=target,
            trim_seconds=settings.reference_trim_seconds,
            target_sr=settings.reference_sample_rate,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    import hashlib

    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()

    repo = VoiceRepo(session, ctx.tenant_id)
    if await repo.get_owned(voice_id) is not None:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"voice '{voice_id}' already exists in this workspace",
        )

    tags = [t.strip() for t in style_tags.split(",") if t.strip()]
    voice = await repo.create(
        voice_id=voice_id,
        display_name=display_name,
        reference_uri=f"file://{target}",
        reference_sha256=sha256,
        reference_seconds=duration_seconds,
        reference_sample_rate=settings.reference_sample_rate,
        language=language,
        gender=gender,
        style_tags=tags,
        source=source,
        license=license,
        created_by_key_id=ctx.api_key_id,
    )
    await AuditRepo(session).record(
        actor_type="api_key",
        actor_id=ctx.api_key_id,
        actor_label=ctx.api_key.prefix,
        action="voice.create",
        result="success",
        tenant_id=ctx.tenant_id,
        target_type="voice",
        target_id=str(voice.id),
        payload={"voice_id": voice_id, "reference_sha256": sha256},
    )
    await session.commit()
    return EnrollResponse(voice=_voice_to_public(voice))


@app.delete("/v1/voices/{voice_id}", response_model=DeleteResponse, tags=["voices"])
async def delete_voice(
    voice_id: str,
    ctx: Annotated[AuthContext, Depends(require_auth("voice:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeleteResponse:
    try:
        validate_voice_id(voice_id)
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    repo = VoiceRepo(session, ctx.tenant_id)
    deleted = await repo.soft_delete(voice_id)
    if deleted is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"voice '{voice_id}' not found"
        )
    await AuditRepo(session).record(
        actor_type="api_key",
        actor_id=ctx.api_key_id,
        actor_label=ctx.api_key.prefix,
        action="voice.delete",
        result="success",
        tenant_id=ctx.tenant_id,
        target_type="voice",
        target_id=str(deleted.id),
    )
    await session.commit()
    return DeleteResponse(voice_id=voice_id)


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #
def _request_id_for(request: Request) -> uuid.UUID:
    """Take an X-Request-Id from the client or mint one. Stripe-style
    idempotency anchoring (D-01); the value is also exposed in the
    response header for client correlation."""
    header = request.headers.get("X-Request-Id")
    if header:
        try:
            return uuid.UUID(header)
        except ValueError:
            pass
    return uuid.uuid4()


def _app_label_from(request: Request) -> str | None:
    """Product attribution from `X-NQAI-App` request header (refactor R,
    2026-05-24). Caps length at 64 chars to stay within the metric
    cardinality budget (D-15). Returns None if header absent or empty
    after trimming."""
    raw = request.headers.get("X-NQAI-App")
    if not raw:
        return None
    val = raw.strip()[:64]
    return val or None


async def _record_usage(
    session: AsyncSession,
    *,
    ctx: AuthContext,
    voice_id: str,
    request_id: uuid.UUID,
    text_char_count: int,
    sentence_count: int,
    duration_ms: int,
    elapsed_ms: int,
    ttfb_ms: int | None,
    rtf: float | None,
    status_str: str,
    error_code: str | None = None,
    app_label: str | None = None,
) -> None:
    try:
        await UsageRepo(session, ctx.tenant_id).record(
            api_key_id=ctx.api_key_id,
            voice_id=voice_id,
            request_id=request_id,
            text_char_count=text_char_count,
            sentence_count=sentence_count,
            duration_ms=duration_ms,
            elapsed_ms=elapsed_ms,
            ttfb_ms=ttfb_ms,
            rtf=rtf,
            status=status_str,
            error_code=error_code,
            model_version=settings.model_id,
            app_label=app_label,
        )
        await session.commit()
    except Exception:
        # Usage logging is non-critical-path; structured log lands when
        # Faz C structlog ships. Swallow but roll back to keep session sane.
        await session.rollback()


@app.post("/v1/tts", tags=["synthesis"])
async def synthesize(
    body: TTSRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_auth("tts:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[TtsJobQueue, Depends(get_queue)],
) -> Response:
    """**Deprecated** — backward-compat queue proxy.

    Faz B.1 step 3 cutover: gateway no longer holds the engine. This
    endpoint XADD's the job to the same Redis Streams queue the async
    `/v1/tts/jobs` path uses, awaits chunks on the per-request result
    stream, concatenates them into a single WAV body, and returns it
    synchronously. The client API contract is preserved (POST → WAV
    body), but the actual synthesis runs in the worker.

    New code should use `POST /v1/tts/jobs` directly — see Sunset and
    Link headers (RFC 8594). Latency: this proxy adds 1-2 ms of queue
    + result-stream overhead vs. the in-process engine; everything
    above that is the worker's inference time.
    """
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    db_voice = await _assert_voice_accessible_or_404(
        body.voice_id, ctx.tenant_id, session,
    )

    rid = _request_id_for(request)
    redis = queue._redis  # use the same client the queue is bound to
    await _check_queue_depth_or_503(queue, session, ctx, voice_id=body.voice_id)

    # Reserve idempotency upfront so a duplicate sync POST with the
    # same X-Request-Id replays cleanly (same path as async jobs).
    idem = IdempotencyRepo(session, ctx.tenant_id)
    await idem.reserve(
        request_id=rid, api_key_id=ctx.api_key_id,
        request_hash=_hash_sync_body(body),
    )
    await session.commit()

    payload = TtsJobPayload(
        request_id=str(rid),
        tenant_id=str(ctx.tenant_id),
        api_key_id=str(ctx.api_key_id),
        voice_id=db_voice.voice_id,
        text=body.text,
        language=body.language,
        audio_format=body.audio_format,
        app_label=_app_label_from(request),
        enqueued_at_ms=int(time() * 1000),
    )
    try:
        await queue.submit(payload)
    except Exception as e:
        await idem.delete(rid)
        await session.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="failed to enqueue sync job",
        ) from e

    t0 = time()
    try:
        pcm, sentences, error = await collect_pcm_until_final(
            redis, str(rid),
            block_ms=200,
            overall_timeout_s=float(
                os.environ.get("NQAI_SYNC_TIMEOUT_S", "30")
            ),
        )
    except ResultStreamTimeout as e:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail="worker did not finish in time; retry via /v1/tts/jobs",
        ) from e
    elapsed_ms = int((time() - t0) * 1000)

    if error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"worker error: {error}",
        )

    # Worker emits fixed-rate PCM int16; a future client-controlled
    # resampling contract should be added explicitly when implemented.
    sample_rate = settings.target_sample_rate
    duration_ms = int(len(pcm) // 2 / max(sample_rate, 1) * 1000)
    rtf = (elapsed_ms / duration_ms) if duration_ms > 0 else None

    # Usage was already recorded by the worker; gateway doesn't double-
    # write here. The session has nothing pending — keep the
    # transaction sane.

    headers = {
        **_SYNC_DEPRECATION_HEADERS,
        "X-NQAI-Request-Id": str(rid),
        "X-NQAI-Sample-Rate": str(sample_rate),
        "X-NQAI-Voice-Id": db_voice.voice_id,
        "X-NQAI-Sentences": str(sentences),
        "X-NQAI-Duration-Seconds": f"{duration_ms / 1000.0:.3f}",
        "X-NQAI-Elapsed-Seconds": f"{elapsed_ms / 1000.0:.3f}",
        "X-NQAI-RTF": f"{rtf:.3f}" if rtf is not None else "inf",
    }
    if body.audio_format == "pcm16":
        return Response(
            content=pcm,
            media_type="application/octet-stream",
            headers=headers,
        )
    wav_bytes = pcm16_to_wav_bytes(pcm, sample_rate)
    return Response(content=wav_bytes, media_type="audio/wav", headers=headers)


@app.post("/v1/tts/stream", tags=["synthesis"])
async def synthesize_stream(
    body: TTSStreamRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_auth("tts:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[TtsJobQueue, Depends(get_queue)],
) -> StreamingResponse:
    """**Deprecated** — sentence-streamed queue proxy.

    Same queue path as `/v1/tts`, but chunks are forwarded to the
    client as they arrive on the result stream rather than concatenated.
    HTTP chunked transfer; the first byte hits the wire after the
    worker's first sentence is generated (Faz B.1 latency: 1-2s on
    L4 with stub engine; real engine drives most of the wall clock).

    Latency-focused live streaming (frame-level, WS / WebRTC) is
    Faz B.1.5 — that path will not use this endpoint.
    """
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    db_voice = await _assert_voice_accessible_or_404(
        body.voice_id, ctx.tenant_id, session,
    )
    rid = _request_id_for(request)
    redis = queue._redis
    await _check_queue_depth_or_503(queue, session, ctx, voice_id=body.voice_id)

    idem = IdempotencyRepo(session, ctx.tenant_id)
    await idem.reserve(
        request_id=rid, api_key_id=ctx.api_key_id,
        request_hash=_hash_sync_body(body),
    )
    await session.commit()

    payload = TtsJobPayload(
        request_id=str(rid),
        tenant_id=str(ctx.tenant_id),
        api_key_id=str(ctx.api_key_id),
        voice_id=db_voice.voice_id,
        text=body.text,
        language=body.language,
        audio_format=body.audio_format,
        app_label=_app_label_from(request),
        enqueued_at_ms=int(time() * 1000),
    )
    try:
        await queue.submit(payload)
    except Exception as e:
        await idem.delete(rid)
        await session.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="failed to enqueue sync job",
        ) from e

    sample_rate = settings.target_sample_rate
    headers = {
        **_SYNC_DEPRECATION_HEADERS,
        "X-NQAI-Request-Id": str(rid),
        "X-NQAI-Sample-Rate": str(sample_rate),
        "X-NQAI-Voice-Id": db_voice.voice_id,
    }

    async def _yield_pcm():
        try:
            async for chunk in consume_result_stream(
                redis, str(rid),
                block_ms=100,
                overall_timeout_s=float(
                    os.environ.get("NQAI_SYNC_TIMEOUT_S", "30")
                ),
            ):
                if chunk.error:
                    # Inline error sentinel — chunked-WAV can't carry
                    # status codes mid-stream; client must check trailers
                    # or out-of-band. For now we log + break.
                    logger.warning("worker error mid-stream: %s", chunk.error)
                    break
                if chunk.final:
                    break
                yield chunk.pcm_bytes
        except ResultStreamTimeout:
            logger.warning("sync-stream proxy timed out for rid=%s", rid)
            return

    async def _yield_wav():
        # RIFF "infinite size" header trick — same as worker.streaming
        # did before the cutover; we reproduce it here so gateway
        # doesn't import any worker module.
        import struct
        header = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
        header += b"fmt " + struct.pack(
            "<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        )
        header += b"data" + struct.pack("<I", 0xFFFFFFFF)
        yield header
        async for pcm in _yield_pcm():
            yield pcm

    if body.audio_format == "pcm16":
        return StreamingResponse(
            _yield_pcm(),
            media_type="application/octet-stream",
            headers=headers,
        )
    return StreamingResponse(
        _yield_wav(),
        media_type="audio/wav",
        headers=headers,
    )


def _hash_sync_body(body) -> str:
    """Stable hash of the sync request shape — same role as
    `_hash_job_body` for async, kept separate so a future shape change
    in TTSRequest vs TTSJobCreate doesn't accidentally collide."""
    import hashlib
    canonical = body.model_dump_json(by_alias=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Async TTS jobs — Stripe-pattern idempotent job model.
# --------------------------------------------------------------------------- #
# Hard ceiling on queue depth — when XLEN exceeds this multiple of the
# (unknown) worker count, gateway returns 503 instead of accepting new
# work. We err on the conservative side (high), so Faz B benchmarks tune
# this down once the actual GPU throughput is known.
QUEUE_DEPTH_BACKPRESSURE = int(os.environ.get("NQAI_QUEUE_DEPTH_LIMIT", "200"))


async def _check_queue_depth_or_503(
    queue: TtsJobQueue,
    session: AsyncSession,
    ctx: AuthContext,
    *,
    voice_id: str | None = None,
) -> None:
    """Faz C capacity-aware backpressure.

    Strategy:
    1. Read cluster capacity from worker heartbeats. If any healthy workers
       exist, admit when the cluster has enough total throughput to absorb
       the already-queued jobs plus this one within a bounded window.
       Concretely: ``depth <= total_capacity + headroom`` where ``headroom
       = total_capacity - total_inflight``. Interpretation: "next wave of
       jobs (one tick of full-cluster work, plus the slots that are free
       right now) is enough to consume what's queued." A hard XLEN ceiling
       still applies as a fail-safe so a slow cluster can't accept
       unbounded jobs even if its self-reported capacity says otherwise.
    2. If NO healthy workers (cold start, all workers crashed, or the
       heartbeat read itself failed): fall back to the original XLEN-only
       path — the queue ceiling is the only signal we trust. This keeps
       backpressure safe even when the heartbeat plane is degraded.
    """
    depth = await queue.depth()
    capacity_known = False
    try:
        cluster = await read_cluster_capacity(queue.redis)
        capacity_known = cluster.worker_count > 0
    except Exception as e:
        # Throttle the noise — under a degraded Redis every admission
        # path would otherwise emit a stack trace per request. Drop the
        # traceback; the exception type + message is enough signal.
        logger.warning(
            "read_cluster_capacity failed — falling back to XLEN-only: %s", e,
        )
        cluster = None
        capacity_known = False

    if capacity_known and cluster is not None:
        headroom = cluster.total_capacity - cluster.total_inflight
        if (
            headroom >= 0
            and depth <= QUEUE_DEPTH_BACKPRESSURE
            and depth <= headroom + cluster.total_capacity
        ):
            return
        denied_reason = "capacity_exhausted"
        payload = {
            "queue_depth": depth,
            "limit": QUEUE_DEPTH_BACKPRESSURE,
            "worker_count": cluster.worker_count,
            "total_capacity": cluster.total_capacity,
            "total_inflight": cluster.total_inflight,
        }
    else:
        if depth <= QUEUE_DEPTH_BACKPRESSURE:
            return
        denied_reason = "queue_depth_limit"
        payload = {"queue_depth": depth, "limit": QUEUE_DEPTH_BACKPRESSURE}

    await AuditRepo(session).record(
        actor_type="api_key",
        actor_id=ctx.api_key_id,
        actor_label=ctx.api_key.prefix,
        action="tts.backpressure",
        result="denied",
        tenant_id=ctx.tenant_id,
        payload={**payload, "reason": denied_reason},
    )
    await session.commit()
    # SLO denominator: every terminal outcome bumps TTS_REQUESTS so
    # dashboards can compute error_rate = errors / requests from one
    # family. Backpressure rejections count as a refusal outcome.
    try:
        TTS_REQUESTS.labels(
            tenant=str(ctx.tenant_id),
            voice=voice_id or "unknown",
            status="backpressure",
        ).inc()
    except Exception:
        logger.exception("TTS_REQUESTS backpressure increment failed — ignoring")
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="queue is saturated; retry shortly",
        headers={"Retry-After": "5"},
    )


@app.post(
    "/v1/tts/jobs",
    response_model=TTSJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["synthesis"],
)
async def create_tts_job(
    body: TTSJobCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_auth("tts:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[TtsJobQueue, Depends(get_queue)],
) -> TTSJobAccepted:
    """Enqueue a synthesis job. Idempotent — same Idempotency-Key returns
    the existing job's id, never enqueues twice. Worker side completes
    the job and writes the output to R2; clients poll the status endpoint.
    """
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )

    try:
        idempotency_key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Voice existence + tenant isolation — same path as sync /v1/tts.
    db_voice = await _assert_voice_accessible_or_404(
        body.voice_id, ctx.tenant_id, session,
    )

    # Stripe-style guarded reserve: same key + same body → replay; same
    # key + different body → 409. The body_hash check is critical —
    # without it a typo-fix POST under the same key silently no-ops.
    idem = IdempotencyRepo(session, ctx.tenant_id)
    request_hash = _hash_job_body(body)

    try:
        existing = await idem.get(idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                # Surface the prior row's metadata so the client can debug.
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "error": "idempotency_conflict",
                        "message": (
                            "Idempotency-Key reused with a different request body"
                        ),
                        "original_created_at": existing.created_at.isoformat(),
                        "original_status": existing.status,
                    },
                )
            existing_status = (
                "complete" if existing.status == "complete" else "queued"
            )
            return TTSJobAccepted(
                job_id=str(idempotency_key),
                status=existing_status,
                created_at=existing.created_at.isoformat(),
                deduplicated=True,
            )

        # Backpressure check before reserving — we don't want to leave
        # half-baked rows behind when the queue is saturated.
        await _check_queue_depth_or_503(queue, session, ctx, voice_id=body.voice_id)

        reserved, _is_new = await idem.reserve_or_get(
            request_id=idempotency_key,
            api_key_id=ctx.api_key_id,
            request_hash=request_hash,
        )
    except IdempotencyConflict as e:
        # Race: another concurrent request reserved between our get() and
        # reserve_or_get(). Translate to the same 409 the upfront check
        # would have returned.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "idempotency_conflict",
                "message": str(e),
                "original_created_at": e.existing.created_at.isoformat(),
                "original_status": e.existing.status,
            },
        ) from e

    await session.commit()

    payload = TtsJobPayload(
        request_id=str(idempotency_key),
        tenant_id=str(ctx.tenant_id),
        api_key_id=str(ctx.api_key_id),
        voice_id=db_voice.voice_id,
        text=body.text,
        language=body.language,
        audio_format=body.audio_format,
        params=body.params.model_dump(exclude_none=True) if body.params else None,
        app_label=_app_label_from(request),
        enqueued_at_ms=int(time() * 1000),
    )
    try:
        await queue.submit(payload)
    except Exception as e:
        # XADD failed after we reserved the idempotency row. The worker
        # never saw the job, so the reservation is bogus — DELETE the
        # row so the client can cleanly retry with the *same*
        # Idempotency-Key (audit F5 fix, 2026-05-24). Marking it
        # `failed` instead would poison the key: the next reserve_or_get
        # would hit a stale `failed` row, body_hash would match, and
        # the client would get back a job that was never enqueued.
        await idem.delete(idempotency_key)
        await session.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="failed to enqueue job; retry with the same Idempotency-Key",
        ) from e

    return TTSJobAccepted(
        job_id=str(idempotency_key),
        status="queued",
        created_at=reserved.created_at.isoformat(),
        deduplicated=False,
    )


@app.get(
    "/v1/tts/jobs/{job_id}",
    response_model=TTSJobStatusResponse,
    tags=["synthesis"],
)
async def get_tts_job(
    job_id: str,
    ctx: Annotated[AuthContext, Depends(require_auth("tts:read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TTSJobStatusResponse:
    try:
        rid = uuid.UUID(job_id)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="job_id must be a UUID"
        ) from e

    idem = IdempotencyRepo(session, ctx.tenant_id)
    row = await idem.get(rid)
    if row is None:
        # Same 404 whether the job is for another tenant, never existed,
        # or expired — no existence leak.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"job '{job_id}' not found"
        )

    response: dict[str, Any] = {
        "job_id": job_id,
        "status": _map_idempotency_status_to_job_status(row.status),
        "created_at": row.created_at.isoformat(),
    }

    if row.status == "complete":
        # Worker side stamped `response_uri` with an `s3://...` location.
        # The client must NEVER see the internal URI — mint a presigned
        # GET URL via the R2 helper (audit F6 fix, 2026-05-24). Falls
        # back to the raw URI only when R2 isn't configured (dev path
        # using `file://` references, env not set); in that case the
        # client is local and the URI is already openable.
        if row.response_uri:
            audio_url = _maybe_presigned_url(row.response_uri)
            response["output"] = TTSJobOutput(
                audio_url=audio_url,
                expires_at=_signed_url_expiry_iso(),
                content_type="audio/wav",
            )
        # Per-job metrics come from usage_records via request_id.
        usage_row = await _find_usage_row(session, ctx.tenant_id, rid)
        if usage_row is not None:
            response["metrics"] = TTSJobMetrics(
                queue_wait_ms=usage_row.queue_wait_ms,
                inference_ms=usage_row.inference_ms or usage_row.elapsed_ms,
                generated_audio_ms=usage_row.duration_ms,
                rtf=usage_row.rtf,
            )

    return TTSJobStatusResponse(**response)


# --------------------------------------------------------------------------- #
# Job helpers — private
# --------------------------------------------------------------------------- #
def _hash_job_body(body: TTSJobCreate) -> str:
    """Stable hash of the request shape so an Idempotency-Key replayed
    with *different* content doesn't silently return the old job. (Stripe
    surfaces a 409 in that case; we just record the hash for now and
    Faz B can wire the conflict response.)
    """
    import hashlib

    canonical = body.model_dump_json(by_alias=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _map_idempotency_status_to_job_status(s: str) -> str:
    """`job_idempotency.status` is {processing, complete, failed}; the
    client-facing job model is {queued, running, complete, failed}.

    For now `processing` always maps to `queued`. Once workers heartbeat
    a "running" state into the row (Faz B), this branches.
    """
    if s == "complete":
        return "complete"
    if s == "failed":
        return "failed"
    return "queued"


async def _find_usage_row(
    session: AsyncSession, tenant_id: uuid.UUID, request_id: uuid.UUID
):
    """Pull the usage_records row for a finished job. Tenant scoped."""
    from sqlalchemy import select

    from db.models import UsageRecord

    result = await session.execute(
        select(UsageRecord).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.request_id == request_id,
        )
    )
    return result.scalar_one_or_none()


def _now_iso() -> str:

    return datetime.now(timezone.utc).isoformat()


def _signed_url_expiry_iso() -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def _maybe_presigned_url(response_uri: str) -> str:
    """If `response_uri` is an S3/R2 URI and R2 storage is configured,
    return a presigned GET URL (1h TTL). Otherwise return the URI
    unchanged — local dev with `file://` references doesn't need
    presigning, and we never want to leak `s3://` to the client
    (audit F6 fix, 2026-05-24).
    """
    if not response_uri.startswith(("s3://", "r2://")):
        return response_uri
    try:
        from storage.r2 import get_r2_storage

        return get_r2_storage().presigned_get_url(response_uri, expires_in=3600)
    except Exception:
        # R2 env not set or transient client error — fall back to the
        # raw URI rather than 500ing the status poll. Worker-emitted
        # URIs in production paths will always have R2 configured.
        logger.warning("presigned URL minting failed for %s; returning raw URI",
                       response_uri)
        return response_uri


def run() -> None:
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("NQAI_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    run()
