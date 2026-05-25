"""Streaming TTS worker bridge — sync engine generator → async chunks.

The durable B.1 pipeline could afford `list(engine.synthesize_stream())`
and emit chunks after full inference. Streaming cannot: the gateway
needs to forward the first PCM frame to the HTTP client as soon as
the engine yields a sentence's worth of audio, not after the whole
request completes.

`iter_engine_chunks` runs the blocking sync generator on a thread and
pushes each `SynthChunk` onto an asyncio queue. The pipeline coroutine
consumes chunks one at a time and `publish_chunk`s each to the
per-request result stream immediately. This module deliberately
contains NO media-sink abstraction — the production "sink" is
`worker.pipeline.publish_chunk` writing to `nqai.tts.results.{rid}`.

Cleanup (audit L3 medium 2026-05-25): pre-cleanup this file also
hosted `iter_live_audio_frames`, `run_live_synthesis`, the
`LiveMediaSink` Protocol, and `InMemoryLiveMediaSink` — all WebRTC-
era scaffolding that the production HTTP-chunked path never used. The
production pipeline only calls `iter_engine_chunks`; the rest was
load-bearing on its own tests. Removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from .engine import BaseSynthEngine

if TYPE_CHECKING:
    # Forward reference only — `worker.pipeline` imports `worker.live`
    # at runtime, so we must NOT import VoiceView at module-load time.
    from .pipeline import VoiceView

logger = logging.getLogger("nqai_voice.worker.live")


async def iter_engine_chunks(
    engine: BaseSynthEngine,
    *,
    text: str,
    voice: VoiceView,
    reference_path: Path,
    language_id: str = "tr",
    cancel_event: asyncio.Event | None = None,
    engine_overrides: dict[str, float | int] | None = None,
    request_meta: dict[str, object] | None = None,
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

    Cancellation semantics (audit L3 H1 2026-05-25)
    -----------------------------------------------
    `cancel_event` is BEST-EFFORT. The producer thread checks it
    BETWEEN yields, not inside a single `model.generate(...)` call.
    For VoxCPM2 that means a cancel signal during inference will be
    observed only after the current sentence finishes (multi-second
    latency on cold worker). The Python C-API doesn't expose a way
    to kill a thread mid-torch-op; honouring cancellation strictly
    would require either a fork-per-job worker model OR an engine
    API that takes a cancel-token. Both are big refactors.

    What we DO guarantee:
    * `_put_safe` swallows RuntimeError from `call_soon_threadsafe`
      when the consumer side has already torn down its loop, so the
      thread leak is bounded to "current inference call" and does
      not surface a noisy exception on shutdown.
    * The finally block sets `cancel_event` so the NEXT sentence
      iteration short-circuits — bounded leak.
    * `asyncio.wait_for(producer_task, timeout=1.0)` does NOT kill
      the thread; it stops AWAITING it. The thread will finish its
      current torch call and exit naturally.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[object | BaseException | None] = asyncio.Queue()

    def _put_safe(item: object | BaseException | None) -> None:
        """Resilient queue write from the producer thread.

        If the consumer's loop has already closed (process shutdown,
        cancel + tear-down race), `call_soon_threadsafe` raises
        RuntimeError. Swallow it — the producer thread is about to
        exit anyway and there is nothing useful we could do with the
        chunk."""
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, item)

    def _producer() -> None:
        try:
            for chunk in engine.synthesize_stream(
                text=text,
                voice=voice,
                reference_path=reference_path,
                language_id=language_id,
                engine_overrides=engine_overrides,
                request_meta=request_meta,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return
                _put_safe(chunk)
        except BaseException as exc:
            _put_safe(exc)
        finally:
            _put_safe(None)

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


# NOTE: Pre-cleanup this module also exposed iter_live_audio_frames /
# run_live_synthesis / LiveMediaSink / InMemoryLiveMediaSink for a
# WebRTC-style transport that we explicitly decided not to ship in
# the TTS API surface (decision-log 2026-05-24, "WebRTC/LiveKit
# scaffold drop"). When duplex voice-agent transports do come back
# they will live in a separate product surface (NIVA call-center,
# etc.) with their OWN sink type — re-introducing them here would
# couple two unrelated product surfaces.
