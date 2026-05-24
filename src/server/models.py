"""TTS model preset registry — `model_id` knob (Faz B.5 Dalga 1.2).

ElevenLabs and MiniMax both expose a `model_id` request field that
selects among low-latency / standard / expressive variants. We have
ONE underlying base model (VoxCPM2 multilingual) so our `model_id`
maps to **quality/latency presets** on the same engine — different
`cfg_value` + `inference_timesteps` combos — rather than separate
weight files.

This is honest naming: we're not pretending to ship 5 different
trained models. We're surfacing 3 well-tuned operating points on the
single base, picked so the latency/quality tradeoff lines up with what
NEEKO (low-latency mobile playback), NeuroCourse (HD long-form), and
NIVA (character voice consistency) actually need.

Pre-fix, the only knob was per-voice `engine_params` baked into the
voice catalog at enrollment time — a request couldn't ask the same
voice to run in turbo vs HD mode. With `model_id`, the SAME enrolled
voice can be driven through different presets per request.

Resolution order at request time:
    explicit `params.cfg_value` / `params.inference_timesteps`
    > model_id preset
    > voice.engine_params default
    > engine constructor default
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPreset:
    """One row in the model registry.

    `cfg_value` controls how strongly the model adheres to the
    reference voice timbre; higher = more faithful but slower.
    `inference_timesteps` is the diffusion sampler step count;
    higher = better audio quality but linearly slower wall time.

    The combos here are calibrated empirically on VoxCPM2 multilingual
    against Turkish reference voices. Numbers will move once the
    Faz C v1 latency_bench operator tour produces hardware-specific
    data — treat them as starting points, not commitments.
    """

    model_id: str
    display_name: str
    description: str
    cfg_value: float
    inference_timesteps: int
    is_default: bool = False


_PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset(
        model_id="nqai-voxcpm2-tr-turbo",
        display_name="NQAI VoxCPM2 Turkish — Turbo",
        description=(
            "Lowest-latency preset. Fewer diffusion steps + lower CFG. "
            "Good for real-time conversation / quick UI feedback. "
            "Slight tradeoff in timbre fidelity vs HD."
        ),
        cfg_value=1.5,
        inference_timesteps=8,
    ),
    ModelPreset(
        model_id="nqai-voxcpm2-tr-hd",
        display_name="NQAI VoxCPM2 Turkish — HD",
        description=(
            "Default. Balanced latency/quality. Production target for "
            "NEEKO storytelling + NeuroCourse instructor reads."
        ),
        cfg_value=2.0,
        inference_timesteps=16,
        is_default=True,
    ),
    ModelPreset(
        model_id="nqai-voxcpm2-tr-character",
        display_name="NQAI VoxCPM2 Turkish — Character",
        description=(
            "Highest CFG + more steps. Maximum reference-voice fidelity, "
            "best for character voices where consistency across sentences "
            "matters more than first-byte speed. Slowest preset."
        ),
        cfg_value=2.5,
        inference_timesteps=24,
    ),
)


_BY_ID: dict[str, ModelPreset] = {p.model_id: p for p in _PRESETS}
_DEFAULTS = [p for p in _PRESETS if p.is_default]
if len(_DEFAULTS) != 1:
    raise RuntimeError(
        "exactly one preset must be marked is_default=True; "
        f"found {len(_DEFAULTS)}"
    )
DEFAULT_MODEL: ModelPreset = _DEFAULTS[0]
DEFAULT_MODEL_ID: str = DEFAULT_MODEL.model_id


class UnknownModelError(ValueError):
    """Caller asked for a `model_id` not in the registry. Surface as
    400 from the HTTP layer."""


def resolve_model(model_id: str | None) -> ModelPreset:
    """Look up a preset by id; return the default when `model_id` is
    None. Raises UnknownModelError if a non-empty value doesn't match
    any registered preset — pydantic on the request schema cannot do
    this enum check because the registry is dynamic."""
    if not model_id:
        return DEFAULT_MODEL
    preset = _BY_ID.get(model_id)
    if preset is None:
        raise UnknownModelError(
            f"unknown model_id {model_id!r}; available: "
            f"{sorted(_BY_ID.keys())}"
        )
    return preset


def list_models() -> tuple[ModelPreset, ...]:
    """Return the full registry. Backing for `GET /v1/models`."""
    return _PRESETS


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MODEL_ID",
    "ModelPreset",
    "UnknownModelError",
    "list_models",
    "resolve_model",
]
