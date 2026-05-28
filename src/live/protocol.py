"""Transport-agnostic streaming-TTS primitives.

The historical name `live` is a leftover from the abandoned LiveKit /
WebRTC scaffold (see decision-log 2026-05-24). Today this module only
holds shared constants + a PCM framing helper used when a transport
needs fixed-duration audio frames (e.g. WebRTC Opus encoders want
20 ms frames). The production HTTP-chunked path does NOT frame audio
at the gateway — the worker emits sentence-sized PCM chunks and the
gateway forwards them as-is.

Cleanup (audit L3 medium 2026-05-25): `LiveAudioFrame`,
`LiveLatencyWaterfall`, `WATERFALL_FIELDS`, `now_ms` were removed
because no production code path read them. The waterfall observation
the audit doc referred to lives in `usage_records` columns + the
`neurovoice_tts_*_seconds` Prometheus histograms; the in-memory bag was
WebRTC-era scaffolding kept alive only by its own tests.
"""

from __future__ import annotations

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_FRAME_MS = 20


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
