"""Live TTS worker helpers.

The durable B.1 pipeline can afford to generate a whole request then publish.
The live path cannot. This module bridges the existing blocking
`engine.synthesize_stream(...)` generator into an async frame stream so the
first playable PCM frame can leave the worker before full generation
completes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from live import (
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    LiveAudioFrame,
    LiveLatencyWaterfall,
    split_pcm16_frames,
)

from .engine import BaseSynthEngine
from .pipeline import VoiceView

logger = logging.getLogger("nqai_voice.worker.live")


class LiveMediaSink(Protocol):
    async def send_audio_frame(self, frame: LiveAudioFrame) -> None: ...
    async def send_control(self, kind: str, payload: dict) -> None: ...
    async def close(self) -> None: ...


class InMemoryLiveMediaSink:
    """Test/dev sink that records audio/control events in memory."""

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
    """Yield PCM frames as soon as the blocking engine yields chunks.

    The producer thread never builds a list of all chunks. It pushes each
    model chunk into the asyncio queue immediately, split into 20ms frames.
    If the engine only supports sentence-level chunks today, the first
    sentence still flows before later sentences are generated.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[LiveAudioFrame | BaseException | None] = asyncio.Queue()

    def _put(item: LiveAudioFrame | BaseException | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def _producer() -> None:
        seq = 0
        try:
            for chunk in engine.synthesize_stream(
                text=text,
                voice=voice,
                reference_path=reference_path,
                language_id=language_id,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return
                sample_rate = int(getattr(chunk, "sample_rate", DEFAULT_SAMPLE_RATE))
                sentence_text = getattr(chunk, "sentence_text", None)
                for pcm in split_pcm16_frames(
                    chunk.pcm_int16,
                    sample_rate=sample_rate,
                    frame_ms=frame_ms,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    _put(
                        LiveAudioFrame(
                            seq=seq,
                            pcm_int16=pcm,
                            sample_rate=sample_rate,
                            duration_ms=frame_ms,
                            sentence_text=sentence_text,
                        )
                    )
                    seq += 1
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


class LiveKitMediaSink:
    """LiveKit-backed media sink.

    Optional runtime dependency: install `livekit` for real WebRTC publishing.
    Unit tests use `InMemoryLiveMediaSink`, so gateway/worker imports stay light.
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = 1,
    ) -> None:
        self._url = url
        self._token = token
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._room = None
        self._source = None

    async def connect(self) -> None:
        try:
            from livekit import rtc
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install livekit to use LiveKitMediaSink") from exc

        room = rtc.Room()
        await room.connect(self._url, self._token)
        source = rtc.AudioSource(self._sample_rate, self._num_channels)
        track = rtc.LocalAudioTrack.create_audio_track("nqai-tts", source)
        await room.local_participant.publish_track(track)
        self._room = room
        self._source = source

    async def send_audio_frame(self, frame: LiveAudioFrame) -> None:
        if self._source is None:
            await self.connect()
        from livekit import rtc

        audio_frame = rtc.AudioFrame.create(
            sample_rate=frame.sample_rate,
            num_channels=1,
            samples_per_channel=frame.samples_per_channel,
        )
        audio_frame.data.cast("B")[:len(frame.pcm_int16)] = frame.pcm_int16
        await self._source.capture_frame(audio_frame)

    async def send_control(self, kind: str, payload: dict) -> None:
        if self._room is None:
            await self.connect()
        import json

        data = json.dumps({"type": kind, **payload}, ensure_ascii=False).encode("utf-8")
        await self._room.local_participant.publish_data(data, reliable=True)

    async def close(self) -> None:
        if self._room is not None:
            await self._room.disconnect()
            self._room = None
            self._source = None
