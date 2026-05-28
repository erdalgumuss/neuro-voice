"""Voice catalog — filesystem-backed registry with thread-safe enroll/delete.

Layout on disk:
    configs/voices/<voice_id>.yaml          # manifest
    data/reference-audio/<voice_id>.wav     # reference audio (trimmed copy)

Manifest schema (v2, ADR-7 + ADR-10):
    schema_version: int     # exactly 2 in this codebase
    voice_id: str           # kebab-case, primary key
    display_name: str       # human-readable
    language: str           # ISO 639-1, "tr" default
    gender: str             # "neutral" | "female" | "male"
    style_tags: [str]       # ["warm", "narrative", "professional", ...]
    reference_audio: str    # filename inside reference_audio_dir
    reference_seconds: float
    source: str             # ADR-10 enum: "bootstrap" | "tenant-enroll" |
                            #              "talent-recorded" | "synthetic-from-prompt" |
                            #              "partner-import"
    license_kind: str       # ADR-10 enum: "example" | "synthetic" | "user-owned" |
                            #              "talent-contract" | "public-figure" |
                            #              "partner-licensed"
    license_ref: str | None # polymorphic: talent_contracts.id UUID, partner URL,
                            #              public-figure rationale, or null
    created_at: str         # ISO-8601 UTC
    created_by: str         # api_key prefix or "system"
    adapter: dict | None    # optional model adapter, e.g. {"type": "lora", "path": "..."}
    engine_params: dict | None  # optional per-voice inference knobs
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
    if "schema_version" not in raw:
        # No backwards-compat shell in v0.x — every manifest declares
        # the version it was authored against so future schema bumps
        # have a sharp boundary to refuse against.
        raise ManifestSchemaError(
            "voice manifest is missing required `schema_version` field "
            f"(expected {CURRENT_SCHEMA_VERSION})"
        )
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
    # ADR-10 — soft upgrade for manifests authored under the old
    # freeform `license: <str>` shape. The new field is `license_kind`,
    # with `license_ref` carrying any "talent-contract:<id>" suffix.
    # Unknown legacy values fold into `user-owned` so the manifest still
    # loads; an operator can re-pin the kind with a PATCH.
    if "license_kind" not in out and "license" in out:
        legacy = out.pop("license")
        if isinstance(legacy, str) and legacy.startswith("talent-contract:"):
            out["license_kind"] = "talent-contract"
            out["license_ref"] = legacy.split(":", 1)[1] or None
        elif legacy in {"example", "synthetic", "user-owned",
                        "talent-contract", "public-figure",
                        "partner-licensed"}:
            out["license_kind"] = legacy
        else:
            out["license_kind"] = "user-owned"
    out.setdefault("license_ref", None)
    return out


VOICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}

CURRENT_SCHEMA_VERSION = 2
"""Voice manifest schema version handled by this codebase. v2 added
optional `lexicon`, `watermark`, `eval_pin`, and `base_model_id` fields;
v1 manifests (no `schema_version` key) are no longer accepted in v0.x
since there is no backwards-compat shell to bridge them."""


class ManifestSchemaError(ValueError):
    """Raised when a voice manifest declares an unsupported schema_version."""


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
    license_kind: str
    license_ref: str | None
    created_at: str
    created_by: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    adapter: dict[str, Any] | None = None
    engine_params: dict[str, Any] | None = None
    # v2 optional forward-shape fields. They sit unpopulated on bundled
    # example voices and on freshly enrolled user voices, and get filled
    # in by later ADRs as the production voice lifecycle wires up.
    base_model_id: str | None = None
    """Engine baseline this voice is pinned to (e.g. ``voxcpm2-tr-hd``).
    NULL means "use the request-time default"; populated rows let the
    model_id preset be tracked across catalog migrations."""
    lexicon: dict[str, Any] | None = None
    """Per-voice pronunciation overlay, layered on top of the language
    pack's lexicon when the worker normalises text. Schema TBD."""
    watermark: dict[str, Any] | None = None
    """Inaudible audio watermark configuration. ``key_id`` references an
    operator-managed key; the worker stamps every generated chunk."""
    eval_pin: dict[str, Any] | None = None
    """Snapshot of the eval baseline that certified this voice
    (``test_set``, ``metrics``, ``evaluated_at``). Lets a regression run
    months later compare apples to apples even after model drift."""

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_SCHEMA_VERSION:
            raise ManifestSchemaError(
                f"voice {self.voice_id!r} declares schema_version="
                f"{self.schema_version}; this codebase requires "
                f"schema_version={CURRENT_SCHEMA_VERSION}. Update the "
                "manifest or pin an older release."
            )

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("reference_audio", None)
        d.pop("adapter", None)
        d.pop("engine_params", None)
        return d

    def reference_path(self, base: Path) -> Path:
        return base / self.reference_audio

    def to_manifest(self) -> dict[str, Any]:
        """Full YAML manifest representation, omitting unset optional fields."""
        return {k: v for k, v in asdict(self).items() if v is not None}


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
        source: str = "tenant-enroll",
        license_kind: str = "user-owned",
        license_ref: str | None = None,
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
                license_kind=license_kind,
                license_ref=license_ref,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by=created_by,
            )
            manifest_path = self.voices_dir / f"{voice_id}.yaml"
            manifest_path.write_text(
                yaml.safe_dump(voice.to_manifest(), sort_keys=False, allow_unicode=True),
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
