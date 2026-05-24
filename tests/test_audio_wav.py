"""Unit tests for src/audio/wav.py — shared by gateway proxy + worker.

Covers:
  * float_to_pcm16_bytes: silence, max-positive, max-negative, overflow clip,
    DC offset, vectorisation correctness
  * pcm16_to_wav_bytes: RIFF header validity, sample-rate round-trip,
    mono vs stereo byte layout, empty-PCM safety
"""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from audio.wav import float_to_pcm16_bytes, pcm16_to_wav_bytes


# --------------------------------------------------------------------------- #
# float_to_pcm16_bytes
# --------------------------------------------------------------------------- #
def test_float_to_pcm16_silence_is_all_zero():
    silence = np.zeros(1024, dtype=np.float32)
    out = float_to_pcm16_bytes(silence)
    assert len(out) == 1024 * 2  # int16 = 2 bytes/sample
    arr = np.frombuffer(out, dtype=np.int16)
    assert (arr == 0).all()


def test_float_to_pcm16_full_scale_positive_clipped_to_max_int16():
    """0.99999 → near +32767. Clipping at +1.0 yields exactly +32767."""
    s = np.array([1.0, 0.99999, 0.5, 0.0, -0.5, -1.0], dtype=np.float32)
    out = np.frombuffer(float_to_pcm16_bytes(s), dtype=np.int16)
    assert out[0] == 32767
    assert out[1] == 32766  # 32767 * 0.99999 rounded
    assert out[2] == 16383
    assert out[3] == 0
    assert out[4] == -16383
    # Exactly -1.0 * 32767 = -32767. Asymmetric — int16 actually allows -32768
    # but we map [-1.0, +1.0] symmetrically to avoid DC bias.
    assert out[5] == -32767


def test_float_to_pcm16_overflow_clips_not_wraps():
    """+2.0 must clip to +32767, not wrap around to a negative value."""
    s = np.array([2.0, -2.5, 100.0, -100.0], dtype=np.float32)
    out = np.frombuffer(float_to_pcm16_bytes(s), dtype=np.int16)
    assert out[0] == 32767
    assert out[1] == -32767
    assert out[2] == 32767
    assert out[3] == -32767


def test_float_to_pcm16_preserves_length():
    for n in [0, 1, 1023, 48000]:
        sig = np.zeros(n, dtype=np.float32)
        assert len(float_to_pcm16_bytes(sig)) == n * 2


# --------------------------------------------------------------------------- #
# pcm16_to_wav_bytes
# --------------------------------------------------------------------------- #
def test_pcm16_to_wav_round_trip_via_wave_module():
    """Write WAV, read it back with stdlib wave, content matches."""
    pcm = (np.arange(-100, 100, dtype=np.int16)).tobytes()
    wav_bytes = pcm16_to_wav_bytes(pcm, sample_rate=16000)
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        assert r.getnchannels() == 1
        assert r.getsampwidth() == 2
        assert r.getframerate() == 16000
        assert r.readframes(r.getnframes()) == pcm


def test_pcm16_to_wav_sample_rate_round_trip():
    for sr in [8000, 16000, 22050, 24000, 44100, 48000]:
        wav_bytes = pcm16_to_wav_bytes(b"\x00\x00" * 100, sample_rate=sr)
        with wave.open(io.BytesIO(wav_bytes), "rb") as r:
            assert r.getframerate() == sr


def test_pcm16_to_wav_stereo_byte_layout():
    """channels=2 means each frame is 4 bytes (L int16 + R int16)."""
    # 10 stereo frames = 40 bytes raw.
    pcm = b"\x00\x00\x00\x00" * 10
    wav_bytes = pcm16_to_wav_bytes(pcm, sample_rate=16000, channels=2)
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        assert r.getnchannels() == 2
        assert r.getnframes() == 10


def test_pcm16_to_wav_empty_pcm_is_valid_zero_length_wav():
    wav_bytes = pcm16_to_wav_bytes(b"", sample_rate=48000)
    # Header alone (44 bytes RIFF for PCM) — wave module accepts it.
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        assert r.getnframes() == 0
        assert r.getframerate() == 48000


def test_pcm16_to_wav_has_riff_magic():
    """First four bytes must be ASCII 'RIFF' for any consumer to recognise it."""
    out = pcm16_to_wav_bytes(b"\x01\x00" * 4, sample_rate=16000)
    assert out[:4] == b"RIFF"
    assert out[8:12] == b"WAVE"


# --------------------------------------------------------------------------- #
# Composition — float in, WAV out (the canonical engine→response pipeline)
# --------------------------------------------------------------------------- #
def test_float_array_to_wav_round_trip():
    """End-to-end: generate a sine wave, encode to WAV, decode back,
    verify amplitude survives quantisation within int16 precision."""
    sr = 16000
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False, dtype=np.float32)
    sine = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    pcm = float_to_pcm16_bytes(sine)
    wav_bytes = pcm16_to_wav_bytes(pcm, sample_rate=sr)
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        decoded = np.frombuffer(r.readframes(r.getnframes()), dtype=np.int16)
    # Reconstruct float in [-1, 1] and check peak amplitude ≈ 0.5.
    decoded_float = decoded.astype(np.float32) / 32767.0
    assert decoded_float.max() == pytest.approx(0.5, abs=1e-3)
    assert decoded_float.min() == pytest.approx(-0.5, abs=1e-3)
