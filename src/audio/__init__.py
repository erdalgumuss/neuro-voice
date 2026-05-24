"""Audio helpers — shared between gateway (proxy/WAV assembly) and worker.

This package holds **pure, GPU-free, framework-light** audio utilities so
both the gateway and the worker can import them without dragging in
VoxCPM2 or any inference dependency. Anything that touches PyTorch,
librosa, or model weights belongs in `src/worker/`.
"""

from .wav import (
    float_to_pcm16_bytes,
    pcm16_to_wav_bytes,
)

__all__ = [
    "float_to_pcm16_bytes",
    "pcm16_to_wav_bytes",
]
