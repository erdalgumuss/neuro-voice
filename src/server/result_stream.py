"""Gateway-side consumer of the per-request result stream.

Mirror of `worker.pipeline.publish_chunk/final/error` — the worker
writes chunks here, the gateway reads them here. Lives in the server
package because it's purely an HTTP-response concern; nothing in
src/worker/ imports this.

Flow (sync `/v1/tts` proxy, `/v1/tts/stream`):

    1. Gateway XADD job to nqai.tts.jobs (already in main.py)
    2. Gateway awaits chunks on nqai.tts.results.{rid} via XREAD BLOCK
    3. Each TtsResult chunk is yielded — caller can concat (sync) or
       push to client (WS / chunked HTTP)
    4. Final chunk OR error chunk ends the stream; gateway DELs the
       stream key to free Redis memory immediately
    5. Optional overall timeout — bail with an error event so the
       client doesn't hang on a dead worker

Failure surface:
  * Worker never wrote anything → caller times out (signal to retry
    via XAUTOCLAIM or to surface a 504 to the client)
  * Worker wrote an error chunk → caller sees `TtsResult.error` and
    can map to a 4xx/5xx
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from .queue import TtsResult, result_stream_name

logger = logging.getLogger("nqai_voice.server.result_stream")


class ResultStreamTimeout(Exception):
    """No final chunk arrived within the deadline. Most likely the
    worker died mid-job; XAUTOCLAIM should hand the job to another
    worker. Caller should map this to a 504 or retry."""


async def consume_result_stream(
    redis: Redis,
    request_id: str,
    *,
    block_ms: int = 200,
    overall_timeout_s: float = 30.0,
    delete_on_finish: bool = True,
) -> AsyncIterator[TtsResult]:
    """Yield `TtsResult` chunks until `final=True` or `error` is set.

    Cleanup: after the terminating chunk, the stream is `DEL`'d so
    Redis doesn't carry the per-request key forever. Worker also sets
    `EXPIRE 600` as a safety net in case the gateway crashes here.

    Why block_ms=200 default: a server-side BLOCK 200 ms naturally
    yields to the asyncio loop while still letting the worker's chunk
    push surface within one block window (worker XADD takes < 1 ms).
    Production tuning: lower for tighter TTFB, higher for fewer
    socket roundtrips.
    """
    stream = result_stream_name(request_id)
    last_id = "0"
    # asyncio.get_running_loop() (not get_event_loop) — the latter is
    # deprecated for use inside a running coroutine since 3.10 and will
    # raise on 3.12 in threads without a current loop (audit L2 fix).
    deadline = asyncio.get_running_loop().time() + overall_timeout_s
    # `seen_seq` is scoped to the current attempt epoch. When the worker
    # retries (XAUTOCLAIM hands the job to a fresh delivery), it deletes
    # the stream and republishes from seq=0 with attempt=N+1. The gateway
    # sees the attempt advance and resets dedupe so the retry's audio
    # actually reaches the client — the pre-fix behaviour dropped
    # everything as "duplicate seq" (audit L3 B3 2026-05-25, pinned wrong
    # in tests/test_result_stream.py before the fix).
    current_attempt = -1
    seen_seq: set[int] = set()
    cleaned_up = False

    async def _cleanup() -> None:
        nonlocal cleaned_up
        if delete_on_finish and not cleaned_up:
            try:
                await redis.delete(stream)
            finally:
                cleaned_up = True

    try:
        while True:
            if asyncio.get_running_loop().time() > deadline:
                await _cleanup()
                raise ResultStreamTimeout(
                    f"no final chunk on {stream} within {overall_timeout_s}s"
                )

            # XREAD on a single stream — block_ms slice so we yield to the
            # event loop frequently and the overall_timeout check is tight.
            response: Any = await redis.xread(
                streams={stream: last_id},
                count=64,
                block=block_ms,
            )
            if not response:
                # Empty slice — loop and re-check the deadline. Real Redis
                # blocked server-side for `block_ms`; fakeredis short-
                # circuits, so we add a tiny sleep to yield in that case.
                await asyncio.sleep(block_ms / 1000.0 if block_ms > 0 else 0)
                continue

            # response = [(stream_name, [(entry_id, fields), ...])]
            for _stream_name, entries in response:
                for entry_id, fields in entries:
                    # Track last_id so we don't re-read entries — XREAD
                    # returns entries strictly greater than last_id.
                    last_id = (
                        entry_id if isinstance(entry_id, str) else entry_id.decode()
                    )
                    chunk = TtsResult.decode(fields)
                    is_terminal = bool(chunk.error or chunk.final)
                    if chunk.attempt > current_attempt:
                        # Fresh worker attempt (XAUTOCLAIM retry, or
                        # the first attempt of the lifetime — bootstrap
                        # case where current_attempt starts at -1 and
                        # the first chunk has attempt=0 or 1).
                        if current_attempt >= 0:
                            logger.info(
                                "result stream attempt advanced stream=%s "
                                "%s → %s — resetting dedupe",
                                stream, current_attempt, chunk.attempt,
                            )
                        current_attempt = chunk.attempt
                        seen_seq.clear()
                    elif chunk.attempt < current_attempt:
                        # Late chunk from a superseded attempt — drop;
                        # only the latest attempt's audio reaches the
                        # client.
                        logger.info(
                            "stale-attempt chunk dropped stream=%s "
                            "attempt=%s current=%s seq=%s",
                            stream, chunk.attempt, current_attempt, chunk.seq,
                        )
                        continue
                    if chunk.seq in seen_seq and not is_terminal:
                        logger.info(
                            "duplicate result seq ignored stream=%s "
                            "attempt=%s seq=%s",
                            stream, chunk.attempt, chunk.seq,
                        )
                        continue
                    if not is_terminal:
                        seen_seq.add(chunk.seq)
                    yield chunk
                    if is_terminal:
                        await _cleanup()
                        return
    finally:
        # Normal terminal paths have already cleaned; this covers client
        # cancellation/disconnect while the worker is still writing.
        await _cleanup()


async def collect_pcm_until_final(
    redis: Redis,
    request_id: str,
    **kwargs,
) -> tuple[bytes, int, str | None]:
    """Convenience for the sync `/v1/tts` proxy path: drain the result
    stream, return (full_pcm, sentence_count, error_message_or_none).

    Sentence count = number of NON-final, NON-error chunks (i.e.
    actual audio chunks). Error chunks short-circuit with the message.

    Attempt-aware reset: when `chunk.attempt` advances mid-stream (a
    XAUTOCLAIM retry started after the previous attempt died), the
    sync buffer DISCARDS what it had collected so far so the returned
    WAV is only the latest attempt's audio. The streaming
    (`/v1/tts/stream`) path can't take back already-yielded bytes; this
    helper has full control until it returns one body to the client.
    """
    pcm_buffer = bytearray()
    sentences = 0
    error_msg: str | None = None
    current_attempt = -1

    async for chunk in consume_result_stream(redis, request_id, **kwargs):
        if chunk.attempt > current_attempt:
            # Retry started — drop everything from the dead attempt.
            if current_attempt >= 0:
                logger.info(
                    "sync drain reset on attempt advance %s → %s "
                    "(dropping %d bytes from prior attempt)",
                    current_attempt, chunk.attempt, len(pcm_buffer),
                )
            current_attempt = chunk.attempt
            pcm_buffer.clear()
            sentences = 0
        if chunk.error:
            error_msg = chunk.error
            break
        if chunk.final:
            break
        pcm_buffer.extend(chunk.pcm_bytes)
        sentences += 1

    return bytes(pcm_buffer), sentences, error_msg
