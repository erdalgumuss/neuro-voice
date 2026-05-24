"""PCM post-processing helpers — worker-side voice_settings application.

The engine yields raw PCM int16 at its native rate. Per-request voice
tuning that doesn't fit inside the engine's own knobs (cfg_value /
inference_timesteps) lands here as a thin numpy pass before
`publish_chunk` writes to the result stream.

Cleanest split:
* Engine knobs (model_id presets, stability, similarity_boost) →
  `engine_overrides` dict, applied at `model.generate(...)` time.
* PCM post-process (speed, future pitch shift, future loudness norm) →
  this module, applied per-chunk between the engine and publish.

`speed` is the only one wired today; pitch and loudness-norm are
documented as future hooks. Each helper is a no-op when its input
parameter is None or the identity value, so callers can apply the
whole pipeline unconditionally and the cost stays zero when nothing
is requested.
"""

from __future__ import annotations

import numpy as np


def apply_speed(
    pcm_int16: bytes,
    *,
    sample_rate: int,
    speed: float | None,
) -> bytes:
    """Linear-interp resample to ``1 / speed`` length.

    `speed=1.0` (or `None`) → input returned unchanged.
    `speed > 1.0` → fewer samples, audio plays back faster.
    `speed < 1.0` → more samples, audio plays back slower.

    Linear interp is voice-grade for the schema-bounded 0.7–1.2x
    range — pitch drifts slightly but acoustically the result is what
    ElevenLabs/MiniMax users expect from a `speed` knob. For
    pitch-preserving time stretch see Dalga 2.6 (librosa /
    rubberband — adds a real dep, deferred until measured demand).

    Empty input → empty output. Engine-yielded chunks may be empty in
    the inter-segment silence path; protect against the resample math
    blowing up on length 0.
    """
    if speed is None or abs(speed - 1.0) < 1e-3:
        return pcm_int16
    if not pcm_int16:
        return pcm_int16
    if speed <= 0:
        raise ValueError(f"speed must be > 0, got {speed}")

    arr = np.frombuffer(pcm_int16, dtype=np.int16)
    if arr.size == 0:
        return pcm_int16

    new_len = max(1, int(round(arr.size / speed)))
    # np.interp wants float coords. We map [0, len-1] to new_len evenly
    # spaced points; `np.interp` clamps + linearly interpolates.
    idx_new = np.linspace(0, arr.size - 1, new_len, dtype=np.float64)
    resampled = np.interp(
        idx_new,
        np.arange(arr.size, dtype=np.float64),
        arr.astype(np.float32),
    )
    # Clip back into int16 range (linear interp can't generate larger
    # magnitudes than the input, but be defensive against floating
    # point drift at the boundary).
    resampled = np.clip(resampled, -32768, 32767)
    return resampled.astype(np.int16).tobytes()


def apply_voice_settings(
    pcm_int16: bytes,
    *,
    sample_rate: int,
    voice_settings: dict | None,
) -> bytes:
    """Apply all PCM-side transforms from a voice_settings dict in
    one call. Currently only `speed`; other fields (pitch, loudness)
    are documented hooks for Dalga 2.6.

    Returns the input unchanged when ``voice_settings`` is None / empty
    / has no PCM-relevant fields. This is the zero-cost fast path the
    caller takes when no voice_settings were sent."""
    if not voice_settings:
        return pcm_int16
    speed = voice_settings.get("speed")
    return apply_speed(pcm_int16, sample_rate=sample_rate, speed=speed)


__all__ = ["apply_speed", "apply_voice_settings"]
