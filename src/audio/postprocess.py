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


def apply_dc_offset_removal(pcm_int16: bytes) -> bytes:
    """Subtract per-buffer mean to remove DC offset.

    A DC offset (non-zero mean) on int16 PCM compounds with sentence
    boundary discontinuities (see ``crossfade_concat``) to produce
    audible clicks/pops. Removing it costs one pass over the buffer.

    Single-pass via numpy ``arr - arr.mean()``; result is cast back to
    int16 with hard clip to the [-32768, 32767] range (defensive — for
    a real DC offset of a few hundred LSBs the cast cannot overflow,
    but a buffer near full-scale could in pathological inputs).

    Empty / odd-length inputs return unchanged (matches the contract
    of the other helpers in this module).
    """
    if not pcm_int16 or len(pcm_int16) < 2:
        return pcm_int16
    arr = np.frombuffer(pcm_int16, dtype=np.int16)
    if arr.size == 0:
        return pcm_int16
    mean = float(arr.mean())
    if abs(mean) < 0.5:  # nothing to do; <1 LSB drift is rounding noise
        return pcm_int16
    centred = arr.astype(np.float64) - mean
    centred = np.clip(centred, -32768, 32767)
    return centred.astype(np.int16).tobytes()


def apply_peak_limit(
    pcm_int16: bytes,
    *,
    ceiling_db: float = -0.5,
) -> bytes:
    """Clip output to a true-peak ceiling expressed in dBTP.

    ``ceiling_db=-0.5`` (default) → max ``|sample| ≤ 10**(-0.5/20) * 32767``
    ≈ 30923. Provides ~0.5 dB headroom against the int16 full-scale,
    eliminating saturated samples without audible loudness loss.

    v0 simplification: per-sample hard clip (no look-ahead, no soft knee).
    Soft-knee + look-ahead refinements deferred until the eval suite
    surfaces an audible regression from the hard clip.
    """
    if not pcm_int16 or len(pcm_int16) < 2:
        return pcm_int16
    arr = np.frombuffer(pcm_int16, dtype=np.int16)
    if arr.size == 0:
        return pcm_int16
    ceiling = int(round(10 ** (ceiling_db / 20.0) * 32767))
    # Early exit: if the buffer is already inside the ceiling there
    # is nothing to do — return the input unchanged so callers that
    # rely on identity ("is" check) see a fast pass-through.
    abs_max = int(np.abs(arr).max())
    if abs_max <= ceiling:
        return pcm_int16
    # Symmetric clip — int16 negative-most is -32768 but we cap at
    # -ceiling for true-peak parity with the positive side.
    clipped = np.clip(arr, -ceiling, ceiling)
    return clipped.astype(np.int16).tobytes()


