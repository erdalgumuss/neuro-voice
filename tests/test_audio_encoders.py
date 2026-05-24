"""Streaming audio encoder tests.

We don't validate audio QUALITY (that's a per-hardware bench); we
validate that the encoder spits out valid framed bytes for each
codec, the bytes form a complete file, and the encoder cleans up its
ffmpeg subprocess.

Codec tests require ffmpeg on PATH. They skip if it's missing rather
than fail — CI without ffmpeg should still run the rest of the suite.
"""

from __future__ import annotations

import shutil

import pytest

from audio.encoders import (
    SUPPORTED_STREAM_FORMATS,
    EncoderError,
    Mp3Encoder,
    OpusEncoder,
    Pcm16PassthroughEncoder,
    get_stream_encoder,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not on PATH"
)


def _silent_pcm(sample_rate: int = 48000, duration_s: float = 0.5) -> bytes:
    """Generate `duration_s` of silent int16 mono PCM."""
    n = int(sample_rate * duration_s)
    return b"\x00\x00" * n


# --------------------------------------------------------------------------- #
# Registry + factory
# --------------------------------------------------------------------------- #


def test_get_stream_encoder_returns_expected_classes() -> None:
    assert isinstance(get_stream_encoder("pcm16", sample_rate=48000), Pcm16PassthroughEncoder)
    assert isinstance(get_stream_encoder("mp3", sample_rate=48000), Mp3Encoder)
    assert isinstance(get_stream_encoder("opus", sample_rate=48000), OpusEncoder)


def test_get_stream_encoder_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError) as ei:
        get_stream_encoder("aac", sample_rate=48000)
    assert "aac" in str(ei.value)
    assert "known:" in str(ei.value)


def test_supported_formats_advertise_wav() -> None:
    """The streaming endpoint accepts `wav` via the inline RIFF trick
    in server.main, NOT through this encoder layer. The public list
    still advertises it so clients see the full surface."""
    assert "wav" in SUPPORTED_STREAM_FORMATS
    assert "pcm16" in SUPPORTED_STREAM_FORMATS
    assert "mp3" in SUPPORTED_STREAM_FORMATS
    assert "opus" in SUPPORTED_STREAM_FORMATS


# --------------------------------------------------------------------------- #
# Pcm16Passthrough
# --------------------------------------------------------------------------- #


async def test_pcm16_passthrough_is_identity() -> None:
    enc = Pcm16PassthroughEncoder(sample_rate=48000)
    await enc.start()
    sample = b"\x01\x00\x02\x00\x03\x00"
    assert await enc.encode_chunk(sample) == sample
    assert await enc.close() == b""
    assert enc.content_type == "application/octet-stream"


# --------------------------------------------------------------------------- #
# Mp3
# --------------------------------------------------------------------------- #


@requires_ffmpeg
async def test_mp3_encoder_produces_framed_output() -> None:
    """Drive 500 ms of silent PCM through the encoder; the output
    must:
      * be non-empty,
      * start with a valid mp3 frame sync (0xFFFB / 0xFFF3 / 0xFFFA)
        OR an ID3v2 tag ("ID3"),
      * have ffmpeg exit cleanly (close() returns without raising).
    """
    enc = Mp3Encoder(sample_rate=48000)
    await enc.start()
    out = bytearray()
    out += await enc.encode_chunk(_silent_pcm())
    tail = await enc.close()
    out += tail

    assert len(out) > 0
    # Either ID3v2 header up front or an mp3 sync word in the first 256 bytes.
    head = bytes(out[:256])
    assert head.startswith(b"ID3") or any(
        head[i] == 0xFF and (head[i + 1] & 0xE0) == 0xE0
        for i in range(len(head) - 1)
    ), f"no ID3 tag nor mp3 sync found in head: {head[:32]!r}"
    assert enc.content_type == "audio/mpeg"


@requires_ffmpeg
async def test_mp3_encoder_supports_multiple_chunks() -> None:
    enc = Mp3Encoder(sample_rate=48000)
    await enc.start()
    out = bytearray()
    # Three chunks → encoder must produce SOMETHING across them in
    # total (frames may bunch up depending on ffmpeg's internal buffer).
    for _ in range(3):
        out += await enc.encode_chunk(_silent_pcm(duration_s=0.2))
    out += await enc.close()
    assert len(out) > 0


@requires_ffmpeg
async def test_mp3_encoder_empty_input_close_is_safe() -> None:
    """Worker may produce 0 bytes (error path). Encoder MUST NOT crash
    on `close()` without any encode_chunk call."""
    enc = Mp3Encoder(sample_rate=48000)
    await enc.start()
    out = await enc.close()
    # Output may be empty (no input → no encoded frames) or contain
    # only an ID3 tag — both fine, neither raises.
    assert isinstance(out, bytes)


# --------------------------------------------------------------------------- #
# Opus / OGG
# --------------------------------------------------------------------------- #


@requires_ffmpeg
async def test_opus_encoder_produces_ogg_container() -> None:
    """Output must start with an OGG capture pattern (`OggS`) — that's
    the magic word every OGG page begins with. Concatenated output is
    a complete .ogg file playable by mpv/VLC/Chrome."""
    enc = OpusEncoder(sample_rate=48000)
    await enc.start()
    out = bytearray()
    # 1 s of silence — enough for the muxer to emit the BOS pages.
    out += await enc.encode_chunk(_silent_pcm(duration_s=1.0))
    out += await enc.close()

    assert len(out) > 0
    assert out[:4] == b"OggS", f"expected OGG header, got: {bytes(out[:8])!r}"
    # The first OGG page should declare the codec as opus — the
    # identification header packet starts with the magic 'OpusHead'.
    assert b"OpusHead" in out[:128], "OpusHead packet missing from first OGG page"
    assert enc.content_type == "audio/ogg"


@requires_ffmpeg
async def test_opus_encoder_emits_close_trailer() -> None:
    """ffmpeg flushes the OGG end-of-stream page on stdin close.
    Verifies `close()` actually picks up the trailer rather than
    leaving the .ogg truncated."""
    enc = OpusEncoder(sample_rate=48000)
    await enc.start()

    # Push input and read incrementally so we know the trailer comes
    # from close(), not from the chunk read.
    pre_close = bytearray()
    pre_close += await enc.encode_chunk(_silent_pcm(duration_s=0.5))
    post_close = await enc.close()

    assert len(post_close) > 0, (
        "close() returned no bytes — OGG end-of-stream page not flushed"
    )
    full = bytes(pre_close) + post_close
    assert full[:4] == b"OggS"


# --------------------------------------------------------------------------- #
# Error surface
# --------------------------------------------------------------------------- #


async def test_encoder_raises_before_start() -> None:
    """encode_chunk before start() should raise EncoderError, not
    AttributeError — the caller gets a meaningful message."""
    enc = Mp3Encoder(sample_rate=48000)
    with pytest.raises(EncoderError):
        await enc.encode_chunk(b"\x00\x00")


async def test_pcm_encoder_works_before_explicit_start() -> None:
    """Passthrough has no subprocess so start() is a no-op; pre-start
    encode_chunk SHOULD work for it (sole exception)."""
    enc = Pcm16PassthroughEncoder(sample_rate=48000)
    assert await enc.encode_chunk(b"\x01\x02") == b"\x01\x02"
