"""Voice catalog — filesystem-backed registry with thread-safe enroll/delete.

Layout on disk:
    configs/voices/<voice_id>.yaml          # manifest
    data/reference-audio/<voice_id>.wav     # reference audio (trimmed copy)

Manifest schema (v0):
    voice_id: str           # kebab-case, primary key
    display_name: str       # human-readable
    language: str           # ISO 639-1, "tr" default
    gender: str             # "neutral" | "female" | "male"
    style_tags: [str]       # ["warm", "child-directed", ...]
    reference_audio: str    # filename inside reference_audio_dir
    reference_seconds: float
    source: str             # "elevenlabs" | "voice-talent" | "user-enroll" | "synthetic"
    license: str            # "internal-bridge" | "talent-contract:<id>" | "user-owned"
    created_at: str         # ISO-8601 UTC
    created_by: str         # api_key prefix or "system"
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _normalize_manifest_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce types that PyYAML auto-parses (datetime, date, int) into the
    string / list forms the `Voice` dataclass expects.

    Manifests are edited by humans in YAML, where `2026-05-19T20:17:18+00:00`
    parses as a `datetime` and an unquoted `2.4` parses as a float. The
    catalog layer keeps everything as JSON-friendly primitives so the public
    Pydantic schema is straightforward and so round-tripping through
    yaml.safe_dump produces the same file.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"manifest root must be a mapping, got {type(raw).__name__}")
    out = dict(raw)
    for key in ("created_at",):
        v = out.get(key)
        if isinstance(v, (datetime, date)):
            out[key] = v.isoformat()
    tags = out.get("style_tags")
    if tags is None:
        out["style_tags"] = []
    elif isinstance(tags, str):
        out["style_tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    return out


VOICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


class VoiceNotFound(LookupError):
    pass


class VoiceAlreadyExists(ValueError):
    pass


class InvalidVoiceId(ValueError):
    pass


@dataclass
class Voice:
    voice_id: str
    display_name: str
    language: str
    gender: str
    style_tags: list[str]
    reference_audio: str
    reference_seconds: float
    source: str
    license: str
    created_at: str
    created_by: str

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("reference_audio", None)
        return d

    def reference_path(self, base: Path) -> Path:
        return base / self.reference_audio


def validate_voice_id(voice_id: str) -> str:
    if not VOICE_ID_PATTERN.match(voice_id):
        raise InvalidVoiceId(
            f"voice_id '{voice_id}' invalid — kebab-case [a-z0-9-], 3-64 chars, "
            f"start/end with alphanumeric"
        )
    return voice_id


class VoiceRegistry:
    """Filesystem-backed voice registry with in-memory cache + RLock."""

    def __init__(self, voices_dir: Path, reference_dir: Path) -> None:
        self.voices_dir = voices_dir
        self.reference_dir = reference_dir
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.reference_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, Voice] = {}
        self._loaded = False

    def _load_all(self) -> None:
        with self._lock:
            self._cache.clear()
            for manifest_path in sorted(self.voices_dir.glob("*.yaml")):
                try:
                    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                    raw = _normalize_manifest_dict(raw)
                    voice = Voice(**raw)
                    self._cache[voice.voice_id] = voice
                except (yaml.YAMLError, TypeError, ValueError) as e:
                    raise RuntimeError(
                        f"Voice manifest {manifest_path} is malformed: {e}"
                    ) from e
            self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_all()

    def list_voices(self) -> list[Voice]:
        self._ensure_loaded()
        with self._lock:
            return sorted(self._cache.values(), key=lambda v: v.voice_id)

    def get(self, voice_id: str) -> Voice:
        validate_voice_id(voice_id)
        self._ensure_loaded()
        with self._lock:
            if voice_id not in self._cache:
                raise VoiceNotFound(voice_id)
            return self._cache[voice_id]

    def enroll(
        self,
        voice_id: str,
        display_name: str,
        reference_audio_bytes: bytes,
        reference_audio_suffix: str,
        *,
        language: str = "tr",
        gender: str = "neutral",
        style_tags: list[str] | None = None,
        source: str = "user-enroll",
        license: str = "user-owned",
        created_by: str = "system",
        reference_trim_seconds: float = 15.0,
        target_sample_rate: int = 16000,
    ) -> Voice:
        validate_voice_id(voice_id)
        suffix = reference_audio_suffix.lower()
        if not suffix.startswith("."):
            suffix = "." + suffix
        if suffix not in ALLOWED_AUDIO_SUFFIXES:
            raise ValueError(f"audio suffix '{suffix}' not allowed; use {ALLOWED_AUDIO_SUFFIXES}")

        self._ensure_loaded()
        with self._lock:
            if voice_id in self._cache:
                raise VoiceAlreadyExists(voice_id)

            from .audio_io import trim_and_resample_to_wav

            ref_filename = f"{voice_id}.wav"
            ref_path = self.reference_dir / ref_filename
            duration_seconds = trim_and_resample_to_wav(
                src_bytes=reference_audio_bytes,
                src_suffix=suffix,
                dst_path=ref_path,
                trim_seconds=reference_trim_seconds,
                target_sr=target_sample_rate,
            )

            voice = Voice(
                voice_id=voice_id,
                display_name=display_name,
                language=language,
                gender=gender,
                style_tags=style_tags or [],
                reference_audio=ref_filename,
                reference_seconds=duration_seconds,
                source=source,
                license=license,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by=created_by,
            )
            manifest_path = self.voices_dir / f"{voice_id}.yaml"
            manifest_path.write_text(
                yaml.safe_dump(asdict(voice), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            self._cache[voice_id] = voice
            return voice

    def delete(self, voice_id: str) -> None:
        validate_voice_id(voice_id)
        self._ensure_loaded()
        with self._lock:
            if voice_id not in self._cache:
                raise VoiceNotFound(voice_id)
            voice = self._cache.pop(voice_id)
            manifest_path = self.voices_dir / f"{voice_id}.yaml"
            manifest_path.unlink(missing_ok=True)
            ref_path = self.reference_dir / voice.reference_audio
            if ref_path.is_file():
                ref_path.unlink()

    def to_json_summary(self) -> str:
        return json.dumps(
            [v.to_public() for v in self.list_voices()],
            ensure_ascii=False,
            indent=2,
        )
