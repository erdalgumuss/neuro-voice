"""Protocol-level types for low-latency live TTS.

The live path has two independent streams:

* WebRTC audio track: PCM16 frames internally, Opus on the wire.
* Data channel/control: JSON events for synthesize/cancel/metrics.

These types keep timing and framing rules explicit without importing
LiveKit or FastAPI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

CONTROL_PROTOCOL = "nqai.tts.live.v1"
DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_FRAME_MS = 20

WATERFALL_FIELDS = (
    "client_request_created_ms",
    "gateway_received_ms",
    "auth_done_ms",
    "session_admitted_ms",
    "worker_selected_ms",
    "worker_accepted_ms",
    "reference_ready_ms",
    "adapter_ready_ms",
    "frontend_done_ms",
    "model_start_ms",
    "model_first_audio_ms",
    "worker_first_frame_sent_ms",
    "gateway_or_media_first_send_ms",
    "client_first_audio_ms",
    "final_audio_done_ms",
)


def now_ms() -> int:
    """Wall-clock milliseconds for cross-process latency traces."""
    return int(time.time() * 1000)


@dataclass
class LiveAudioFrame:
    """One internally framed PCM16 mono audio packet for WebRTC publishing."""

    seq: int
    pcm_int16: bytes
    sample_rate: int = DEFAULT_SAMPLE_RATE
    duration_ms: int = DEFAULT_FRAME_MS
    sentence_text: str | None = None

    @property
    def samples_per_channel(self) -> int:
        return len(self.pcm_int16) // 2


@dataclass
class LiveLatencyWaterfall:
    """Timestamp bag + derived metrics for one live synthesis request."""

    request_id: str
    timestamps: dict[str, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in WATERFALL_FIELDS:
            self.timestamps.setdefault(field_name, None)

    def mark(self, field_name: str, value_ms: int | None = None) -> int:
        if field_name not in WATERFALL_FIELDS:
            raise KeyError(f"unknown live latency field: {field_name}")
        value = now_ms() if value_ms is None else int(value_ms)
        self.timestamps[field_name] = value
        return value

    def duration(self, start: str, end: str) -> int | None:
        a = self.timestamps.get(start)
        b = self.timestamps.get(end)
        if a is None or b is None:
            return None
        return max(0, b - a)

    def derived(self) -> dict[str, int | None]:
        return {
            "admission_ms": self.duration("gateway_received_ms", "session_admitted_ms"),
            "worker_dispatch_ms": self.duration("worker_selected_ms", "worker_accepted_ms"),
            "reference_resolve_ms": self.duration("worker_accepted_ms", "reference_ready_ms"),
            "adapter_load_ms": self.duration("reference_ready_ms", "adapter_ready_ms"),
            "frontend_ms": self.duration("adapter_ready_ms", "frontend_done_ms"),
            "model_ttfa_ms": self.duration("model_start_ms", "model_first_audio_ms"),
            "first_audio_ms": self.duration("gateway_received_ms", "gateway_or_media_first_send_ms"),
            "total_inference_ms": self.duration("model_start_ms", "final_audio_done_ms"),
            "live_session_wait_ms": self.duration("session_admitted_ms", "worker_accepted_ms"),
        }

    def as_dict(self) -> dict[str, int | None]:
        return {**self.timestamps, **self.derived()}


def split_pcm16_frames(
    pcm_int16: bytes,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    frame_ms: int = DEFAULT_FRAME_MS,
) -> list[bytes]:
    """Split PCM16 mono into fixed-duration frames.

    The final frame is allowed to be short. Padding belongs in the media
    adapter if a transport requires exact frame sizes.
    """
    if frame_ms <= 0:
        raise ValueError("frame_ms must be positive")
    bytes_per_frame = int(sample_rate * (frame_ms / 1000.0)) * 2
    if bytes_per_frame <= 0:
        raise ValueError("frame size resolved to zero bytes")
    return [
        pcm_int16[i:i + bytes_per_frame]
        for i in range(0, len(pcm_int16), bytes_per_frame)
        if pcm_int16[i:i + bytes_per_frame]
    ]