def crossfade_concat(
    prev_pcm_int16: bytes,
    next_pcm_int16: bytes,
    *,
    sample_rate: int,
    fade_ms: int = 4,
    gap_ms: int = 80,
) -> bytes:
    """Concatenate two PCM buffers with a short cosine cross-fade gap.

    Replaces the naive ``prev + zeros(200ms) + next`` pattern at
    sentence boundaries. The hard transition from non-zero PCM to a
    zero buffer (and back) is a documented click/pop source; cosine
    fades on the last ``fade_ms`` of ``prev`` and the first ``fade_ms``
    of ``next``, separated by a ``gap_ms`` zero pad, removes the
    first-derivative discontinuity at both edges.

    Algorithm:
      1. Tail of ``prev`` is trimmed forward to the nearest zero
         crossing inside the last ``fade_ms`` of the buffer (so the
         fade starts from a low-magnitude sample where possible).
      2. A cosine fade-out is applied to the last ``fade_ms`` of the
         (trimmed) prev tail.
      3. ``int(gap_ms * sr / 1000)`` zero samples are inserted.
      4. A cosine fade-in is applied to the first ``fade_ms`` of
         ``next``.
      5. Concatenated bytes returned.

    Edge cases:
      * Empty ``prev`` → return ``next`` unchanged (no boundary).
      * Empty ``next`` → return ``prev`` unchanged.
      * Buffers shorter than ``fade_ms`` worth of samples → use the
        full buffer length as the effective fade window.
    """
    if not prev_pcm_int16:
        return next_pcm_int16
    if not next_pcm_int16:
        return prev_pcm_int16

    prev_arr = np.frombuffer(prev_pcm_int16, dtype=np.int16).astype(np.float64)
    next_arr = np.frombuffer(next_pcm_int16, dtype=np.int16).astype(np.float64)
    if prev_arr.size == 0:
        return next_pcm_int16
    if next_arr.size == 0:
        return prev_pcm_int16

    fade_samples_target = max(1, int(round(fade_ms * sample_rate / 1000.0)))
    gap_samples = int(round(gap_ms * sample_rate / 1000.0))

    # (1) Trim the prev tail forward to the nearest zero crossing in
    # the last `fade_ms` window. "Forward" = drop the post-crossing
    # tail so the fade-out starts from a near-zero sample. If no
    # crossing is found, keep the buffer as-is.
    fade_window_prev = min(fade_samples_target, prev_arr.size)
    if fade_window_prev > 1:
        window_start = prev_arr.size - fade_window_prev
        window = prev_arr[window_start:]
        # Zero crossing = sign change between consecutive samples.
        signs = np.sign(window)
        # Treat exact zeros as "matches" so we can land on them too.
        changes = np.where(np.diff(signs) != 0)[0]
        if changes.size > 0:
            # Pick the LATEST zero crossing in the window so we keep
            # as much of the prev tail as possible while still landing
            # on a near-zero edge.
            crossing_idx_in_window = int(changes[-1]) + 1  # post-crossing sample
            trim_to = window_start + crossing_idx_in_window
            # Don't trim below the fade window itself.
            trim_to = min(trim_to, prev_arr.size)
            prev_arr = prev_arr[:trim_to]

    # (2) Cosine fade-out on prev's tail.
    fade_samples_prev = min(fade_samples_target, prev_arr.size)
    if fade_samples_prev > 0:
        # Cosine ramp from 1.0 (at the start of the window) down to
        # 0.0 at the last sample. `cos(pi * t/2)` on t in [0, 1] gives
        # a smooth, click-free fade-out.
        ramp = np.cos(
            np.linspace(0.0, np.pi / 2.0, fade_samples_prev, dtype=np.float64)
        )
        prev_arr = prev_arr.copy()  # defensive — frombuffer returns RO array
        prev_arr[-fade_samples_prev:] *= ramp

    # (3) Gap zeros.
    gap = np.zeros(gap_samples, dtype=np.float64)

    # (4) Cosine fade-in on the head of next.
    fade_samples_next = min(fade_samples_target, next_arr.size)
    next_arr = next_arr.copy()
    if fade_samples_next > 0:
        ramp_in = np.cos(
            np.linspace(np.pi / 2.0, 0.0, fade_samples_next, dtype=np.float64)
        )
        next_arr[:fade_samples_next] *= ramp_in

    # (5) Concatenate, clip to int16 range, cast.
    combined = np.concatenate([prev_arr, gap, next_arr])
    combined = np.clip(combined, -32768, 32767)
    return combined.astype(np.int16).tobytes()


def apply_voice_settings(
    pcm_int16: bytes,
    *,
    sample_rate: int,
    voice_settings: dict | None,
) -> bytes:
    """Apply all PCM-side transforms from a voice_settings dict in
    one call. Currently only `speed`; other fields (pitch, loudness)
    are documented hooks for Dalga 2.6.

    Pipeline order (matters):
      1. DC-offset removal — center the buffer around zero so the
         resampler doesn't propagate / amplify a constant offset.
      2. Peak limiter (-0.5 dBTP) — clip hot peaks before resampling
         so the speed knob never re-saturates and the int16 cast
         downstream sees in-range data.
      3. ``apply_speed`` (existing) — resamples the centred / limited
         buffer to the requested speed.

    Returns the input unchanged when ``voice_settings`` is None / empty
    AND no defensive transforms are needed. We still run DC removal +
    peak limit on every non-empty buffer because both are quality
    hotfixes (A.5) that benefit unconditional callers, but they are
    no-ops on already-clean PCM (small mean / no saturated peaks)."""
    if not pcm_int16:
        return pcm_int16
    # Quality hotfix A.5 — always run DC + peak limit, even when no
    # voice_settings were sent. Both helpers are O(n) and bail early
    # on a clean buffer so the cost is negligible.
    pcm_int16 = apply_dc_offset_removal(pcm_int16)
    pcm_int16 = apply_peak_limit(pcm_int16)
    if not voice_settings:
        return pcm_int16
    speed = voice_settings.get("speed")
    return apply_speed(pcm_int16, sample_rate=sample_rate, speed=speed)


__all__ = [
    "apply_dc_offset_removal",
    "apply_peak_limit",
    "apply_speed",
    "apply_voice_settings",
    "crossfade_concat",
]
