"""Reference-audio normalization — trim + resample + write as mono WAV.

VoxCPM2 expects reference audio at **16 kHz mono**. This module is the
single bottleneck where arbitrary user-uploaded audio (MP3, WAV, M4A,
OGG, FLAC) gets normalized to that contract; downstream code never
sees the original format.

The `target_sr` default is intentionally 16000 — drift here causes
silent reference-contract violation downstream (VoxCPM2 will either
fail to clone the voice or produce off-pitch output). Audit 2026-05-24
(F3) caught a 24000 default that diverged from VoxCPM2's expectation
and from `NEUROVOICE_REF_SR` env (also 16000).
"""

from __future__ import annotations

import io
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def trim_and_resample_to_wav(
    *,
    src_bytes: bytes,
    dst_path: Path,
    trim_seconds: float = 15.0,
    target_sr: int = 16000,
) -> float:
    """Decode arbitrary audio bytes → trim to N seconds → resample to
    `target_sr` → write mono PCM-16 WAV. Returns actual duration written.

    `src_bytes` may be any format librosa/audioread can decode (WAV/MP3/
    M4A/OGG/FLAC). For exotic codecs ffmpeg must be on PATH or
    `librosa.load` raises a clear error.
    """
    audio, _src_sr = librosa.load(io.BytesIO(src_bytes), sr=target_sr, mono=True)
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
