"""Pydantic schemas for the v1 HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Faz B.5 Dalga 1 — codec layer (audit 2026-05-25): mp3 + opus added
# alongside the existing wav/pcm16. mp3 (~3-5x smaller than wav) and
# opus (~10x smaller, voice-tuned) are the formats real product
# clients want; wav stays for download/debug, pcm16 stays for
# low-level / benchmark use.
AudioFormat = Literal["wav", "pcm16", "mp3", "opus"]
StreamFormat = Literal["wav", "pcm16", "mp3", "opus"]


# --------------------------------------------------------------------------- #
# Voice settings — Dalga 2.1, per-request voice fine-tuning
# --------------------------------------------------------------------------- #
class VoiceSettings(BaseModel):
    """Per-request voice tuning knobs.

    Vendor parity layer: ElevenLabs ships
    ``{stability, similarity_boost, style, use_speaker_boost, speed}``;
    MiniMax ships ``{speed, vol, pitch}``. We accept the superset so
    NEEKO/NIVA/NeuroCourse can use the same SDK shapes they're used to;
    fields the engine doesn't act on YET are documented as forward-
    compatible (validated + persisted but no-op on the model).

    Field semantics (NQAI mapping):
    * ``stability`` (0.0–1.0): more stable = more inference timesteps.
      Maps to ``inference_timesteps`` offset (-4 at 0.0, +8 at 1.0).
      0.5 means "use the model_id preset's default steps".
    * ``similarity_boost`` (0.0–1.0): higher = stronger adherence to
      the reference voice. Maps to ``cfg_value`` offset (-0.3 at 0.0,
      +0.5 at 1.0). 0.5 = preset default.
    * ``style`` (0.0–1.0): emotional exaggeration. Currently
      forward-compatible (persisted on the request, no engine action
      yet — wires into style_tag selection in Dalga 2.6 follow-up).
    * ``use_speaker_boost`` (bool): clarity boost. Forward-compatible
      until VoxCPM2 exposes a matching flag.
    * ``speed`` (0.7–1.2): playback rate. Worker-side PCM resample;
      pitch will shift slightly at extremes (acceptable for voice in
      this range). Pitch-preserving time stretch is Dalga 2.6.
    * ``pitch`` (-12.0–+12.0 semitones): MiniMax-style pitch shift.
      Forward-compatible.

    All fields are optional — the gateway falls through to the
    ``model_id`` preset + voice catalog defaults when omitted.
    """
    model_config = ConfigDict(protected_namespaces=())

    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)
    use_speaker_boost: bool | None = Field(default=None)
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
    pitch: float | None = Field(default=None, ge=-12.0, le=12.0)


class TTSRequest(BaseModel):
    # `model_id` collides with pydantic v2's protected namespace; silence
    # the warning — this is intentional vendor parity (ElevenLabs and
    # MiniMax both use `model_id` as the preset selector).
    model_config = ConfigDict(protected_namespaces=())

    text: str = Field(..., min_length=1, max_length=20000)
    voice_id: str = Field(..., min_length=3, max_length=64)
    language: Literal["tr"] = "tr"
    audio_format: AudioFormat = "wav"
    # Faz B.5 Dalga 1.2 — preset knob (turbo / hd / character). Resolved
    # at the worker against `server.models.resolve_model`; unknown ids
    # surface as 400 from the gateway. None = registry default.
    model_id: str | None = Field(default=None, max_length=64)
    # Faz B.5 Dalga 2.1 — per-request voice tuning. See VoiceSettings
    # docstring for the field-by-field NQAI mapping.
    voice_settings: VoiceSettings | None = None


class TTSStreamRequest(TTSRequest):
    audio_format: StreamFormat = "wav"


# --------------------------------------------------------------------------- #
# Vendor-compat URL aliases — Dalga 2.2
# --------------------------------------------------------------------------- #
# ElevenLabs ships `POST /v1/text-to-speech/{voice_id}` and the
# corresponding `/stream` variant. SDKs they generate (Python, Node,
# Go) call those exact URLs. To let NEEKO/NIVA/NeuroCourse swap base
# URL and keep their code, we accept the same shape. `voice_id` lives
# in the URL path; the request body is the rest of `TTSRequest`
# WITHOUT the voice_id field.
class TTSAliasRequest(BaseModel):
    """Body for ``POST /v1/text-to-speech/{voice_id}``.

    Same fields as ``TTSRequest`` minus ``voice_id`` (URL-bound).
    """
    model_config = ConfigDict(protected_namespaces=())

    text: str = Field(..., min_length=1, max_length=20000)
    language: Literal["tr"] = "tr"
    audio_format: AudioFormat = "wav"
    model_id: str | None = Field(default=None, max_length=64)
    voice_settings: VoiceSettings | None = None


class TTSStreamAliasRequest(TTSAliasRequest):
    """Body for ``POST /v1/text-to-speech/{voice_id}/stream``."""
    audio_format: StreamFormat = "wav"


class VoicePublic(BaseModel):
    voice_id: str
    display_name: str
    language: str
    gender: str
    style_tags: list[str]
    reference_seconds: float
    source: str
    license: str
    visibility: Literal["private", "shared", "public"] = "private"
    created_at: str
    created_by: str


class VoiceListResponse(BaseModel):
    voices: list[VoicePublic]
    count: int


class EnrollResponse(BaseModel):
    voice: VoicePublic
    detail: str = "voice enrolled"


class DeleteResponse(BaseModel):
    voice_id: str
    detail: str = "voice deleted"


class ModelPublic(BaseModel):
    """One row in `GET /v1/models`. Mirrors the vendor pattern
    (ElevenLabs `/v1/models`, MiniMax model list) so clients can
    pick a `model_id` from a discoverable catalog instead of
    hard-coding strings."""
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    display_name: str
    description: str
    cfg_value: float
    inference_timesteps: int
    is_default: bool


class ModelListResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    models: list[ModelPublic]
    count: int
    default_model_id: str


class HealthResponse(BaseModel):
    status: Literal["ok", "warming", "degraded"]
    model_id: str
    device: str
    sample_rate: int
    loaded: bool
    voice_count: int
    version: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


# --------------------------------------------------------------------------- #
# Async TTS jobs — Stripe-style idempotent job model.
# --------------------------------------------------------------------------- #
class TTSJobParams(BaseModel):
    """Per-request override of engine knobs. Whitelisted fields only —
    arbitrary VoxCPM kwargs do not flow through here.
    """
    cfg_value: float | None = Field(default=None, ge=1.0, le=3.5)
    inference_timesteps: int | None = Field(default=None, ge=4, le=40)


class TTSJobCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    text: str = Field(..., min_length=1, max_length=20000)
    voice_id: str = Field(..., min_length=3, max_length=64)
    language: Literal["tr", "en"] = "tr"
    audio_format: AudioFormat = "wav"
    model_id: str | None = Field(default=None, max_length=64)
    voice_settings: VoiceSettings | None = None
    params: TTSJobParams | None = None


JobStatus = Literal["queued", "running", "complete", "failed"]


class TTSJobAccepted(BaseModel):
    job_id: str
    status: Literal["queued", "complete"]  # "complete" when an idempotent replay
    created_at: str
    deduplicated: bool = False


class TTSJobMetrics(BaseModel):
    queue_wait_ms: int | None = None
    inference_ms: int | None = None
    generated_audio_ms: int | None = None
    rtf: float | None = None


class TTSJobOutput(BaseModel):
    audio_url: str
    expires_at: str
    content_type: str = "audio/wav"


class TTSJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    error_code: str | None = None
    error_detail: str | None = None
    created_at: str
    metrics: TTSJobMetrics | None = None
    output: TTSJobOutput | None = None


