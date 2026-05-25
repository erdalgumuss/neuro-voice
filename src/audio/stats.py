"""MLOps PR #2 — output PCM dimension statistics.

Quick, allocation-light analysis of int16 PCM buffers so the worker
can emit quality-side observability metrics without dragging in a
heavy DSP dependency. Pure numpy, single pass over the buffer.

Why this module rather than inline in `worker.pipeline`:

* The same shape will get reused by the (future) canary harness and
  the eval suite — keep the computation in one place so a fix to the
  RMS formula doesn't drift across three call sites.
* It is easy to unit-test in isolation (no DB, no Redis, no engine).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Int16 full-scale reference point. Below this (~1 % of max) is
# treated as "silent" — comfortable margin above noise floor of a
# clean recording, well below any audible speech amplitude.
_INT16_FULL_SCALE: int = 32767
_SILENCE_THRESHOLD: int = int(_INT16_FULL_SCALE * 0.01)  # 327
_CLIPPING_THRESHOLD: int = int(_INT16_FULL_SCALE * 0.95)  # 31128


@dataclass(frozen=True)
class PcmStats:
    """Dimensional snapshot of an int16 PCM buffer."""

    rms_normalized: float       # 0.0..1.0 of int16 full-scale
    silence_ratio: float        # 0.0..1.0 of samples below silence threshold
    clipping_ratio: float       # 0.0..1.0 of samples at >= 95 % full-scale
    duration_seconds: float     # buffer length / sample_rate
    sample_count: int           # number of int16 samples (frames × channels)
    integrated_lufs: float      # ITU-R BS.1770-5 LUFS (-70.0 for clips < 400 ms)


# BS.1770 gated minimum window — anything shorter cannot be reliably
# measured and pyloudnorm raises ValueError. We treat that as the
# "definitely silent / noise floor" sentinel.
_BS1770_MIN_DURATION_S: float = 0.4
_LUFS_FLOOR: float = -70.0


def _compute_integrated_lufs(
    arr: np.ndarray,
    *,
    sample_rate: int,
) -> float:
    """ITU-R BS.1770-5 integrated loudness via pyloudnorm.

    Returns `_LUFS_FLOOR` (= -70.0) for:
      * clips shorter than 400 ms (BS.1770 gated-window minimum),
      * pyloudnorm ValueError (too-short / silent / all-zeros),
      * any other unexpected exception (defensive — never break the
        outer stats pipeline on a measurement failure).

    Lazy-imported so installs without the `dev` extra still work
    (pyloudnorm only ships in the dev group; production worker
    images will pin it explicitly once the metric is wired into
    SLO dashboards).
    """
    if arr.size == 0 or sample_rate <= 0:
        return _LUFS_FLOOR
    duration_s = arr.size / float(sample_rate)
    if duration_s < _BS1770_MIN_DURATION_S:
        return _LUFS_FLOOR
    try:
        import pyloudnorm as pyln
    except Exception:
        # pyloudnorm not installed (non-dev environment) — emit floor
        # rather than crashing. Metric will read as "below threshold"
        # which is the safest read on a missing dependency.
        return _LUFS_FLOOR
    try:
        meter = pyln.Meter(sample_rate)
        loudness = float(meter.integrated_loudness(
            arr.astype(np.float32) / 32768.0
        ))
    except ValueError:
        # pyloudnorm raises ValueError on too-short / degenerate input
        # even when we already filter by duration — keep the same
        # sentinel so the histogram bucket is consistent.
        return _LUFS_FLOOR
    except Exception:
        return _LUFS_FLOOR
    # Clamp -inf (digital silence) to the floor for histogram safety;
    # leave normal in-range values untouched.
    if not np.isfinite(loudness):
        return _LUFS_FLOOR
    return loudness


def compute_pcm_stats(
    pcm_int16: bytes,
    *,
    sample_rate: int,
) -> PcmStats:
    """Single-pass stats. Empty / odd-byte input returns all-zeros stats
    so callers never have to special-case the "engine produced nothing"
    error path twice — the metric naturally reads "RMS=0, silence=1".
    """
    if not pcm_int16 or len(pcm_int16) < 2:
        return PcmStats(
            rms_normalized=0.0,
            silence_ratio=1.0,
            clipping_ratio=0.0,
            duration_seconds=0.0,
            sample_count=0,
            integrated_lufs=_LUFS_FLOOR,
        )
    arr = np.frombuffer(pcm_int16, dtype=np.int16)
    if arr.size == 0:
        return PcmStats(0.0, 1.0, 0.0, 0.0, 0, _LUFS_FLOOR)

    # RMS computed in float64 to avoid int16 overflow (square of 32767
    # exceeds int16 / int32 mid-range safely as float).
    f = arr.astype(np.float64)
    rms_raw = float(np.sqrt(np.mean(f * f)))
    rms_normalized = min(rms_raw / _INT16_FULL_SCALE, 1.0)

    abs_arr = np.abs(arr)
    silence_ratio = float((abs_arr < _SILENCE_THRESHOLD).mean())
    clipping_ratio = float((abs_arr >= _CLIPPING_THRESHOLD).mean())

    duration_seconds = arr.size / max(sample_rate, 1)
    integrated_lufs = _compute_integrated_lufs(arr, sample_rate=sample_rate)
    return PcmStats(
        rms_normalized=rms_normalized,
        silence_ratio=silence_ratio,
        clipping_ratio=clipping_ratio,
        duration_seconds=float(duration_seconds),
        sample_count=int(arr.size),
        integrated_lufs=integrated_lufs,
    )
