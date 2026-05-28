"""Streaming audio codec layer for the gateway.

The worker always emits raw PCM16 (mono, engine sample rate). The
gateway transcodes per-request based on the client's requested
`audio_format`. Three reasons this layer lives gateway-side, not
worker-side:

1. Worker stays simple — one output format means simpler tests,
   simpler invariants, no codec-VRAM contention.
2. The same PCM stream can be served as different codecs to different
   clients (mobile wants opus, browser wants mp3, downloader wants
   wav) without re-running inference.
3. The gateway is CPU-only and idle outside ffmpeg encoding; the GPU
   worker is the expensive resource and should not waste cycles on
   codec work.

Encoders here use **ffmpeg as a long-running subprocess pipe**, one
process per request. We picked ffmpeg over CFFI bindings (lameenc,
pyogg, opuslib) because:

* ffmpeg is the gold-standard reference encoder for libmp3lame and
  libopus — same audio quality, mature, universally tested.
* It is already a system dep on the gateway and worker images
  (deploy/gateway.Dockerfile and worker.Dockerfile install it).
* Subprocess startup overhead is bounded (~10-20 ms) and amortised
  over the request lifetime; in-process bindings shave that but add
  build-fragility and CFFI surface.
* Swapping to in-process encoders later is a `StreamEncoder` protocol
  change — call sites don't move.

Each encoder yields valid framed bytes as PCM is fed in. Concatenated
output is a complete, playable file:

* `Mp3Encoder`         → constant-bitrate mp3 frames (no container)
* `OpusEncoder`        → opus packets in an OGG container
* `Pcm16PassthroughEncoder` → raw int16 PCM (no encoding)
* `WavOneShotEncoder`  → builds a WAV body at close() time; used by
                          the sync `/v1/tts` path, NOT the streaming
                          path (the streaming WAV body uses the
                          "infinite-size" RIFF header trick inlined
                          in `server.main._yield_wav`).

Streaming WAV is a special case because the streaming response writes
headers BEFORE the body is final; the inline RIFF-with-0xFFFFFFFF
sizes trick keeps the body parseable while still chunked. That
predates this module and lives where the StreamingResponse lives.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Protocol

logger = logging.getLogger("neurovoice.audio.encoders")


class EncoderError(Exception):
    """Encoder failed in a way the caller should surface, not retry."""


class StreamEncoder(Protocol):
    """Async streaming encoder contract.

    Usage pattern:

        enc = OpusEncoder(sample_rate=48000)
        await enc.start()
        async for pcm in worker_chunks:
            out = await enc.encode_chunk(pcm)
            if out:
                yield out
        tail = await enc.close()
        if tail:
            yield tail
    """

    content_type: str
    sample_rate: int

    async def start(self) -> None: ...
    async def encode_chunk(self, pcm_bytes: bytes) -> bytes: ...
    async def close(self) -> bytes: ...


class Pcm16PassthroughEncoder:
    """Raw int16 PCM — no encoding. The client is responsible for
    knowing the sample rate (we expose it via the `X-NV-Sample-Rate`
    response header)."""

    content_type = "application/octet-stream"

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate

    async def start(self) -> None:
        return

    async def encode_chunk(self, pcm_bytes: bytes) -> bytes:
        return pcm_bytes

    async def close(self) -> bytes:
        return b""


class _FfmpegStreamEncoder:
    """Base ffmpeg subprocess streaming encoder.

    Spawns one ffmpeg process per request, pipes PCM into stdin,
    reads encoded bytes from stdout as they appear. ffmpeg handles
    framing for us — for mp3 each frame is self-contained, for opus
    each OGG page is written when ready.

    Subclasses provide the codec-specific argv tail (codec + bitrate
    + container format).
    """

    content_type: str = ""  # subclass

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._out_buffer: bytearray = bytearray()
        self._lock = asyncio.Lock()
        self._closed = False

    def _ffmpeg_argv_tail(self) -> list[str]:
        raise NotImplementedError

    async def start(self) -> None:
        if self._proc is not None:
            return
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise EncoderError(
                "ffmpeg not found on PATH — install ffmpeg on the "
                "gateway image (deploy/gateway.Dockerfile already "
                "installs it; if you see this in dev, run `apt install "
                "ffmpeg` or equivalent)"
            )

        # `-hide_banner -loglevel error` keeps stderr quiet unless
        # something actually fails. `-nostdin` prevents ffmpeg from
        # trying to read terminal input. `-fflags +nobuffer` cuts
        # ffmpeg's internal buffer so output frames hit our reader
        # promptly. `-flush_packets 1` makes the muxer write each
        # packet as soon as it's ready (matters for opus/ogg).
        argv = [
            ffmpeg,
            "-hide_banner", "-loglevel", "error", "-nostdin",
            "-fflags", "+nobuffer",
            "-f", "s16le",
            "-ar", str(self.sample_rate),
            "-ac", "1",
            "-i", "pipe:0",
            "-flush_packets", "1",
            *self._ffmpeg_argv_tail(),
            "pipe:1",
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(
            self._read_stdout(), name=f"ffmpeg-reader-{self.content_type}",
        )

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                # 4 KiB read sizes — small enough that the first frame
                # gets pushed out quickly, big enough not to thrash
                # the event loop on long streams.
                buf = await self._proc.stdout.read(4096)
                if not buf:
                    return
                async with self._lock:
                    self._out_buffer.extend(buf)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("ffmpeg stdout reader crashed (%s)", self.content_type)

    async def _drain(self) -> bytes:
        async with self._lock:
            if not self._out_buffer:
                return b""
            out = bytes(self._out_buffer)
            self._out_buffer.clear()
            return out

    async def encode_chunk(self, pcm_bytes: bytes) -> bytes:
        if self._proc is None or self._proc.stdin is None:
            raise EncoderError("encoder not started")
        if not pcm_bytes:
            return await self._drain()
        try:
            self._proc.stdin.write(pcm_bytes)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            # ffmpeg died — surface stderr if we can read it quickly.
            await self._raise_with_stderr(e)
        # Give the reader a tick to pick up any encoded bytes ffmpeg
        # has produced from the PCM we just pushed. asyncio.sleep(0)
        # is a single-tick yield — much cheaper than a real sleep,
        # but enough for the reader coroutine to drain stdout.
        await asyncio.sleep(0)
        return await self._drain()

    async def _raise_with_stderr(self, original: BaseException) -> None:
        stderr_tail = b""
        if self._proc is not None and self._proc.stderr is not None:
            with _suppress(asyncio.TimeoutError, Exception):
                stderr_tail = await asyncio.wait_for(
                    self._proc.stderr.read(2048), timeout=0.5,
                )
        raise EncoderError(
            f"ffmpeg pipe broke ({original!r}); stderr tail: "
            f"{stderr_tail!r}"
        ) from original

    async def close(self) -> bytes:
        if self._proc is None:
            return b""
        if self._closed:
            return await self._drain()
        self._closed = True

        # Closing stdin tells ffmpeg "no more input"; it then flushes
        # any buffered frames + writes the container trailer and exits.
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
                # Wait for stdin to actually close. wait_closed is
                # available on Python 3.7+.
                with _suppress(Exception):
                    await self._proc.stdin.wait_closed()
        except Exception:
            logger.exception("ffmpeg stdin close failed (%s)", self.content_type)

        # Wait for the reader to drain stdout fully + ffmpeg to exit.
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("ffmpeg did not exit in 5s — killing (%s)", self.content_type)
            with _suppress(Exception):
                self._proc.kill()
                await self._proc.wait()

        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._reader_task.cancel()

        if self._proc.returncode and self._proc.returncode != 0:
            stderr = b""
            if self._proc.stderr is not None:
                with _suppress(Exception):
                    stderr = await self._proc.stderr.read()
            logger.warning(
                "ffmpeg exit code %s (%s); stderr tail: %r",
                self._proc.returncode, self.content_type, stderr[-2048:],
            )

        return await self._drain()


class _suppress:  # noqa: N801 — small helper, lowercased context manager
    """`contextlib.suppress` without the import (avoids a top-level
    `import contextlib` for a one-line use). Equivalent semantics."""

    def __init__(self, *excs: type[BaseException]) -> None:
        self.excs = excs or (Exception,)

    def __enter__(self) -> None:
        return None

    def __exit__(self, et, ev, tb) -> bool:
        return et is not None and issubclass(et, self.excs)


class Mp3Encoder(_FfmpegStreamEncoder):
    """libmp3lame at 128 kbps CBR — sane balance of quality/size for
    voice. Frame-based; concatenation produces a valid mp3 file.

    Header note: mp3 doesn't need a global header for playability;
    each frame carries its own sync word + parameters. Some players
    do better with an ID3v2 tag up front; ffmpeg adds a small one by
    default."""

    content_type = "audio/mpeg"

    def _ffmpeg_argv_tail(self) -> list[str]:
        return [
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            "-f", "mp3",
        ]


class OpusEncoder(_FfmpegStreamEncoder):
    """libopus at 64 kbps inside an OGG container — ElevenLabs/MiniMax
    grade compression for voice. ~10x smaller than WAV at perceptually
    transparent voice quality.

    OGG framing means concatenating multiple opus streams is NOT safe
    (each stream has its own header pages); the per-request long-pipe
    design here produces ONE OGG stream end-to-end, so this is fine."""

    content_type = "audio/ogg"

    def _ffmpeg_argv_tail(self) -> list[str]:
        return [
            "-c:a", "libopus",
            "-b:a", "64k",
            "-vbr", "on",
            "-application", "voip",  # tuned for voice; lower latency than 'audio'
            "-f", "ogg",
        ]


# Format → encoder class registry. `wav` is intentionally absent here:
# the streaming WAV path uses the inline "infinite-size" RIFF trick in
# `server.main._yield_wav` and the sync WAV path uses
# `audio.wav.pcm16_to_wav_bytes`. Streaming WAV via this encoder layer
# would just duplicate that trick.
_REGISTRY: dict[str, type[StreamEncoder]] = {
    "pcm16": Pcm16PassthroughEncoder,
    "mp3": Mp3Encoder,
    "opus": OpusEncoder,
}


def get_stream_encoder(audio_format: str, *, sample_rate: int) -> StreamEncoder:
    """Look up an encoder class by `audio_format` and instantiate it.

    Raises `KeyError` for unknown formats — callers should validate
    `audio_format` against their pydantic schema *before* calling here.
    """
    cls = _REGISTRY.get(audio_format)
    if cls is None:
        raise KeyError(
            f"unsupported audio_format {audio_format!r}; "
            f"known: {sorted(_REGISTRY)}"
        )
    return cls(sample_rate=sample_rate)


SUPPORTED_STREAM_FORMATS = ("pcm16", "wav", "mp3", "opus")
"""Public list of formats the streaming endpoint accepts. `wav` is
handled inline in the gateway (StreamingResponse + RIFF infinity
trick) and is included here so the schema can advertise the full
surface to clients."""


__all__ = [
    "SUPPORTED_STREAM_FORMATS",
    "EncoderError",
    "Mp3Encoder",
    "OpusEncoder",
    "Pcm16PassthroughEncoder",
    "StreamEncoder",
    "get_stream_encoder",
]
