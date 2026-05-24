"""WAV + PCM byte helpers — no model, no GPU, no inference deps.

These primitives let the gateway assemble a final WAV from int16 PCM
chunks (sync proxy path), let the worker hand back int16 PCM to the
result stream (audio chunk wire format), and let any test fixture
generate WAV blobs without spinning up VoxCPM2.

VoxCPM2 emits float32 in [-1.0, +1.0]; downstream consumers (HTTP WAV,
WebSocket frames, R2 archive) all want int16 PCM. The two functions
below are the canonical bridge.
"""

from __future__ import annotations

import io
import wave

import numpy as np


def float_to_pcm16_bytes(wav: np.ndarray) -> bytes:
    """Float32 [-1.0, +1.0] → int16 PCM bytes (mono, native byte order).

    Clips overflow rather than wrap-around so a hot signal becomes a
    flat-top distortion (audible but harmless) instead of impulse noise.
    """
    wav = np.clip(wav, -1.0, 1.0)
    return (wav * 32767.0).astype(np.int16).tobytes()


def pcm16_to_wav_bytes(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw int16 PCM in a complete RIFF/WAVE container.

    Used by the sync proxy path: gateway concatenates worker-emitted
    chunks into a single PCM buffer, then this helper turns it into a
    playable .wav response body. The chunked streaming endpoint
    (`/v1/tts/stream`) uses a different "infinite-size" RIFF header
    trick inlined in `server.main._yield_wav` — gateway never imports
    a worker module.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)  # int16 = 2 bytes per sample
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()
