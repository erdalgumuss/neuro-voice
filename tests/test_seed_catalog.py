"""Launch voice catalog invariants."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed_voices() -> list[dict]:
    raw = yaml.safe_load((REPO_ROOT / "configs" / "seed_voices.yaml").read_text())
    return raw["voices"]


def test_launch_catalog_has_exact_five_voice_distribution() -> None:
    voices = _seed_voices()
    ids = [v["voice_id"] for v in voices]

    assert len(voices) == 5
    assert len(set(ids)) == 5
    assert sum(v.startswith("neeko-") for v in ids) == 1
    assert sum(v.startswith("niva-") for v in ids) == 2
    assert sum(v.startswith("neurocourse-") for v in ids) == 2
    assert all(not v.startswith("naro-") for v in ids)
    assert all("sandbox" not in v for v in ids)


def test_launch_catalog_entries_have_reference_audio_and_license() -> None:
    for voice in _seed_voices():
        assert voice["language"] == "tr"
        assert voice["reference_audio"]
        assert "/" not in voice["reference_audio"]
        assert voice["source"]
        assert voice["license"]
        assert voice["style_tags"]
