"""Runtime configuration — env-driven, validated at import."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(os.path.expandvars(raw)).expanduser().resolve() if raw else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [t.strip() for t in raw.split(",") if t.strip()]


@dataclass(frozen=True)
class Settings:
    repo_root: Path = field(default_factory=_repo_root)
    voices_dir: Path = field(default_factory=lambda: _env_path(
        "NQAI_VOICES_DIR", _repo_root() / "configs" / "voices"
    ))
    reference_audio_dir: Path = field(default_factory=lambda: _env_path(
        "NQAI_REFERENCE_DIR", _repo_root() / "data" / "reference-audio"
    ))
    model_id: str = field(default_factory=lambda: os.environ.get(
        "NQAI_MODEL_ID", "openbmb/VoxCPM2"
    ))
    lora_path: Path | None = field(default_factory=lambda: _env_path(
        "NQAI_LORA_PATH", Path(os.environ["NQAI_LORA_PATH"])
    ) if os.environ.get("NQAI_LORA_PATH") else None)
    lora_config_path: Path | None = field(default_factory=lambda: _env_path(
        "NQAI_LORA_CONFIG_PATH", Path(os.environ["NQAI_LORA_CONFIG_PATH"])
    ) if os.environ.get("NQAI_LORA_CONFIG_PATH") else None)
    device: str = field(default_factory=lambda: os.environ.get("NQAI_DEVICE", "auto"))
    optimize: bool = field(default_factory=lambda: _env_bool("NQAI_OPTIMIZE", False))
    cfg_value: float = field(default_factory=lambda: float(
        os.environ.get("NQAI_CFG_VALUE", "2.0")
    ))
    inference_timesteps: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_INFERENCE_TIMESTEPS", "10")
    ))
    max_chars_per_request: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_MAX_CHARS", "4000")
    ))
    reference_trim_seconds: float = field(default_factory=lambda: float(
        os.environ.get("NQAI_REF_TRIM_SECONDS", "15.0")
    ))
    # VoxCPM2 accepts 16 kHz reference audio; output is 48 kHz via AudioVAE V2.
    reference_sample_rate: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_REF_SR", "16000")
    ))
    target_sample_rate: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_TARGET_SR", "48000")
    ))
    api_keys: list[str] = field(default_factory=lambda: _env_list("NQAI_API_KEYS", []))
    require_auth: bool = field(default_factory=lambda: _env_bool("NQAI_REQUIRE_AUTH", True))
    # CORS allow-list. Default targets local dev (gateway + an SPA on
    # 5173). Production deployments MUST set NQAI_CORS_ORIGINS to the
    # actual admin SPA origin. Wildcard "*" is incompatible with
    # `allow_credentials=True` (Starlette silently drops credentials)
    # which would re-break the admin cookie flow we just fixed; if you
    # need wildcard, accept that admin cookies won't work cross-origin.
    cors_origins: list[str] = field(default_factory=lambda: _env_list(
        "NQAI_CORS_ORIGINS",
        ["http://localhost:8000", "http://127.0.0.1:8000",
         "http://localhost:5173", "http://127.0.0.1:5173"],
    ))
    enroll_max_upload_mb: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_ENROLL_MAX_MB", "20")
    ))
    # Faz B.5 Dalga 2.5 — minimum trimmed reference duration accepted on
    # POST /v1/voices. ElevenLabs/MiniMax enforce ≥10s; we default to 1.0s
    # so test fixtures (1-second tones) keep working and operators ramp
    # the floor via NQAI_ENROLL_MIN_SECONDS once they wire real FSEK
    # talent consent in production. Anything below the trimmed window
    # (0.5–1.0s) usually produces unstable clones, but the floor is a
    # business policy, not a model contract.
    enroll_min_seconds: float = field(default_factory=lambda: float(
        os.environ.get("NQAI_ENROLL_MIN_SECONDS", "1.0")
    ))
    # Per-tenant aggregate cap (sum across all keys in the tenant). Acts as
    # an upper bound the operator can lift via DB; per-key limits in
    # api_keys.rate_limit_per_minute are independent and checked first.
    tenant_rate_limit_per_minute: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_TENANT_RATE_LIMIT_PER_MINUTE", "600")
    ))


settings = Settings()
