"""WAV / PCM streaming helpers.

Two output modes:

* `wav`        — server pre-streams a WAV header with a huge placeholder
                 data-chunk size (RIFF "streaming WAV" trick). Players that
                 don't seek (ffplay, browsers in <audio>) tolerate this.
* `pcm16`      — raw little-endian int16 PCM; the client must know the
                 sample rate (returned in the `X-NQAI-Sample-Rate` header).

For multi-sentence streams we inject 200 ms of silence between segments so
the listener doesn't perceive a cliff between sentence boundaries.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Iterator

from registry import Voice

from .engine import BaseSynthEngine

INTER_SEGMENT_SILENCE_MS = 200


def _streaming_wav_header(sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    # Use a huge but valid uint32 for chunk sizes — players that ignore EOF
    # will keep reading until the stream closes.
    riff_size = 0xFFFFFFFF - 8
    data_size = 0xFFFFFFFF - 44
    header = b"RIFF"
    header += struct.pack("<I", riff_size)
    header += b"WAVEfmt "
    header += struct.pack("<I", 16)                 # PCM fmt chunk size
    header += struct.pack("<H", 1)                  # PCM format tag
    header += struct.pack("<H", channels)
    header += struct.pack("<I", sample_rate)
    header += struct.pack("<I", byte_rate)
    header += struct.pack("<H", block_align)
    header += struct.pack("<H", bits)
    header += b"data"
    header += struct.pack("<I", data_size)
    return header


def stream_wav(
    engine: BaseSynthEngine,
    *,
    text: str,
    voice: Voice,
    reference_path,
    language_id: str = "tr",
) -> Iterator[bytes]:
    """Yield streaming-WAV bytes (header + sentence chunks + inter-silence)."""
    yielded_header = False
    sr = engine.sample_rate
    silence = b"\x00\x00" * int(sr * INTER_SEGMENT_SILENCE_MS / 1000)

    for i, chunk in enumerate(
        engine.synthesize_stream(
            text=text, voice=voice, reference_path=reference_path, language_id=language_id
        )
    ):
        if not yielded_header:
            yield _streaming_wav_header(chunk.sample_rate)
            sr = chunk.sample_rate
            yielded_header = True
        if i > 0:
            yield silence
        yield chunk.pcm_int16

    if not yielded_header:
        # No segments produced — still emit a valid empty WAV
        yield _streaming_wav_header(sr)


def stream_pcm16(
    engine: BaseSynthEngine,
    *,
    text: str,
    voice: Voice,
    reference_path,
    language_id: str = "tr",
) -> Iterator[bytes]:
    """Yield raw little-endian int16 PCM, with inter-segment silence padding."""
    sr = engine.sample_rate
    silence = b"\x00\x00" * int(sr * INTER_SEGMENT_SILENCE_MS / 1000)
    for i, chunk in enumerate(
        engine.synthesize_stream(
            text=text, voice=voice, reference_path=reference_path, language_id=language_id
        )
    ):
        if i > 0:
            yield silence
        yield chunk.pcm_int16


def collect_wav_bytes(stream: Iterator[bytes]) -> bytes:
    buf = io.BytesIO()
    for part in stream:
        buf.write(part)
    return buf.getvalue()
