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

import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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

from db.session import get_session
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

from . import streaming
from .admin import admin_router
from .auth import AuthContext, require_auth
from .config import settings
from .engine import (
    BaseSynthEngine,
    get_engine,
    pcm16_to_wav_bytes,
)
from .queue import TtsJobPayload, TtsJobQueue, get_queue, parse_idempotency_key
from .reference_resolver import (
    ReferenceAudioMissing,
    UnsupportedReferenceURI,
    resolve_reference_uri,
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

VERSION = "0.3.0"

# Engine singleton — lazy load. The model itself stays per-process; multi-
# replica horizontal scale waits on the Faz B worker split.
_engine: BaseSynthEngine | None = None


def get_engine_dep() -> BaseSynthEngine:
    global _engine
    if _engine is None:
        _engine = get_engine(
            model_id=settings.model_id,
            device=settings.device,
            lora_path=settings.lora_path,
            lora_config_path=settings.lora_config_path,
            cfg_value=settings.cfg_value,
            inference_timesteps=settings.inference_timesteps,
            optimize=settings.optimize,
        )
    return _engine


# --------------------------------------------------------------------------- #
# VoiceView — duck-typed shape the engine consumes
# --------------------------------------------------------------------------- #
# The engine accesses three attributes on a "voice": `voice_id`,
# `engine_params`, and `adapter` (an optional mapping understood by
# engine._lora_from_mapping). Both the legacy registry.Voice and the
# DB-backed db.models.Voice satisfy this loosely, but they're shaped
# differently — DB carries adapter_uri/_sha256/_type as separate columns
# while the engine wants a single dict. VoiceView is the canonical
# adapter the request path uses.
@dataclass(frozen=True)
class VoiceView:
    voice_id: str
    engine_params: dict[str, Any] = field(default_factory=dict)
    adapter: dict[str, Any] | None = None


def _voice_view_from_db(v) -> VoiceView:
    """Project a db.models.Voice → engine-shaped VoiceView."""
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


async def _load_voice_or_404(
    voice_id: str, tenant_id: uuid.UUID, session: AsyncSession
):
    """Viewer-scoped accessibility lookup returning (db_voice, VoiceView,
    resolved_path). Resolves owned ∪ public ∪ shared (refactor R, D-08).

    Raises HTTPException(404/400). Resolved reference path is a local
    file — Faz B's R2 fetcher plugs in here for s3:// URIs.
    """
    try:
        validate_voice_id(voice_id)
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    repo = VoiceRepo(session, tenant_id)
    voice = await repo.get_accessible(voice_id)
    if voice is None:
        # Existence-leak prevention — same 404 whether the voice belongs
        # to another tenant (and isn't shared/public) or doesn't exist.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"voice '{voice_id}' not found"
        )

    try:
        ref_path = resolve_reference_uri(voice.reference_uri)
    except ReferenceAudioMissing as e:
        # Reference uploaded but file gone — operator must re-upload.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"reference audio for '{voice_id}' is missing on this node",
        ) from e
    except UnsupportedReferenceURI as e:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, detail=str(e)
        ) from e

    return voice, _voice_view_from_db(voice), ref_path


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
    # In-flight drain is a Faz A.6+ item (lifespan shutdown hook). For now
    # we just log so the K8s eviction trail is visible.
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
# /health — unauthenticated liveness
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    eng = get_engine_dep()
    loaded = getattr(eng, "_model", None) is not None
    return HealthResponse(
        status="ok" if loaded else "warming",
        model_id=settings.model_id,
        device=getattr(eng, "_device", settings.device),
        sample_rate=getattr(eng, "sample_rate", 0),
        loaded=loaded,
        voice_count=0,  # Faz B'de DB-aggregate; gateway voice-count'a karar vermez
        version=VERSION,
    )


