"""Reference-audio normalization — trim + resample + write as mono WAV."""

from __future__ import annotations

import io
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def trim_and_resample_to_wav(
    *,
    src_bytes: bytes,
    src_suffix: str,
    dst_path: Path,
    trim_seconds: float = 15.0,
    target_sr: int = 24000,
) -> float:
    """Decode arbitrary audio bytes → trim to N seconds → resample → write mono WAV.

    Returns the actual duration (seconds) written.
    """
    audio, src_sr = librosa.load(io.BytesIO(src_bytes), sr=target_sr, mono=True)
    if audio.size == 0:
        raise ValueError("decoded audio is empty")

    max_samples = int(trim_seconds * target_sr)
    if audio.shape[0] > max_samples:
        audio = audio[:max_samples]

    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = audio * (0.95 / peak)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dst_path, audio, target_sr, subtype="PCM_16")
    return float(audio.shape[0] / target_sr)


def read_audio_bytes_from_path(path: Path) -> tuple[bytes, str]:
    return path.read_bytes(), path.suffix
