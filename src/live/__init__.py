"""Shared live-TTS primitives for gateway and worker.

This package is deliberately independent from `server.*` and `worker.*` so
the WebRTC/live path has one protocol surface shared by both processes.
"""

from .livekit import LiveKitConfig, LiveKitTokenIssuer
from .protocol import (
    CONTROL_PROTOCOL,
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    LiveAudioFrame,
    LiveLatencyWaterfall,
    now_ms,
    split_pcm16_frames,
)
from .registry import LiveWorkerInfo, LiveWorkerRegistry
from .sessions import (
    LiveSession,
    LiveSessionAssignment,
    LiveSessionStore,
    live_assignment_stream,
)

__all__ = [
    "CONTROL_PROTOCOL",
    "DEFAULT_FRAME_MS",
    "DEFAULT_SAMPLE_RATE",
    "LiveAudioFrame",
    "LiveKitConfig",
    "LiveKitTokenIssuer",
    "LiveLatencyWaterfall",
    "LiveSession",
    "LiveSessionAssignment",
    "LiveSessionStore",
    "LiveWorkerInfo",
    "LiveWorkerRegistry",
    "now_ms",
    "live_assignment_stream",
    "split_pcm16_frames",
]
