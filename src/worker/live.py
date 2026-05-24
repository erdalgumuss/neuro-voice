"""Streaming TTS worker helpers — frame-by-frame audio bridge.

The durable B.1 pipeline could afford `list(engine.synthesize_stream())`
and emit chunks after the full inference. Streaming cannot: the gateway
needs to forward the first PCM frame to the HTTP client (or future
WebSocket consumer) as soon as the engine yields a sentence's worth
of audio, not after the whole request completes.

`iter_live_audio_frames` runs the blocking sync generator on a thread,
splits each model chunk into fixed-duration PCM frames, and pushes
them onto an asyncio queue. The coroutine yields each frame to the
caller, which can XADD it onto the per-request result stream
immediately.

The `LiveMediaSink` protocol + `InMemoryLiveMediaSink` reference
implementation are kept for tests of `run_live_synthesis`, which is
itself an interface the worker pipeline can lean on if we ever ship
a sink that talks to a non-Redis transport (the deleted WebRTC scaffold
used to live here). For B.1.5 the production sink is "XADD onto
nqai.tts.results.{rid}" via `worker.pipeline.publish_chunk` — there is
no media-sink in the streaming HTTP path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from live import (
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    LiveAudioFrame,
    LiveLatencyWaterfall,
    split_pcm16_frames,
)

from .engine import BaseSynthEngine

if TYPE_CHECKING:
    # Forward reference only — `worker.pipeline` imports `worker.live`
    # at runtime, so we must NOT import VoiceView at module-load time.
    from .pipeline import VoiceView

logger = logging.getLogger("nqai_voice.worker.live")


class LiveMediaSink(Protocol):
    """Outbound media bridge — frames + control events to a client.

    Implementations might be a test sink (in-memory), a WebSocket
    writer, or (in a future product surface) a WebRTC publisher. The
    streaming HTTP path does NOT use a sink — it writes chunks
    directly to the per-request Redis result stream via
    `worker.pipeline.publish_chunk`.
    """

    async def send_audio_frame(self, frame: LiveAudioFrame) -> None: ...
    async def send_control(self, kind: str, payload: dict) -> None: ...
    async def close(self) -> None: ...


class InMemoryLiveMediaSink:
    """Test/dev sink — records outbound events for assertions."""

    def __init__(self) -> None:
        self.audio_frames: list[LiveAudioFrame] = []
        self.control_events: list[tuple[str, dict]] = []
        self.closed = False

    async def send_audio_frame(self, frame: LiveAudioFrame) -> None:
        self.audio_frames.append(frame)

    async def send_control(self, kind: str, payload: dict) -> None:
        self.control_events.append((kind, payload))

    async def close(self) -> None:
        self.closed = True


async def iter_engine_chunks(
    engine: BaseSynthEngine,
    *,
    text: str,
    voice: VoiceView,
    reference_path: Path,
    language_id: str = "tr",
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[object]:
    """Bridge the blocking sync engine generator into async.

    The engine runs on a thread; each `SynthChunk` it yields is pushed
    onto an asyncio queue immediately. The caller consumes chunks one
    at a time and can XADD / write each as soon as the engine produces
    it — no `list(...)` drain.

    Returns the raw SynthChunk objects (not LiveAudioFrame), so the
    caller controls framing. For HTTP chunked streaming we publish
    one sentence-chunk per result-stream entry; for WebRTC we'd
    split into 20ms frames via `split_pcm16_frames` instead.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[object | BaseException | None] = asyncio.Queue()

    def _put(item: object | BaseException | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def _producer() -> None:
        try:
            for chunk in engine.synthesize_stream(
                text=text,
                voice=voice,
                reference_path=reference_path,
                language_id=language_id,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return
                _put(chunk)
        except BaseException as exc:
            _put(exc)
        finally:
            _put(None)

    producer_task = asyncio.create_task(asyncio.to_thread(_producer))
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if cancel_event is not None:
            cancel_event.set()
        if not producer_task.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(producer_task, timeout=1.0)
        else:
            await producer_task


async def iter_live_audio_frames(
    engine: BaseSynthEngine,
    *,
    text: str,
    voice: VoiceView,
    reference_path: Path,
    language_id: str = "tr",
    frame_ms: int = DEFAULT_FRAME_MS,
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[LiveAudioFrame]:
    """Same as `iter_engine_chunks` but emits fixed-duration
    `LiveAudioFrame` objects (default 20ms) for transports that need
    constant frame rate. The HTTP chunked path uses `iter_engine_chunks`
    directly; WebRTC-style transports use this."""
    seq = 0
    async for chunk in iter_engine_chunks(
        engine, text=text, voice=voice, reference_path=reference_path,
        language_id=language_id, cancel_event=cancel_event,
    ):
        sample_rate = int(getattr(chunk, "sample_rate", DEFAULT_SAMPLE_RATE))
        sentence_text = getattr(chunk, "sentence_text", None)
        for pcm in split_pcm16_frames(
            chunk.pcm_int16,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
        ):
            if cancel_event is not None and cancel_event.is_set():
                return
            yield LiveAudioFrame(
                seq=seq,
                pcm_int16=pcm,
                sample_rate=sample_rate,
                duration_ms=frame_ms,
                sentence_text=sentence_text,
            )
            seq += 1


async def run_live_synthesis(
    *,
    engine: BaseSynthEngine,
    sink: LiveMediaSink,
    text: str,
    voice: VoiceView,
    reference_path: Path,
    request_id: str,
    language_id: str = "tr",
    waterfall: LiveLatencyWaterfall | None = None,
    cancel_event: asyncio.Event | None = None,
) -> LiveLatencyWaterfall:
    """Generate live audio into a media sink with canonical control events."""
    trace = waterfall or LiveLatencyWaterfall(request_id=request_id)
    trace.mark("worker_accepted_ms")
    trace.mark("reference_ready_ms")
    trace.mark("adapter_ready_ms")
    trace.mark("frontend_done_ms")
    trace.mark("model_start_ms")

    sent_first = False
    frame_count = 0
    try:
        await sink.send_control("accepted", {"request_id": request_id})
        async for frame in iter_live_audio_frames(
            engine,
            text=text,
            voice=voice,
            reference_path=reference_path,
            language_id=language_id,
            cancel_event=cancel_event,
        ):
            if cancel_event is not None and cancel_event.is_set():
                await sink.send_control("cancelled", {"request_id": request_id})
                break
            if not sent_first:
                trace.mark("model_first_audio_ms")
                trace.mark("worker_first_frame_sent_ms")
                trace.mark("gateway_or_media_first_send_ms")
                await sink.send_control(
                    "first_audio",
                    {"request_id": request_id, "seq": frame.seq},
                )
                sent_first = True
            await sink.send_audio_frame(frame)
            frame_count += 1
        trace.mark("final_audio_done_ms")
        await sink.send_control(
            "done",
            {
                "request_id": request_id,
                "frames": frame_count,
                "metrics": trace.as_dict(),
            },
        )
        return trace
    except Exception as exc:
        trace.mark("final_audio_done_ms")
        await sink.send_control(
            "error",
            {
                "request_id": request_id,
                "error": str(exc),
                "metrics": trace.as_dict(),
            },
        )
        raise
    finally:
        await sink.close()


# NOTE: LiveKit/WebRTC sink intentionally removed. NQAI Voice ships
# as a one-way streaming TTS API (text in → audio out), à la
# ElevenLabs / OpenAI / Cartesia. Duplex voice-agent transports live
# in a separate product surface and would land as a different sink
# implementation behind this same `LiveMediaSink` protocol.
