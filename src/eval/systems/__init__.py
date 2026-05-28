"""TTS system abstraction for the eval harness.

A `TTSSystem` synthesizes a test sentence into int16 PCM bytes. The
runner doesn't care HOW (HTTP call to NeuroVoice, REST call to
ElevenLabs, local synthesis call) — only that it gets back audio that
the metric backends can score.

The reason for this abstraction is the most important number in the
audit: a side-by-side table of "NeuroVoice VoxCPM2 vs ElevenLabs vs
MiniMax vs OpenAI" on the same test set. Without a system-level
abstraction each comparison is a one-off script that drifts. With it,
adding a new vendor is one file in `src/eval/systems/`.

Reproducibility contract: every system MUST return a `SystemMetadata`
dict alongside its PCM (model id, voice id, latency, version). The
report writer pins these in the markdown header so re-runs years later
are interpretable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SystemMetadata:
    """Per-call provenance. Pinned in the report header — without it
    the report is "VoxCPM2-hd vs ElevenLabs" without a recoverable
    "which preset, which voice, when, how long" trail."""

    system: str             # canonical name: "neurovoice", "elevenlabs", ...
    model_id: str           # vendor preset / model identifier
    voice_id: str
    elapsed_ms: int
    extra: dict[str, object] | None = None


@dataclass(frozen=True)
class SystemOutput:
    pcm_int16: bytes
    sample_rate: int
    metadata: SystemMetadata


class TTSSystem(Protocol):
    """The harness contract."""

    name: str

    def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
    ) -> SystemOutput: ...


# Registry shared with the CLI — concrete adapters register themselves
# explicitly from `scripts/eval_run.py` so we don't pull a real HTTP
# client into the import path of `tests/test_eval_harness.py`.
_SYSTEMS: dict[str, TTSSystem] = {}


def register_system(name: str, system: TTSSystem) -> None:
    _SYSTEMS[name] = system


def get_system(name: str) -> TTSSystem:
    if name not in _SYSTEMS:
        raise KeyError(
            f"system '{name}' not registered. Known: {sorted(_SYSTEMS)}"
        )
    return _SYSTEMS[name]


def list_systems() -> list[str]:
    return sorted(_SYSTEMS)
