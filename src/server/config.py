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
    return Path(raw).expanduser().resolve() if raw else default


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
    device: str = field(default_factory=lambda: os.environ.get("NQAI_DEVICE", "auto"))
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
    cors_origins: list[str] = field(default_factory=lambda: _env_list(
        "NQAI_CORS_ORIGINS", ["*"]
    ))
    enroll_max_upload_mb: int = field(default_factory=lambda: int(
        os.environ.get("NQAI_ENROLL_MAX_MB", "20")
    ))


settings = Settings()
