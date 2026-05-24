"""Shared streaming-TTS primitives for gateway and worker.

Despite the package name, nothing here is WebRTC-specific. The
abstractions (audio frame, latency waterfall, fixed-duration framing)
are transport-agnostic; they back the HTTP chunked `/v1/tts/stream`
path today, and would back a WebSocket or WebRTC path the same way
tomorrow without changing this module.

Keeping the namespace as `live` is a historical accident — semantic
correction is on the deletion side (LiveKit/session orchestration left
the repo when we decided NQAI Voice ships as a standard streaming TTS
API à la ElevenLabs/OpenAI, not a WebRTC voice-agent platform). A
later rename to `src/streaming/` is fine; today it doesn't earn the
churn.
"""

from .protocol import (
    CONTROL_PROTOCOL,
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    LiveAudioFrame,
    LiveLatencyWaterfall,
    now_ms,
    split_pcm16_frames,
)

__all__ = [
    "CONTROL_PROTOCOL",
    "DEFAULT_FRAME_MS",
    "DEFAULT_SAMPLE_RATE",
    "LiveAudioFrame",
    "LiveLatencyWaterfall",
    "now_ms",
    "split_pcm16_frames",
]
