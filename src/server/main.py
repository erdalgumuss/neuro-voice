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
import contextlib
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
from db.session import AsyncSessionLocal, get_session
from observability import (
    QUEUE_DEPTH,
    TTS_DEPRECATED_ENDPOINT_TOTAL,
    TTS_GATEWAY_FIRST_BYTE_SECONDS,
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
from .models import UnknownModelError, list_models, resolve_model
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
    ModelListResponse,
    ModelPublic,
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

if "*" in settings.cors_origins:
    logger.warning(
        "NQAI_CORS_ORIGINS contains '*' — admin cookie cross-origin "
        "will NOT work (Starlette silently drops credentials with a "
        "wildcard origin). Set NQAI_CORS_ORIGINS to an explicit "
        "allow-list in production.",
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # True (not False) — spec auth-multi-tenant.md §6 requires it so the
    # admin SPA can carry the `nqai_admin_access` cookie cross-origin.
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "X-NQAI-Sample-Rate",
        "X-NQAI-Voice-Id",
        "X-NQAI-Model-Id",
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
        # DLQ depth (audit L4 H1 2026-05-25): metric was declared with a
        # `stream` enum of {jobs, dlq} but only `jobs` was being set,
        # making a jammed DLQ invisible to the dashboard.
        from server.queue import DEFAULT_DLQ_STREAM
        dlq_len = int(await queue.redis.xlen(DEFAULT_DLQ_STREAM))
        QUEUE_DEPTH.labels(stream="dlq").set(dlq_len)
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


# --------------------------------------------------------------------------- #
# Model catalog (public, no auth — same as ElevenLabs /v1/models)
# --------------------------------------------------------------------------- #
@app.get("/v1/models", response_model=ModelListResponse, tags=["meta"])
async def list_tts_models() -> ModelListResponse:
    """Faz B.5 Dalga 1.2 — public model registry.

    Clients call this to discover available `model_id` values
    (turbo / hd / character presets on the VoxCPM2 base) along with
    the underlying inference knobs. Mirrors the vendor pattern
    (ElevenLabs `GET /v1/models`); unauthenticated because the
    catalog is the same for every tenant.
    """
    from .models import DEFAULT_MODEL_ID
    models = [
        ModelPublic(
            model_id=p.model_id,
            display_name=p.display_name,
            description=p.description,
            cfg_value=p.cfg_value,
            inference_timesteps=p.inference_timesteps,
            is_default=p.is_default,
        )
        for p in list_models()
    ]
    return ModelListResponse(
        models=models,
        count=len(models),
        default_model_id=DEFAULT_MODEL_ID,
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
    # Sunset readiness counter (audit L4 H1). Every hit on this
    # deprecated surface bumps a dedicated counter so an operator can
    # plot rate(...) over time and see when migration is "done"
    # (counter trends to 0). Independent from TTS_REQUESTS to avoid
    # double-counting success.
    try:
        TTS_DEPRECATED_ENDPOINT_TOTAL.labels(endpoint="/v1/tts").inc()
    except Exception:
        logger.exception("deprecated counter increment failed — ignoring")

    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    db_voice = await _assert_voice_accessible_or_404(
        body.voice_id, ctx.tenant_id, session,
    )

    # Faz B.5 Dalga 1.2 — validate model_id early so the client gets
    # a clean 400 instead of a worker-side PoisonJob. Resolves to the
    # registry default when body.model_id is None.
    try:
        preset = resolve_model(body.model_id)
    except UnknownModelError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

    rid = _request_id_for(request)
    redis = queue._redis  # use the same client the queue is bound to
    await _check_queue_depth_or_503(queue, session, ctx, voice_id=body.voice_id)

    # Idempotency replay (audit L1 H1 2026-05-25):
    # `reserve_or_get` gives the Stripe pattern — same X-Request-Id + same
    # body returns the existing row, different body raises
    # IdempotencyConflict → 409. Pre-fix the sync paths used bare
    # `reserve()` which crashed with raw IntegrityError on duplicate
    # X-Request-Id (raw 500 to the client). Async /v1/tts/jobs already
    # used this pattern; sync paths now match.
    idem = IdempotencyRepo(session, ctx.tenant_id)
    try:
        _row, reserved_new = await idem.reserve_or_get(
            request_id=rid, api_key_id=ctx.api_key_id,
            request_hash=_hash_sync_body(body),
        )
    except IdempotencyConflict as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="X-Request-Id reuse with different body",
        ) from e
    await session.commit()

    if not reserved_new:
        # Cached replay path: a previous attempt with the same
        # X-Request-Id is in flight or already finished. We don't
        # re-enqueue (would double-charge the worker) — instead we
        # subscribe to the existing result stream. If the prior attempt
        # is still processing the client gets its audio; if it finished
        # the worker has already DELed the stream and this will timeout
        # → 504 with a hint to poll /v1/tts/jobs/{id}. Acceptable
        # because the contract documented for the sync path is
        # "fire-and-block"; replay-after-completion is rare and the
        # async job surface is the right place to fetch the artifact.
        logger.info("sync /v1/tts replay for rid=%s (no re-enqueue)", rid)
    else:
        payload = TtsJobPayload(
            request_id=str(rid),
            tenant_id=str(ctx.tenant_id),
            api_key_id=str(ctx.api_key_id),
            voice_id=db_voice.voice_id,
            text=body.text,
            language=body.language,
            audio_format=body.audio_format,
            model_id=preset.model_id,
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
        "X-NQAI-Model-Id": preset.model_id,
        "X-NQAI-Sentences": str(sentences),
        "X-NQAI-Duration-Seconds": f"{duration_ms / 1000.0:.3f}",
        "X-NQAI-Elapsed-Seconds": f"{elapsed_ms / 1000.0:.3f}",
        "X-NQAI-RTF": f"{rtf:.3f}" if rtf is not None else "inf",
    }
    # Faz B.5 Dalga 1 — codec layer dispatch on the sync path too.
    # Sync /v1/tts buffers all PCM into one body before encoding, so
    # we run the encoder to completion in one shot. mp3 + opus = real
    # bandwidth savings (mp3 ~3-5x, opus ~10x) for the deprecated
    # sync surface until clients migrate to /v1/tts/jobs.
    if body.audio_format == "wav":
        wav_bytes = pcm16_to_wav_bytes(pcm, sample_rate)
        return Response(content=wav_bytes, media_type="audio/wav", headers=headers)
    if body.audio_format == "pcm16":
        return Response(
            content=pcm,
            media_type="application/octet-stream",
            headers=headers,
        )

    from audio.encoders import EncoderError, get_stream_encoder
    encoder = get_stream_encoder(body.audio_format, sample_rate=sample_rate)
    try:
        await encoder.start()
        encoded = await encoder.encode_chunk(pcm)
        tail = await encoder.close()
        body_bytes = encoded + tail
    except EncoderError:
        logger.exception("sync /v1/tts encoder failed; falling back to WAV")
        wav_bytes = pcm16_to_wav_bytes(pcm, sample_rate)
        return Response(content=wav_bytes, media_type="audio/wav", headers=headers)

    media_type = {"mp3": "audio/mpeg", "opus": "audio/ogg"}[body.audio_format]
    return Response(content=body_bytes, media_type=media_type, headers=headers)


@app.post("/v1/tts/stream", tags=["synthesis"])
async def synthesize_stream(
    body: TTSStreamRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_auth("tts:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[TtsJobQueue, Depends(get_queue)],
) -> StreamingResponse:
    """Primary streaming TTS endpoint — HTTP chunked WAV.

    Sentence-streamed via the same Redis queue path that drives async
    jobs and the deprecated sync ``/v1/tts``. The worker pipeline uses
    ``iter_engine_chunks`` (Faz B.1.5) to bridge the engine generator
    onto the result stream frame-by-frame, so the first byte hits the
    client wire as soon as the engine emits its first sentence — full
    generation does NOT drain before publishing.

    This is the canonical industry-standard one-way streaming TTS
    surface (ElevenLabs / OpenAI Audio / Cartesia / MiniMax mental
    model). Duplex voice-agent (NIVA call-center, real bidirectional
    conversation) is a separate product surface — if that product
    ships it will use a different transport (WebRTC / gRPC) on a
    different endpoint, not this one. See
    ``docs/architecture/streaming-protocol.md``.
    """
    # Capture request-received timestamp at the TOP of the handler
    # (audit L3 H2 2026-05-25). Pre-fix the timer was set AFTER auth +
    # voice check + idempotency reserve + queue submit ran, silently
    # excluding 5-50 ms of preamble from the client-facing TTFB metric.
    # `time.monotonic_ns` avoids wall-clock jumps tripping the
    # gateway_first_byte_ms_nonneg CHECK constraint.
    import time as _time
    request_received_ns = _time.monotonic_ns()

    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    db_voice = await _assert_voice_accessible_or_404(
        body.voice_id, ctx.tenant_id, session,
    )

    # Faz B.5 Dalga 1.2 — validate model_id early (400 instead of poison).
    try:
        preset = resolve_model(body.model_id)
    except UnknownModelError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

    rid = _request_id_for(request)
    redis = queue._redis
    await _check_queue_depth_or_503(queue, session, ctx, voice_id=body.voice_id)

    # Same reserve_or_get pattern as sync /v1/tts (audit L1 H1).
    idem = IdempotencyRepo(session, ctx.tenant_id)
    try:
        _row, reserved_new = await idem.reserve_or_get(
            request_id=rid, api_key_id=ctx.api_key_id,
            request_hash=_hash_sync_body(body),
        )
    except IdempotencyConflict as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="X-Request-Id reuse with different body",
        ) from e
    await session.commit()

    if reserved_new:
        payload = TtsJobPayload(
            request_id=str(rid),
            tenant_id=str(ctx.tenant_id),
            api_key_id=str(ctx.api_key_id),
            voice_id=db_voice.voice_id,
            text=body.text,
            language=body.language,
            audio_format=body.audio_format,
            model_id=preset.model_id,
            app_label=_app_label_from(request),
            enqueued_at_ms=int(time() * 1000),
        )
        try:
            await queue.submit(payload)
        except Exception as e:
            await idem.delete(rid)
            await session.commit()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="failed to enqueue sync job",
            ) from e
    else:
        logger.info(
            "sync /v1/tts/stream replay for rid=%s (no re-enqueue)", rid,
        )

    sample_rate = settings.target_sample_rate
    # NO Deprecation / Sunset headers here — /v1/tts/stream is the
    # canonical streaming surface (streaming-protocol.md:49). The
    # earlier inclusion was a copy-paste from sync /v1/tts; clients
    # honouring RFC 8594 would have been silently nudged off the
    # primary endpoint (audit L1 2026-05-25).
    headers = {
        "X-NQAI-Request-Id": str(rid),
        "X-NQAI-Sample-Rate": str(sample_rate),
        "X-NQAI-Voice-Id": db_voice.voice_id,
        "X-NQAI-Model-Id": preset.model_id,
    }

    # Faz C v1 item 1 — gateway-side TTFB measurement.
    # request_received_ns was captured at the TOP of the handler so the
    # measurement INCLUDES auth + voice check + idempotency + queue
    # submit (audit L3 H2). The instrumented wrapper records first-byte
    # AT the wire, INCLUDING the WAV header for wav mode (audit L3 H3).
    gateway_first_byte_ms_holder: dict[str, int] = {}

    def _stamp_first_byte_if_unset() -> None:
        if "ms" not in gateway_first_byte_ms_holder:
            elapsed_ns = _time.monotonic_ns() - request_received_ns
            gateway_first_byte_ms_holder["ms"] = max(
                0, elapsed_ns // 1_000_000,
            )

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
        # RIFF "infinite size" header trick: gateway sends 44 bytes of
        # header up front, then PCM streams. The WAV header IS the
        # client's first byte over the wire, so it counts toward TTFB
        # (audit L3 H3 — pre-fix the metric set only on first PCM,
        # excluding the header, understating real TTFB in WAV mode).
        import struct
        header = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
        header += b"fmt " + struct.pack(
            "<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        )
        header += b"data" + struct.pack("<I", 0xFFFFFFFF)
        yield header
        async for pcm in _yield_pcm():
            yield pcm

    async def _instrumented(inner):
        """Forward `inner` and stitch gateway_first_byte_ms into the
        usage row + Prometheus histogram after the stream completes
        (success OR client disconnect). The worker writes the usage row
        when its pipeline commits; the gateway updates only the column
        it can measure. Failures are best-effort — observability MUST
        NOT break the stream itself.

        First-byte capture (audit L3 H3): stamp on the FIRST yield out
        of `inner`, regardless of mode. In WAV mode that's the RIFF
        header (44 bytes the client receives first); in PCM mode it's
        the first audio chunk. Either way it's "what the wire sees
        first" — matches HTTP TTFB semantics.
        """
        try:
            async for buf in inner:
                _stamp_first_byte_if_unset()
                yield buf
        finally:
            first_byte_ms = gateway_first_byte_ms_holder.get("ms")
            if first_byte_ms is not None:
                # No bytes ever made it out (worker error / timeout) means
                # `first_byte_ms` is None and we skip persistence — there's
                # no client TTFB to record.
                try:
                    async with AsyncSessionLocal() as s:
                        repo = UsageRepo(s, ctx.tenant_id)
                        rowcount = await repo.update_gateway_first_byte_ms(
                            rid, first_byte_ms,
                        )
                        await s.commit()
                    if rowcount == 0:
                        # Worker hadn't written the usage row yet when the
                        # gateway finished streaming. Rare — happens only on
                        # an abrupt client disconnect before the worker has
                        # done its DB commit. Log and move on; the row will
                        # exist eventually but without gateway timing.
                        logger.info(
                            "usage row for rid=%s not yet present at stream "
                            "end — skipping gateway_first_byte_ms stitch", rid,
                        )
                except Exception:
                    logger.exception(
                        "gateway_first_byte_ms persistence failed for rid=%s "
                        "— stream succeeded, metric lost", rid,
                    )
                try:
                    TTS_GATEWAY_FIRST_BYTE_SECONDS.labels(
                        tenant=str(ctx.tenant_id),
                        voice=db_voice.voice_id,
                    ).observe(first_byte_ms / 1000.0)
                except Exception:
                    logger.exception(
                        "TTS_GATEWAY_FIRST_BYTE_SECONDS observe failed — "
                        "ignoring",
                    )

    # Faz B.5 Dalga 1 — codec layer dispatch:
    # * wav  — inline RIFF "infinite size" trick (predates this layer;
    #          ffmpeg can't write WAV streaming-safely, so the inline
    #          path is the right answer).
    # * pcm16 — passthrough; client uses sample-rate header to play.
    # * mp3 / opus — ffmpeg pipe encoder; bandwidth wins ~3-10x vs WAV.
    if body.audio_format == "wav":
        return StreamingResponse(
            _instrumented(_yield_wav()),
            media_type="audio/wav",
            headers=headers,
        )

    async def _yield_encoded():
        from audio.encoders import EncoderError, get_stream_encoder
        try:
            encoder = get_stream_encoder(
                body.audio_format, sample_rate=sample_rate,
            )
        except KeyError as e:
            # Pydantic Literal should have caught this before we got
            # here; defensive.
            logger.error("unknown audio_format reached encoder dispatch: %s", e)
            return
        try:
            await encoder.start()
        except EncoderError:
            logger.exception(
                "encoder start failed for %s — falling back to PCM",
                body.audio_format,
            )
            async for pcm in _yield_pcm():
                yield pcm
            return

        try:
            async for pcm in _yield_pcm():
                try:
                    out = await encoder.encode_chunk(pcm)
                except EncoderError:
                    logger.exception(
                        "encoder.encode_chunk failed mid-stream — "
                        "breaking stream",
                    )
                    return
                if out:
                    yield out
            # Flush the encoder's container trailer (mp3: nothing; opus:
            # OGG end-of-stream page).
            tail = await encoder.close()
            if tail:
                yield tail
        finally:
            # Defensive: if the consumer cancelled mid-stream, make
            # sure the ffmpeg subprocess is still cleaned up.
            if not getattr(encoder, "_closed", True):
                with contextlib.suppress(Exception):
                    await encoder.close()

    # Content-Type by format. Streaming PCM goes octet-stream so
    # browsers don't try to autoplay it.
    media_type = {
        "pcm16": "application/octet-stream",
        "mp3": "audio/mpeg",
        "opus": "audio/ogg",
    }[body.audio_format]
    return StreamingResponse(
        _instrumented(_yield_encoded()),
        media_type=media_type,
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

    Strategy
    --------
    1. Read cluster capacity from worker heartbeats. If any healthy workers
       exist, admit while:

           depth <= total_capacity + headroom

       where ``headroom = total_capacity - total_inflight``. The intent
       is: "if I admit this job, the next wave of cluster work (the slots
       currently free, plus one full pass through every slot) will
       absorb what's queued." This **accepts backlog up to one full
       cluster-pass even when the cluster is 100 % utilised** — a
       deliberate choice for TTS workloads where individual jobs are
       multi-second and a few seconds of queue latency is acceptable.
       Pinned by ``tests/test_metrics_endpoint.py::test_backpressure_
       admits_at_capacity_with_one_cluster_pass_backlog`` so a future
       refactor can't quietly tighten it. A hard XLEN ceiling still
       applies as a final fail-safe so a slow cluster can't accept
       unbounded jobs even if its self-reported capacity says otherwise.
    2. If NO healthy workers (cold start, all workers crashed, or the
       heartbeat read itself failed): fall back to the original
       XLEN-only path — the queue ceiling is the only signal we trust.
       This keeps backpressure safe even when the heartbeat plane is
       degraded.
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

    # Faz B.5 Dalga 1.2 — model_id validation up front.
    try:
        preset = resolve_model(body.model_id)
    except UnknownModelError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e

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
        model_id=preset.model_id,
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
