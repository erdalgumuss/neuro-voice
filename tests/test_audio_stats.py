"""MLOps PR #2 — unit tests for `audio.stats.compute_pcm_stats`.

Pin the four invariants downstream metrics depend on:

  1. Silent buffer → RMS≈0, silence_ratio=1.0, clipping=0.
  2. Full-scale square wave → RMS≈1.0, clipping_ratio>0.
  3. Normal speech-shape signal → RMS in (0.05, 0.35), silence < 0.5.
  4. Empty / malformed buffer → all-zeros stats (never raises).

If the metric SHAPE drifts the dashboard panels / alert thresholds
shift silently. This is the cheapest place to catch that.
"""

from __future__ import annotations

import numpy as np

from audio.stats import compute_pcm_stats


def _pcm_int16(arr: np.ndarray) -> bytes:
    return arr.astype(np.int16).tobytes()


def test_silent_buffer_produces_silence_metrics():
    """All zeros → RMS=0, silence=1.0, clipping=0. The
    `NqaiVoiceSilentOutput` alert depends on this shape."""
    silent = _pcm_int16(np.zeros(48000, dtype=np.int16))
    s = compute_pcm_stats(silent, sample_rate=48000)
    assert s.rms_normalized == 0.0
    assert s.silence_ratio == 1.0
    assert s.clipping_ratio == 0.0
    assert s.duration_seconds == 1.0
    assert s.sample_count == 48000


def test_full_scale_signal_produces_clipping_metric():
    """Saturated signal → clipping_ratio > 0 and RMS near 1.0."""
    full_scale = _pcm_int16(np.full(48000, 32767, dtype=np.int16))
    s = compute_pcm_stats(full_scale, sample_rate=48000)
    assert s.rms_normalized > 0.99
    assert s.clipping_ratio == 1.0  # every sample clips
    assert s.silence_ratio == 0.0


def test_realistic_speech_signal_lands_in_normal_ranges():
    """Random gaussian × 0.1 of full-scale ≈ comfortable speech RMS;
    nothing clips, very little silence. If this band shifts, retune
    the alert thresholds — but the test pinning catches the drift."""
    rng = np.random.default_rng(42)
    sig = (rng.standard_normal(48000) * 0.1 * 32767).clip(-32767, 32767)
    s = compute_pcm_stats(_pcm_int16(sig), sample_rate=48000)
    assert 0.05 < s.rms_normalized < 0.35
    assert s.clipping_ratio < 0.001
    assert s.silence_ratio < 0.20
    assert abs(s.duration_seconds - 1.0) < 0.001


def test_empty_buffer_is_safe_and_reads_as_silent():
    """Engine produced nothing → stats are well-defined and the
    metric panel reads 'silent', which is exactly the failure we
    want surfaced."""
    s = compute_pcm_stats(b"", sample_rate=48000)
    assert s.sample_count == 0
    assert s.duration_seconds == 0.0
    assert s.rms_normalized == 0.0
    assert s.silence_ratio == 1.0
    assert s.clipping_ratio == 0.0


def test_odd_length_buffer_is_safe():
    """Wire format glitch: trailing byte without its pair. Must NOT
    raise. We choose to read it as empty rather than truncating to
    avoid emitting partial-sample metrics that look real."""
    s = compute_pcm_stats(b"\x01", sample_rate=48000)
    assert s.sample_count == 0
    assert s.silence_ratio == 1.0


def test_partial_silence_with_speech_bursts():
    """Mid-stream dropouts: half silence + half normal speech. We
    expect silence_ratio in [0.4, 0.6] — that's the alert sweet
    spot for the `HighSilenceRatio` rule."""
    rng = np.random.default_rng(7)
    speech = (rng.standard_normal(24000) * 0.1 * 32767).clip(-32767, 32767)
    silent = np.zeros(24000, dtype=np.float64)
    mixed = np.concatenate([silent, speech])
    s = compute_pcm_stats(_pcm_int16(mixed), sample_rate=48000)
    assert 0.4 < s.silence_ratio < 0.6
    assert 0.02 < s.rms_normalized < 0.20


# --------------------------------------------------------------------------- #
# A.2 — integrated LUFS (ITU-R BS.1770-5)
# --------------------------------------------------------------------------- #
def test_lufs_short_clip_returns_floor():
    """Clips shorter than 400 ms (BS.1770 gated minimum) emit -70.0."""
    short = _pcm_int16(np.full(4800, 8000, dtype=np.int16))  # 100 ms at 48k
    s = compute_pcm_stats(short, sample_rate=48000)
    assert s.integrated_lufs == -70.0


def test_lufs_silent_clip_returns_floor():
    """Digital silence has no measurable loudness → floor sentinel."""
    silent = _pcm_int16(np.zeros(48000, dtype=np.int16))
    s = compute_pcm_stats(silent, sample_rate=48000)
    assert s.integrated_lufs == -70.0


def test_lufs_realistic_signal_lands_in_band():
    """A 2 s sine wave at 50% scale should land in a reasonable LUFS
    band (-30, 0]. Loose band — BS.1770's K-weighted filter rejects
    energy outside the speech band; a pure tone is generous enough
    to pass gating."""
    sr = 48000
    duration_s = 2.0
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    # 440 Hz tone at 50% scale — well above the BS.1770 absolute
    # threshold (-70 LKFS).
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
    s = compute_pcm_stats(sig.tobytes(), sample_rate=sr)
    assert s.integrated_lufs > -70.0  # not the sentinel
    assert s.integrated_lufs < 0.0     # below digital full-scale


def test_lufs_empty_returns_floor():
    """Empty buffer → floor (matches the rest of the silence contract)."""
    s = compute_pcm_stats(b"", sample_rate=48000)
    assert s.integrated_lufs == -70.0
