"""Worker Redis Streams consumer — XREADGROUP loop + XACK semantics.

Wraps `worker.pipeline.process_one_job` in the canonical Redis Streams
consumption pattern:

    boot         → XGROUP CREATE MKSTREAM (idempotent — BUSYGROUP ok)
    each tick    → XREADGROUP group=tts-workers consumer=<name>
                              streams={nqai.tts.jobs: ">"}
                              count=1 block=NQAI_WORKER_BLOCK_MS
    on a job     → process_one_job(...) → see XACK matrix below
    on no jobs   → XAUTOCLAIM scan for stale messages from dead workers

XACK matrix (D-06 at-least-once):

    process_one_job returns          → XACK   (success, never retry)
    PoisonJob raised                 → XACK   (voice/ref missing —
                                                 retry won't help, free
                                                 the message slot)
    TransientFailure raised          → NO XACK (engine/DB hiccup —
                                                 XAUTOCLAIM will hand it
                                                 to another worker)
    Unknown Exception bubbled        → NO XACK (safer to retry than to
                                                 drop a job on an
                                                 unanticipated bug)

XAUTOCLAIM is deliberately conservative: after `idle_ms` of no progress
on a message in PEL, it migrates to this consumer for another attempt.
Faz C adds a DLQ for messages that fail past N retries.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from server.queue import DEFAULT_STREAM, TtsJobPayload

from .engine import BaseSynthEngine
from .pipeline import (
    ArchiveCallable as _ArchiveCallable,
)
from .pipeline import (
    PoisonJob,
    ReferenceResolver,
    TransientFailure,
    process_one_job,
)

logger = logging.getLogger("nqai_voice.worker.consumer")

DEFAULT_GROUP = "tts-workers"


def _default_consumer_name() -> str:
    """`worker-<short-hostname>-<pid>` — distinguishable across replicas
    while staying readable in `XINFO CONSUMERS` output."""
    base = os.environ.get("NQAI_WORKER_CONSUMER_NAME")
    if base:
        return base
    host = socket.gethostname().split(".")[0][:24]
    return f"worker-{host}-{os.getpid()}"


async def ensure_consumer_group(
    redis: Redis, *, stream: str, group: str,
) -> None:
    """Create the consumer group + stream if absent. `BUSYGROUP` (group
    exists) is the success path — silently ignore.

    `id="0"` (not `"$"`) so a fresh group sees ALL existing messages on
    the stream, not just future XADDs. This matters because:
      * Tests enqueue jobs THEN start a consumer — `$` would make them
        invisible.
      * In production, if every worker crashes simultaneously, the
        group's read pointer survives, but if the group itself was
        deleted (operator intervention, manual schema reset) a fresh
        worker must still drain whatever was queued during the outage.
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        logger.info("created consumer group %s on %s (from id=0)",
                    group, stream)
    except ResponseError as e:
        msg = str(e)
        if "BUSYGROUP" not in msg:
            raise
        # Group already exists — that's fine. Faz B.0 worker restarts
        # hit this path on every boot.