# --------------------------------------------------------------------------- #
# /admin/warmup — admin-scoped (uses Bearer API key with admin:read scope)
# --------------------------------------------------------------------------- #
@app.post("/admin/warmup", tags=["meta"])
async def warmup(
    _ctx: Annotated[AuthContext, Depends(require_auth("admin:read"))],
    eng: Annotated[BaseSynthEngine, Depends(get_engine_dep)],
) -> dict:
    eng.warmup()
    return {"loaded": True, "sample_rate": eng.sample_rate}


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
    db_voice, _view, _path = await _load_voice_or_404(voice_id, ctx.tenant_id, session)
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
    eng: Annotated[BaseSynthEngine, Depends(get_engine_dep)],
) -> Response:
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    db_voice, view, ref_path = await _load_voice_or_404(
        body.voice_id, ctx.tenant_id, session
    )
    rid = _request_id_for(request)
    t0 = time()
    result = eng.synthesize(
        text=body.text,
        voice=view,
        reference_path=ref_path,
        language_id=body.language,
    )
    elapsed_ms = int((time() - t0) * 1000)
    duration_ms = int(result.duration_seconds * 1000)
    rtf = (result.elapsed_seconds / result.duration_seconds) if result.duration_seconds else None

    await _record_usage(
        session,
        ctx=ctx,
        voice_id=view.voice_id,
        request_id=rid,
        text_char_count=len(body.text),
        sentence_count=result.sentence_count,
        duration_ms=duration_ms,
        elapsed_ms=elapsed_ms,
        ttfb_ms=None,
        rtf=rtf,
        status_str="ok",
        app_label=_app_label_from(request),
    )

    headers = {
        "X-NQAI-Request-Id": str(rid),
        "X-NQAI-Sample-Rate": str(result.sample_rate),
        "X-NQAI-Voice-Id": view.voice_id,
        "X-NQAI-Sentences": str(result.sentence_count),
        "X-NQAI-Duration-Seconds": f"{result.duration_seconds:.3f}",
        "X-NQAI-Elapsed-Seconds": f"{result.elapsed_seconds:.3f}",
        "X-NQAI-RTF": f"{rtf:.3f}" if rtf is not None else "inf",
    }
    if body.audio_format == "pcm16":
        return Response(
            content=result.pcm_int16,
            media_type="application/octet-stream",
            headers=headers,
        )
    wav_bytes = pcm16_to_wav_bytes(result.pcm_int16, result.sample_rate)
    return Response(content=wav_bytes, media_type="audio/wav", headers=headers)


@app.post("/v1/tts/stream", tags=["synthesis"])
async def synthesize_stream(
    body: TTSStreamRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_auth("tts:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    eng: Annotated[BaseSynthEngine, Depends(get_engine_dep)],
) -> StreamingResponse:
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    _db_voice, view, ref_path = await _load_voice_or_404(
        body.voice_id, ctx.tenant_id, session
    )
    rid = _request_id_for(request)
    headers = {
        "X-NQAI-Request-Id": str(rid),
        "X-NQAI-Sample-Rate": str(eng.sample_rate),
        "X-NQAI-Voice-Id": view.voice_id,
    }
    if body.audio_format == "pcm16":
        return StreamingResponse(
            streaming.stream_pcm16(
                eng,
                text=body.text,
                voice=view,
                reference_path=ref_path,
                language_id=body.language,
            ),
            media_type="application/octet-stream",
            headers=headers,
        )
    return StreamingResponse(
        streaming.stream_wav(
            eng,
            text=body.text,
            voice=view,
            reference_path=ref_path,
            language_id=body.language,
        ),
        media_type="audio/wav",
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Async TTS jobs — Stripe-pattern idempotent job model.
# --------------------------------------------------------------------------- #
# Hard ceiling on queue depth — when XLEN exceeds this multiple of the
# (unknown) worker count, gateway returns 503 instead of accepting new
# work. We err on the conservative side (high), so Faz B benchmarks tune
# this down once the actual GPU throughput is known.
QUEUE_DEPTH_BACKPRESSURE = int(os.environ.get("NQAI_QUEUE_DEPTH_LIMIT", "200"))


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
    db_voice, _view, _path = await _load_voice_or_404(
        body.voice_id, ctx.tenant_id, session
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
        depth = await queue.depth()
        if depth > QUEUE_DEPTH_BACKPRESSURE:
            await AuditRepo(session).record(
                actor_type="api_key",
                actor_id=ctx.api_key_id,
                actor_label=ctx.api_key.prefix,
                action="tts.backpressure",
                result="denied",
                tenant_id=ctx.tenant_id,
                payload={"queue_depth": depth, "limit": QUEUE_DEPTH_BACKPRESSURE},
            )
            await session.commit()
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="queue is saturated; retry shortly",
                headers={"Retry-After": "5"},
            )

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
                queue_wait_ms=None,  # Faz B worker writes this
                inference_ms=usage_row.elapsed_ms,
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
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _signed_url_expiry_iso() -> str:
    from datetime import datetime, timedelta, timezone

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
