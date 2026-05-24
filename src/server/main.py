"""NQAI Voice TTS — FastAPI application.

Run with:
    uvicorn server.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from registry import Voice, VoiceAlreadyExists, VoiceNotFound, VoiceRegistry
from registry.catalog import InvalidVoiceId

from . import streaming
from .auth import require_api_key
from .config import settings
from .engine import (
    BaseSynthEngine,
    get_engine,
    pcm16_to_wav_bytes,
)
from .schemas import (
    DeleteResponse,
    EnrollResponse,
    ErrorResponse,
    HealthResponse,
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

VERSION = "0.1.0"

_registry = VoiceRegistry(
    voices_dir=settings.voices_dir,
    reference_dir=settings.reference_audio_dir,
)
_engine: BaseSynthEngine | None = None


def get_registry() -> VoiceRegistry:
    return _registry


def get_engine_dep() -> BaseSynthEngine:
    global _engine
    if _engine is None:
        _engine = get_engine(model_id=settings.model_id, device=settings.device)
    return _engine


def _voice_or_404(voice_id: str, reg: VoiceRegistry) -> Voice:
    try:
        return reg.get(voice_id)
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except VoiceNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"voice '{voice_id}' not found"
        ) from e


def _reference_path(voice: Voice) -> Path:
    return voice.reference_path(settings.reference_audio_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("nqai-voice server starting (model=%s device=%s)", settings.model_id, settings.device)
    logger.info("voices_dir=%s", settings.voices_dir)
    logger.info("reference_dir=%s", settings.reference_audio_dir)
    n = len(_registry.list_voices())
    logger.info("loaded %d voice(s) from catalog", n)
    yield
    logger.info("nqai-voice server shutting down")


app = FastAPI(
    title="NQAI Voice — Türkçe TTS Platform",
    description=(
        "Türkçe + voice-cloning + streaming TTS API on Chatterbox Multilingual. "
        "Catalog-based voices (`/v1/voices`), HTTP synthesis (`/v1/tts`), and "
        "sentence-chunked streaming (`/v1/tts/stream`)."
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
    expose_headers=["X-NQAI-Sample-Rate", "X-NQAI-Voice-Id", "X-NQAI-Sentences"],
)


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
        voice_count=len(_registry.list_voices()),
        version=VERSION,
    )


@app.post("/admin/warmup", tags=["meta"])
async def warmup(
    _: Annotated[str, Depends(require_api_key)],
    eng: Annotated[BaseSynthEngine, Depends(get_engine_dep)],
) -> dict:
    eng.warmup()
    return {"loaded": True, "sample_rate": eng.sample_rate}


@app.get("/v1/voices", response_model=VoiceListResponse, tags=["voices"])
async def list_voices(
    _: Annotated[str, Depends(require_api_key)],
    reg: Annotated[VoiceRegistry, Depends(get_registry)],
) -> VoiceListResponse:
    voices = [VoicePublic(**v.to_public()) for v in reg.list_voices()]
    return VoiceListResponse(voices=voices, count=len(voices))


@app.get("/v1/voices/{voice_id}", response_model=VoicePublic, tags=["voices"])
async def get_voice(
    voice_id: str,
    _: Annotated[str, Depends(require_api_key)],
    reg: Annotated[VoiceRegistry, Depends(get_registry)],
) -> VoicePublic:
    return VoicePublic(**_voice_or_404(voice_id, reg).to_public())


@app.post("/v1/voices", response_model=EnrollResponse, tags=["voices"])
async def enroll_voice(
    api_key: Annotated[str, Depends(require_api_key)],
    reg: Annotated[VoiceRegistry, Depends(get_registry)],
    voice_id: Annotated[str, Form(min_length=3, max_length=64)],
    display_name: Annotated[str, Form(min_length=1, max_length=120)],
    reference_audio: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "tr",
    gender: Annotated[str, Form()] = "neutral",
    style_tags: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "user-enroll",
    license: Annotated[str, Form()] = "user-owned",
) -> EnrollResponse:
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

    suffix = Path(reference_audio.filename or "ref.wav").suffix.lower() or ".wav"
    tags = [t.strip() for t in style_tags.split(",") if t.strip()]

    try:
        voice = reg.enroll(
            voice_id=voice_id,
            display_name=display_name,
            reference_audio_bytes=data,
            reference_audio_suffix=suffix,
            language=language,
            gender=gender,
            style_tags=tags,
            source=source,
            license=license,
            created_by=api_key,
            reference_trim_seconds=settings.reference_trim_seconds,
            # VoxCPM2 wants 16 kHz mono reference; output is 48 kHz.
            target_sample_rate=settings.reference_sample_rate,
        )
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except VoiceAlreadyExists as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"voice '{e}' already exists") from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return EnrollResponse(voice=VoicePublic(**voice.to_public()))


@app.delete("/v1/voices/{voice_id}", response_model=DeleteResponse, tags=["voices"])
async def delete_voice(
    voice_id: str,
    _: Annotated[str, Depends(require_api_key)],
    reg: Annotated[VoiceRegistry, Depends(get_registry)],
) -> DeleteResponse:
    try:
        reg.delete(voice_id)
    except InvalidVoiceId as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except VoiceNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"voice '{voice_id}' not found"
        ) from e
    return DeleteResponse(voice_id=voice_id)


@app.post("/v1/tts", tags=["synthesis"])
async def synthesize(
    body: TTSRequest,
    _: Annotated[str, Depends(require_api_key)],
    reg: Annotated[VoiceRegistry, Depends(get_registry)],
    eng: Annotated[BaseSynthEngine, Depends(get_engine_dep)],
) -> Response:
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    voice = _voice_or_404(body.voice_id, reg)
    reference_path = _reference_path(voice)
    result = eng.synthesize(
        text=body.text,
        voice=voice,
        reference_path=reference_path,
        language_id=body.language,
    )
    headers = {
        "X-NQAI-Sample-Rate": str(result.sample_rate),
        "X-NQAI-Voice-Id": voice.voice_id,
        "X-NQAI-Sentences": str(result.sentence_count),
        "X-NQAI-Duration-Seconds": f"{result.duration_seconds:.3f}",
        "X-NQAI-Elapsed-Seconds": f"{result.elapsed_seconds:.3f}",
        "X-NQAI-RTF": f"{result.elapsed_seconds / result.duration_seconds:.3f}" if result.duration_seconds else "inf",
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
    _: Annotated[str, Depends(require_api_key)],
    reg: Annotated[VoiceRegistry, Depends(get_registry)],
    eng: Annotated[BaseSynthEngine, Depends(get_engine_dep)],
) -> StreamingResponse:
    if len(body.text) > settings.max_chars_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"text exceeds max_chars={settings.max_chars_per_request}",
        )
    voice = _voice_or_404(body.voice_id, reg)
    reference_path = _reference_path(voice)
    headers = {
        "X-NQAI-Sample-Rate": str(eng.sample_rate),
        "X-NQAI-Voice-Id": voice.voice_id,
    }
    if body.audio_format == "pcm16":
        return StreamingResponse(
            streaming.stream_pcm16(
                eng,
                text=body.text,
                voice=voice,
                reference_path=reference_path,
                language_id=body.language,
            ),
            media_type="application/octet-stream",
            headers=headers,
        )
    return StreamingResponse(
        streaming.stream_wav(
            eng,
            text=body.text,
            voice=voice,
            reference_path=reference_path,
            language_id=body.language,
        ),
        media_type="audio/wav",
        headers=headers,
    )


def run() -> None:
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=int(__import__("os").environ.get("NQAI_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    run()
