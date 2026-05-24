"""Shared streaming-TTS primitives for gateway and worker.

Despite the package name, nothing here is WebRTC-specific. The PCM
framing helper backs any transport that needs fixed-duration frames
(WebRTC Opus, future WebSocket binary frames, etc.). The production
HTTP-chunked `/v1/tts/stream` path does NOT frame at the gateway —
the worker emits sentence-sized PCM chunks and the gateway forwards
them verbatim.

Keeping the namespace as `live` is a historical accident from the
abandoned LiveKit scaffold (decision-log 2026-05-24). A later rename
to `src/streaming/` is fine; today it doesn't earn the churn.
"""

from .protocol import (
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    split_pcm16_frames,
)

__all__ = [
    "DEFAULT_FRAME_MS",
    "DEFAULT_SAMPLE_RATE",
    "split_pcm16_frames",
]
