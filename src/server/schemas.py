"""Pydantic schemas for the v1 HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# --------------------------------------------------------------------------- #
# Faz B.5 Dalga 2.6 — context + pronunciation + seed (vendor parity)
# --------------------------------------------------------------------------- #
# Shared pronunciation_dict bounds. Centralised so every request shape
# enforces the same envelope and the worker never has to defend against
# unbounded text-frontend work.
_PRON_DICT_MAX_ENTRIES = 64
_PRON_DICT_MAX_KEY_LEN = 64
_PRON_DICT_MAX_VAL_LEN = 64


def _validate_pronunciation_dict(
    value: dict[str, str] | None,
) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > _PRON_DICT_MAX_ENTRIES:
        raise ValueError(
            f"pronunciation_dict has {len(value)} entries; "
            f"max {_PRON_DICT_MAX_ENTRIES}"
        )
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("pronunciation_dict keys and values must be strings")
        if not k.strip():
            raise ValueError("pronunciation_dict keys must be non-empty")
        if len(k) > _PRON_DICT_MAX_KEY_LEN:
            raise ValueError(
                f"pronunciation_dict key '{k[:16]}…' exceeds "
                f"{_PRON_DICT_MAX_KEY_LEN} chars"
            )
        if len(v) > _PRON_DICT_MAX_VAL_LEN:
            raise ValueError(
                f"pronunciation_dict value for '{k}' exceeds "
                f"{_PRON_DICT_MAX_VAL_LEN} chars"
            )
    return value



# ElevenLabs ships `seed`, `previous_text`, `next_text`,
# `pronunciation_dictionary_locators`; MiniMax ships `seed` + inline
# pronunciation. We accept the union so SDKs that already pass these
# fields keep working. Where the engine doesn't act on a field yet, it
# is forward-compatible (validated + persisted in audit log, no engine
# action) and the docstring says so explicitly.


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
    # Faz B.5 Dalga 2.6 — best-effort determinism. Seeded torch RNG
    # at the worker just before `model.generate()`. Same seed +
    # same text + same voice + same engine knobs → same waveform
    # within a model build; cross-build replays are not guaranteed.
    # Constrained to signed 31-bit so it round-trips through JSON
    # safely on every SDK.
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    # Faz B.5 Dalga 2.6 — surrounding-context hints (ElevenLabs-style).
    # Today they are forward-compat: validated, persisted on the job
    # payload, surfaced in the audit log for downstream learning,
    # but the worker does not yet thread them into the model context
    # window. Wires into a prosody-continuity pass when the engine
    # exposes a sliding text buffer; clients can already start
    # sending them so audiobook / long-doc flows light up
    # automatically when that ships.
    previous_text: str | None = Field(default=None, max_length=4000)
    next_text: str | None = Field(default=None, max_length=4000)
    # Faz B.5 Dalga 2.6 — per-request pronunciation override map.
    # Concrete behavior: every key is treated as a whole-word case-
    # insensitive substitution applied in the Turkish frontend
    # BEFORE the built-in code-mix lexicon, so a tenant can correct
    # brand pronunciations on a per-request basis without touching
    # the global lexicon. Capped at 64 entries × 64 chars each to
    # bound text-frontend work.
    pronunciation_dict: dict[str, str] | None = Field(default=None)

    @field_validator("pronunciation_dict")
    @classmethod
    def _validate_pron(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_pronunciation_dict(v)


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
    # Dalga 2.6 fields — same semantics as TTSRequest.
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    previous_text: str | None = Field(default=None, max_length=4000)
    next_text: str | None = Field(default=None, max_length=4000)
    pronunciation_dict: dict[str, str] | None = Field(default=None)

    @field_validator("pronunciation_dict")
    @classmethod
    def _validate_pron(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_pronunciation_dict(v)


class TTSStreamAliasRequest(TTSAliasRequest):
    """Body for ``POST /v1/text-to-speech/{voice_id}/stream``."""
    audio_format: StreamFormat = "wav"


class VoicePublic(BaseModel):
    """Voice catalog entry. Fields added in Faz B.5 Dalga 2.4 are
    optional so old enrolled voices stay representable."""
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
    # Dalga 2.4 — vendor-parity metadata
    description: str | None = None
    labels: list[str] | None = None
    preview_url: str | None = None
    voice_settings_defaults: VoiceSettings | None = None


class VoiceListResponse(BaseModel):
    voices: list[VoicePublic]
    count: int
    # Faz B.5 Dalga 2.4 — pagination cursors. Cheap to add now so
    # clients don't have to migrate when catalogs grow past one page.
    limit: int | None = None
    offset: int | None = None
    total: int | None = None


class VoiceUpdateRequest(BaseModel):
    """Body for ``PATCH /v1/voices/{voice_id}`` — owner-only voice
    metadata edits. All fields optional; only the provided fields
    are written. Reference audio + voice_id slug are immutable here
    (re-enroll for those)."""
    model_config = ConfigDict(protected_namespaces=())

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    labels: list[str] | None = None
    preview_url: str | None = Field(default=None, max_length=2048)
    voice_settings_defaults: VoiceSettings | None = None
    style_tags: list[str] | None = None
    visibility: Literal["private", "shared", "public"] | None = None


class EnrollResponse(BaseModel):
    """Faz B.5 Dalga 2.5 — first-class voice clone response.

    `requires_verification` mirrors ElevenLabs IVC: when True, the
    voice is enrolled but the platform expects the operator to confirm
    talent consent + KVKK / FSEK rider before the voice flows into
    production synthesis. Today this is purely an audit flag the
    catalog stores alongside the row; in Faz 3 (governance layer) it
    gates `release_status='production'` transitions. Defaults to False
    when the caller passes `voice_talent_consent=true` on enroll —
    same shape vendors use.
    """
    voice: VoicePublic
    requires_verification: bool = False
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

    # Faz B.5 Dalga 3.2 — async surface accepts long-form text.
    # ElevenLabs / MiniMax async paths take 100k–1M chars (full books).
    # The schema ceiling is generous (250 000 chars ≈ ~5h playback at
    # 14 chars/sec read rate); a tighter, env-tunable runtime cap
    # (NQAI_ASYNC_MAX_CHARS, default 100 000) is enforced in the
    # gateway so operators can lift it per deployment without a code
    # change. Sync `/v1/tts` stays at 4 000 chars (config.max_chars_per_request)
    # because the gateway → result-stream → response timeout (30s default)
    # would 504 long before long-form finishes.
    text: str = Field(..., min_length=1, max_length=250000)
    voice_id: str = Field(..., min_length=3, max_length=64)
    language: Literal["tr", "en"] = "tr"
    audio_format: AudioFormat = "wav"
    model_id: str | None = Field(default=None, max_length=64)
    voice_settings: VoiceSettings | None = None
    params: TTSJobParams | None = None
    # Dalga 2.6 fields — same semantics as TTSRequest.
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    previous_text: str | None = Field(default=None, max_length=4000)
    next_text: str | None = Field(default=None, max_length=4000)
    pronunciation_dict: dict[str, str] | None = Field(default=None)

    @field_validator("pronunciation_dict")
    @classmethod
    def _validate_pron(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_pronunciation_dict(v)


JobStatus = Literal["queued", "running", "complete", "failed"]


class TTSJobAccepted(BaseModel):
    job_id: str
    status: Literal["queued", "complete"]  # "complete" when an idempotent replay
    created_at: str
    deduplicated: bool = False


class TTSJobMetrics(BaseModel):
    """Latency + billing metadata in the async job status response.

    Faz B.5 Dalga 2.3 — fields added so the response shape lines up
    with ElevenLabs raw-header metadata + MiniMax `extra_info` body.
    All fields nullable — the worker may not have written some yet
    (e.g. a job that errored before inference).
    """
    model_config = ConfigDict(protected_namespaces=())

    queue_wait_ms: int | None = None
    inference_ms: int | None = None
    # `first_audio_ms`: worker-side inference-start → first-publish_chunk
    # XADD. Surfaced on the API now that the streaming endpoint also
    # measures it (Faz C v1 item 1 wired the column; this just
    # re-exposes it on the async status response).
    first_audio_ms: int | None = None
    generated_audio_ms: int | None = None
    rtf: float | None = None
    # Billing primary key — character count of the original text.
    # Same value the gateway puts on the `X-NQAI-Character-Count`
    # header for sync paths.
    character_count: int | None = None
    # Preset that actually ran (registry-default when client sent None).
    model_id: str | None = None


class TTSJobOutput(BaseModel):
    audio_url: str
    expires_at: str
    content_type: str = "audio/wav"


class SentenceAlignment(BaseModel):
    """Faz B.5 Dalga 3.2 — one row of the per-sentence alignment list
    returned with long-form jobs. Timestamps are PLAYBACK milliseconds
    relative to the start of the rendered audio, so a client can map a
    scrub-bar position straight to a sentence."""
    seq: int
    start_ms: int
    end_ms: int
    text: str


class TTSJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    error_code: str | None = None
    error_detail: str | None = None
    created_at: str
    metrics: TTSJobMetrics | None = None
    output: TTSJobOutput | None = None
    # Faz B.5 Dalga 3.2 — only present on completed long-form jobs.
    # Short jobs (or pre-Dalga-3.2 rows) leave this NULL so the response
    # payload doesn't balloon for the common single-sentence case.
    alignment: list[SentenceAlignment] | None = None