class WorkerConsumer:
    """Stateful consumer wrapping one Redis Streams group + one engine.

    Lifecycle:
        c = WorkerConsumer(...)
        await c.run()           # blocks until stop_event is set
        # or
        await c.run(max_iterations=N)   # bounded — useful in tests

    Each consumer instance maps to one worker process; the
    `consumer_name` distinguishes replicas within the group so PEL +
    XAUTOCLAIM see them as distinct.
    """

    def __init__(
        self,
        *,
        redis: Redis,
        engine: BaseSynthEngine,
        stream: str = DEFAULT_STREAM,
        group: str = DEFAULT_GROUP,
        consumer_name: str | None = None,
        block_ms: int = 5_000,
        xautoclaim_min_idle_ms: int = 30_000,
        resolve_reference: ReferenceResolver | None = None,
        archive_to_r2: _ArchiveCallable | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._redis = redis
        self._engine = engine
        self._stream = stream
        self._group = group
        self._consumer_name = consumer_name or _default_consumer_name()
        self._block_ms = block_ms
        self._xautoclaim_idle_ms = xautoclaim_min_idle_ms
        self._resolve_reference = resolve_reference
        self._archive_to_r2 = archive_to_r2
        self._stop = stop_event or asyncio.Event()
        # Test/observability counters — incremented per outcome class.
        self.acked = 0
        self.poisoned = 0
        self.transient_failures = 0
        self.unknown_failures = 0
        self.claimed = 0

    @property
    def consumer_name(self) -> str:
        return self._consumer_name

    def stop(self) -> None:
        """Signal graceful shutdown. The current job (if any) finishes
        and gets XACK'd or not per the failure matrix, then run() exits."""
        self._stop.set()

    async def run(self, *, max_iterations: int | None = None) -> None:
        """Main loop. Returns when `stop_event` fires or `max_iterations`
        is reached (whichever first).

        Idle yield: real Redis's XREADGROUP BLOCK 100 sleeps server-side,
        which naturally yields to other asyncio tasks. fakeredis returns
        immediately regardless of BLOCK, so without an explicit
        `asyncio.sleep` after an empty tick the loop would hot-spin and
        starve the stop_event task. The sleep duration mirrors block_ms
        so production behaviour stays consistent (one tick ≈ block_ms
        of latency before stop-check or XAUTOCLAIM)."""
        await ensure_consumer_group(
            self._redis, stream=self._stream, group=self._group,
        )
        iters = 0
        idle_yield_s = max(self._block_ms, 1) / 1000.0
        while not self._stop.is_set():
            if max_iterations is not None and iters >= max_iterations:
                return
            iters += 1
            try:
                handled = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("tick raised unhandled exception")
                handled = False
            if self._stop.is_set():
                return
            if not handled:
                # Idle slice — XAUTOCLAIM stale work, then yield so the
                # stop event (or any other task) gets scheduled.
                await self._xautoclaim_sweep()
                await asyncio.sleep(idle_yield_s)

    async def _tick(self) -> bool:
        """Consume at most one message. Returns True if a message was
        pulled (regardless of XACK outcome), False on timeout."""
        try:
            response = await self._redis.xreadgroup(
                groupname=self._group,
                consumername=self._consumer_name,
                streams={self._stream: ">"},
                count=1,
                block=self._block_ms,
            )
        except ResponseError as e:
            # Stream got deleted out from under us, or the group went
            # away — log and back off so we don't hot-loop on errors.
            logger.warning("xreadgroup ResponseError on %s: %s", self._stream, e)
            await asyncio.sleep(1)
            return False

        if not response:
            return False

        # response = [(stream_name, [(entry_id, {field: value, ...}), ...])]
        for _stream_name, entries in response:
            for entry_id, fields in entries:
                await self._handle_one(entry_id, fields)
        return True

    async def _handle_one(self, entry_id, fields) -> None:
        rid_str: str | None = None
        try:
            job = TtsJobPayload.decode(fields)
            rid_str = job.request_id
        except Exception:
            # Malformed payload — the only safe thing is to XACK so it
            # doesn't loop forever, and log loudly for the operator.
            logger.exception(
                "malformed job payload on entry %s — XACKing to drain", entry_id,
            )
            await self._ack(entry_id)
            self.poisoned += 1
            return

        try:
            await process_one_job(
                job,
                redis=self._redis,
                engine=self._engine,
                resolve_reference=self._resolve_reference,
                archive_to_r2=self._archive_to_r2,
            )
        except PoisonJob:
            # No point retrying — voice missing, ref missing, etc.
            # Pipeline already published an error chunk + idem.fail().
            logger.warning("poison job rid=%s — XACK to drain", rid_str)
            await self._ack(entry_id)
            self.poisoned += 1
            return
        except TransientFailure:
            # Engine/DB hiccup. DO NOT XACK — XAUTOCLAIM will retry on
            # this or another worker after idle_ms.
            logger.info("transient failure rid=%s — leaving in PEL", rid_str)
            self.transient_failures += 1
            return
        except Exception:
            # Unanticipated. Safer to keep the message in PEL than to
            # XACK and silently lose work. Operator will see the trace.
            logger.exception(
                "unexpected exception processing rid=%s — leaving in PEL",
                rid_str,
            )
            self.unknown_failures += 1
            return

        await self._ack(entry_id)
        self.acked += 1

    async def _ack(self, entry_id) -> None:
        try:
            await self._redis.xack(self._stream, self._group, entry_id)
        except ResponseError:
            logger.exception("xack failed for entry %s", entry_id)

    async def _xautoclaim_sweep(self) -> None:
        """Claim any pending message older than `idle_ms` so a crashed
        worker doesn't strand jobs. We claim at most 1 per sweep to keep
        steady-state behaviour close to FIFO."""
        try:
            response = await self._redis.xautoclaim(
                name=self._stream,
                groupname=self._group,
                consumername=self._consumer_name,
                min_idle_time=self._xautoclaim_idle_ms,
                count=1,
            )
        except ResponseError as e:
            logger.warning("xautoclaim ResponseError: %s", e)
            return

        # redis-py xautoclaim returns
        # (next_cursor, claimed_entries[, deleted_ids])
        if not response or len(response) < 2:
            return
        _next_cursor, claimed = response[0], response[1]
        for entry_id, fields in claimed:
            self.claimed += 1
            logger.info("xautoclaim picked up %s (consumer=%s)",
                        entry_id, self._consumer_name)
            await self._handle_one(entry_id, fields)


async def run_consumer_once(
    redis: Redis, engine: BaseSynthEngine, *,
    stream: str = DEFAULT_STREAM, group: str = DEFAULT_GROUP, **kwargs,
) -> WorkerConsumer:
    """Convenience: build, run one tick, return the consumer (for tests)."""
    c = WorkerConsumer(redis=redis, engine=engine, stream=stream, group=group, **kwargs)
    await ensure_consumer_group(redis, stream=stream, group=group)
    await c._tick()
    return c


__all__ = [
    "WorkerConsumer",
    "ensure_consumer_group",
    "run_consumer_once",
    "DEFAULT_GROUP",
]
