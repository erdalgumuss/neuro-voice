"""Unit tests for the TTS `model_id` preset registry.

The registry is the only source of truth for what `model_id` values
clients can pass; this file pins:
* default is exactly one entry,
* unknown ids raise UnknownModelError (gateway maps to 400),
* every registered preset has sane param ranges (so a typo in the
  registry can't silently slip a 0-step preset into production).
"""

from __future__ import annotations

import pytest

from server.models import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_ID,
    ModelPreset,
    UnknownModelError,
    list_models,
    resolve_model,
)


def test_default_model_resolved_when_id_is_none() -> None:
    assert resolve_model(None) is DEFAULT_MODEL
    assert resolve_model("") is DEFAULT_MODEL


def test_default_model_id_matches_default_preset() -> None:
    assert DEFAULT_MODEL.model_id == DEFAULT_MODEL_ID
    assert DEFAULT_MODEL.is_default is True


def test_exactly_one_default_in_registry() -> None:
    defaults = [p for p in list_models() if p.is_default]
    assert len(defaults) == 1, (
        f"multiple defaults make resolve_model(None) ambiguous: {defaults}"
    )


def test_unknown_model_id_raises() -> None:
    with pytest.raises(UnknownModelError) as ei:
        resolve_model("nqai-tts-flash-v3")
    msg = str(ei.value)
    assert "nqai-tts-flash-v3" in msg
    assert "available:" in msg


def test_all_known_ids_resolve() -> None:
    for preset in list_models():
        resolved = resolve_model(preset.model_id)
        assert resolved is preset, (
            f"resolve_model({preset.model_id!r}) returned wrong instance"
        )


@pytest.mark.parametrize("preset", list_models())
def test_preset_param_ranges_are_sane(preset: ModelPreset) -> None:
    """A registry entry with cfg=0 or steps=0 would silently break
    inference. Pin reasonable ranges so a future PR can't introduce
    nonsense numbers."""
    # Engine constructor accepts cfg_value > 0 in practice; production
    # presets sit between 1.0 and 3.5 (TTSJobParams bounds).
    assert 1.0 <= preset.cfg_value <= 3.5, (
        f"{preset.model_id}: cfg_value {preset.cfg_value} out of [1.0, 3.5]"
    )
    assert 4 <= preset.inference_timesteps <= 40, (
        f"{preset.model_id}: inference_timesteps "
        f"{preset.inference_timesteps} out of [4, 40]"
    )
    assert preset.model_id, "empty model_id"
    assert preset.display_name, "empty display_name"
    assert preset.description, "empty description"


def test_list_models_returns_immutable_tuple() -> None:
    """Returning a list would let callers mutate the registry; tuple
    catches the mistake at the type level + at runtime."""
    assert isinstance(list_models(), tuple)


def test_registry_has_turbo_hd_character_triplet() -> None:
    """The vendor parity story is exactly this triplet — turbo for
    low-latency, hd for default quality, character for max fidelity.
    Pin so a refactor can't drop one without an explicit decision."""
    ids = {p.model_id for p in list_models()}
    assert "nqai-voxcpm2-tr-turbo" in ids
    assert "nqai-voxcpm2-tr-hd" in ids
    assert "nqai-voxcpm2-tr-character" in ids


def test_turbo_is_faster_than_hd_is_faster_than_character() -> None:
    """Latency ordering invariant: fewer steps → faster wall time.
    `turbo.steps < hd.steps < character.steps`."""
    by_id = {p.model_id: p for p in list_models()}
    assert (
        by_id["nqai-voxcpm2-tr-turbo"].inference_timesteps
        < by_id["nqai-voxcpm2-tr-hd"].inference_timesteps
        < by_id["nqai-voxcpm2-tr-character"].inference_timesteps
    )
