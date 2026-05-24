"""Unit tests for src/audio/postprocess.py — Dalga 2.1 speed knob.

We don't measure subjective audio quality (that's a per-hardware
bench); we pin the contract: speed=1.0 is identity, speed > 1.0
shortens the buffer, speed < 1.0 lengthens it, empty in → empty out,
all clipped to int16.
"""

from __future__ import annotations

import numpy as np
import pytest

from audio.postprocess import apply_speed, apply_voice_settings


def _sin_pcm(duration_s: float = 0.5, sr: int = 48000, freq: float = 440.0) -> bytes:
    """Generate `duration_s` of a sine wave int16 mono PCM. Sine is
    nicer to debug than silence — a regression that drops samples
    shows up in the waveform on inspection."""
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    sig = (np.sin(2 * np.pi * freq * t) * 0.5 * 32767).astype(np.int16)
    return sig.tobytes()


def test_apply_speed_identity_when_none() -> None:
    pcm = _sin_pcm()
    assert apply_speed(pcm, sample_rate=48000, speed=None) is pcm


def test_apply_speed_identity_when_one() -> None:
    pcm = _sin_pcm()
    # Numerical 1.0 with tiny epsilon should still hit the fast path.
    out = apply_speed(pcm, sample_rate=48000, speed=1.0)
    assert out == pcm
    # And the float-close path too.
    out = apply_speed(pcm, sample_rate=48000, speed=1.0 + 1e-4)
    assert out == pcm


def test_apply_speed_empty_input_returns_empty() -> None:
    assert apply_speed(b"", sample_rate=48000, speed=1.2) == b""


def test_apply_speed_faster_shortens_buffer() -> None:
    pcm = _sin_pcm(duration_s=1.0)  # 48000 samples * 2 bytes
    out = apply_speed(pcm, sample_rate=48000, speed=1.2)
    # speed=1.2 → ~5/6 of input length, within ±1 sample.
    expected_samples = int(round(48000 / 1.2))
    actual_samples = len(out) // 2
    assert abs(actual_samples - expected_samples) <= 1
    assert actual_samples < 48000


def test_apply_speed_slower_lengthens_buffer() -> None:
    pcm = _sin_pcm(duration_s=1.0)
    out = apply_speed(pcm, sample_rate=48000, speed=0.8)
    expected_samples = int(round(48000 / 0.8))
    actual_samples = len(out) // 2
    assert abs(actual_samples - expected_samples) <= 1
    assert actual_samples > 48000


def test_apply_speed_output_is_valid_int16() -> None:
    """Linear interp can produce out-of-range floats at the boundary
    only if input has extreme values; defensive clipping keeps int16
    cast safe regardless. Verify the byte length is always even."""
    pcm = _sin_pcm()
    for speed in (0.7, 0.85, 1.0, 1.1, 1.2):
        out = apply_speed(pcm, sample_rate=48000, speed=speed)
        assert len(out) % 2 == 0, f"speed={speed}: output bytes not int16-aligned"
        # Decode and confirm range.
        arr = np.frombuffer(out, dtype=np.int16)
        if arr.size:
            assert int(arr.min()) >= -32768
            assert int(arr.max()) <= 32767


def test_apply_speed_rejects_zero_or_negative() -> None:
    with pytest.raises(ValueError):
        apply_speed(_sin_pcm(), sample_rate=48000, speed=0.0)
    with pytest.raises(ValueError):
        apply_speed(_sin_pcm(), sample_rate=48000, speed=-1.0)


def test_apply_speed_extreme_short_buffer() -> None:
    """A 2-sample chunk (1 frame of int16) should still resample
    safely — np.linspace + np.interp don't trip on length 1."""
    pcm = b"\x00\x10\x00\x20"  # 2 int16 samples
    out = apply_speed(pcm, sample_rate=48000, speed=1.2)
    assert len(out) % 2 == 0


# --------------------------------------------------------------------------- #
# apply_voice_settings dispatcher
# --------------------------------------------------------------------------- #


def test_apply_voice_settings_none_is_passthrough() -> None:
    pcm = _sin_pcm()
    assert apply_voice_settings(pcm, sample_rate=48000, voice_settings=None) is pcm
    assert apply_voice_settings(pcm, sample_rate=48000, voice_settings={}) is pcm


def test_apply_voice_settings_speed_applied() -> None:
    pcm = _sin_pcm(duration_s=1.0)
    out = apply_voice_settings(
        pcm, sample_rate=48000, voice_settings={"speed": 1.1},
    )
    actual = len(out) // 2
    expected = int(round(48000 / 1.1))
    assert abs(actual - expected) <= 1


def test_apply_voice_settings_ignores_non_pcm_fields() -> None:
    """`stability` / `similarity_boost` are engine knobs applied at
    inference time, not PCM transforms. The dispatcher must IGNORE
    them rather than choke."""
    pcm = _sin_pcm()
    out = apply_voice_settings(
        pcm, sample_rate=48000,
        voice_settings={
            "stability": 0.5,
            "similarity_boost": 0.8,
            "style": 0.3,
            "use_speaker_boost": True,
            "pitch": 2.0,  # forward-compatible; still no-op here
        },
    )
    assert out == pcm
