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
    Unknown Exception bubbled        → NO XACK until max retries, then
                                       terminal failure + DLQ + XACK

XAUTOCLAIM is deliberately conservative: after `idle_ms` of no progress
on a message in PEL, it migrates to this consumer for another attempt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from observability import TTS_ERRORS, TTS_REQUESTS, WORKER_DLQ
from server.queue import DEFAULT_DLQ_STREAM, DEFAULT_STREAM, TtsJobPayload

from .engine import BaseSynthEngine
from .pipeline import (
    ArchiveCallable as _ArchiveCallable,
)
from .pipeline import (
    PoisonJob,
    ReferenceResolver,
    TransientFailure,
    mark_terminal_failure,
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


def _safe_metric(counter, **labels) -> None:
    """Increment a Prometheus counter without ever raising into the
    consumer hot path. Metric infrastructure must not turn into a worker
    crash vector — best-effort increments only, swallow everything."""
    try:
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        logger.exception("metric increment failed (labels=%s) — ignoring", labels)


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
        xautoclaim_period_s: float = 5.0,
        max_retries: int | None = None,
        dlq_stream: str | None = None,
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
        # Periodic sweep cadence — XAUTOCLAIM also runs under sustained
        # traffic, not just when the queue is idle (Codex audit 2026-05-24).
        # Under busy traffic, idle ticks rarely fire, so a worker crash
        # could otherwise strand a message for ages.
        self._xautoclaim_period_s = xautoclaim_period_s
        self._last_xautoclaim_at = 0.0
        self._max_retries = max_retries or int(
            os.environ.get("NQAI_WORKER_MAX_RETRIES", "3")
        )
        self._dlq_stream = dlq_stream or os.environ.get(
            "NQAI_WORKER_DLQ_STREAM", DEFAULT_DLQ_STREAM
        )
        self._resolve_reference = resolve_reference
        self._archive_to_r2 = archive_to_r2
        self._stop = stop_event or asyncio.Event()
        # Test/observability counters — incremented per outcome class.
        self.acked = 0
        self.poisoned = 0
        self.transient_failures = 0
        self.unknown_failures = 0
        self.claimed = 0
        self.dlqed = 0
        # Faz C heartbeat state — gateway aggregates these via Redis HSET
        # to decide capacity-aware admission. Single-process consumer:
        # capacity is 1 (one job in-flight at a time). NQAI_WORKER_CAPACITY
        # lets ops bump it later if/when concurrent dispatch lands.
        self.capacity: int = int(os.environ.get("NQAI_WORKER_CAPACITY", "1"))
        self.in_flight: int = 0
        self.last_pickup_ms: int = int(time.time() * 1000)
        self.started_at_ms: int = int(time.time() * 1000)

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
        loop = asyncio.get_running_loop()
        self._last_xautoclaim_at = loop.time()
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

            # XAUTOCLAIM strategy (Codex audit 2026-05-24): sweep on
            # *every* idle tick AND periodically under sustained traffic.
            # Idle alone misses crashed-worker recovery when the queue
            # never drains (a fast producer can keep us busy forever).
            now = loop.time()
            elapsed_since_sweep = now - self._last_xautoclaim_at
            if not handled or elapsed_since_sweep >= self._xautoclaim_period_s:
                await self._xautoclaim_sweep()
                self._last_xautoclaim_at = loop.time()

            if not handled:
                # Yield to other tasks (stop_event, etc.) — see docstring.
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
        # Faz C heartbeat: mark pickup time on every successful read so the
        # gateway can detect stuck workers (last_pickup_ms not advancing).
        self.last_pickup_ms = int(time.time() * 1000)
        for _stream_name, entries in response:
            for entry_id, fields in entries:
                self.in_flight += 1
                try:
                    await self._handle_one(entry_id, fields)
                finally:
                    self.in_flight = max(0, self.in_flight - 1)
        return True

    async def _handle_one(self, entry_id, fields) -> None:
        rid_str: str | None = None
        try:
            job = TtsJobPayload.decode(fields)
            rid_str = job.request_id
        except Exception as e:
            # Malformed payload — the only safe thing is to XACK so it
            # doesn't loop forever, and log loudly for the operator.
            logger.exception(
                "malformed job payload on entry %s — XACKing to drain", entry_id,
            )
            await self._send_raw_to_dlq(
                entry_id=entry_id,
                fields=fields,
                reason="malformed_payload",
                error=e,
                delivery_count=await self._delivery_count(entry_id),
            )
            await self._ack(entry_id)
            self.poisoned += 1
            self.dlqed += 1
            _safe_metric(TTS_ERRORS, type="poison")
            _safe_metric(WORKER_DLQ)
            return

        # Faz C step 1: capture the worker-pickup latency at the
        # boundary where the consumer first owns the message. The
        # gateway stamped `enqueued_at_ms` onto the payload when it
        # XADD'd the job; the difference between then and now is how
        # long the message waited in PEL before a worker began handling
        # it. None when the payload predates B.1 hardening or the field
        # is otherwise absent — keep the column NULL rather than
        # synthesising a value.
        worker_pickup_ms: int | None = None
        if job.enqueued_at_ms is not None:
            worker_pickup_ms = max(
                0, int(time.time() * 1000) - int(job.enqueued_at_ms),
            )

        # B3 fix (audit L3 2026-05-25): the retry epoch travels onto
        # every TtsResult chunk so the gateway dedupe can distinguish
        # "duplicate seq within the SAME attempt" (drop) from "seq=0
        # again on a XAUTOCLAIM-handed-off retry that reset its own
        # counter" (reset dedupe + accept). delivery_count from PEL is
        # already the authoritative retry source — see _delivery_count
        # at line 412.
        try:
            attempt = await self._delivery_count(entry_id)
        except Exception:
            logger.exception(
                "delivery_count read failed; falling back to attempt=0",
            )
            attempt = 0

        try:
            await process_one_job(
                job,
                redis=self._redis,
                engine=self._engine,
                resolve_reference=self._resolve_reference,
                archive_to_r2=self._archive_to_r2,
                worker_id=self._consumer_name,
                worker_pickup_ms=worker_pickup_ms,
                attempt=attempt,
            )
        except PoisonJob as e:
            # No point retrying — voice missing, ref missing, etc.
            # Pipeline already published an error chunk + idem.fail().
            # Archive the poisoned job to the DLQ so an operator can
            # find it for postmortem (audit L2 H1 2026-05-25 — pre-fix
            # poison jobs were XACKed without any forensic trail).
            logger.warning("poison job rid=%s — XACK to drain + DLQ", rid_str)
            try:
                await self._send_job_to_dlq(
                    entry_id=entry_id,
                    fields=fields,
                    job=job,
                    reason="poison",
                    error=e,
                    delivery_count=attempt,
                )
                self.dlqed += 1
                _safe_metric(WORKER_DLQ)
            except Exception:
                logger.exception(
                    "DLQ archive of poison rid=%s failed — XACKing anyway",
                    rid_str,
                )
            await self._ack(entry_id)
            self.poisoned += 1
            _safe_metric(TTS_ERRORS, type="poison")
            _safe_metric(
                TTS_REQUESTS,
                tenant=str(job.tenant_id),
                voice=job.voice_id,
                status="error",
            )
            return
        except TransientFailure as e:
            # Engine/DB hiccup. DO NOT XACK — XAUTOCLAIM will retry on
            # this or another worker after idle_ms, until retry budget
            # is exhausted and the job is terminally failed.
            if await self._maybe_dlq_and_ack(
                entry_id=entry_id,
                fields=fields,
                job=job,
                reason="transient_max_retries",
                error=e,
            ):
                self.dlqed += 1
                _safe_metric(WORKER_DLQ)
                _safe_metric(TTS_ERRORS, type="dlq")
                # Only emit the SLO-denominator counter once the request
                # has reached a terminal failure (DLQ). Mid-retry doesn't
                # count — the same request_id may yet succeed.
                _safe_metric(
                    TTS_REQUESTS,
                    tenant=str(job.tenant_id),
                    voice=job.voice_id,
                    status="error",
                )
            else:
                logger.info("transient failure rid=%s — leaving in PEL", rid_str)
            self.transient_failures += 1
            _safe_metric(TTS_ERRORS, type="transient")
            return
        except Exception as e:
            # Unanticipated. Safer to keep the message in PEL than to
            # XACK and silently lose work until retry budget is spent.
            logger.exception(
                "unexpected exception processing rid=%s — leaving in PEL",
                rid_str,
            )
            if await self._maybe_dlq_and_ack(
                entry_id=entry_id,
                fields=fields,
                job=job,
                reason="unknown_exception",
                error=e,
            ):
                self.dlqed += 1
                _safe_metric(WORKER_DLQ)
                _safe_metric(TTS_ERRORS, type="dlq")
                _safe_metric(
                    TTS_REQUESTS,
                    tenant=str(job.tenant_id),
                    voice=job.voice_id,
                    status="error",
                )
            self.unknown_failures += 1
            _safe_metric(TTS_ERRORS, type="unknown")
            return

        await self._ack(entry_id)
        self.acked += 1

    async def _ack(self, entry_id) -> None:
        try:
            await self._redis.xack(self._stream, self._group, entry_id)
        except ResponseError:
            logger.exception("xack failed for entry %s", entry_id)

    async def _delivery_count(self, entry_id) -> int:
        """Return Redis PEL `times_delivered` for this stream entry."""
        try:
            rows = await self._redis.xpending_range(
                self._stream, self._group, entry_id, entry_id, 1
            )
        except Exception:
            logger.exception("xpending_range failed for entry %s", entry_id)
            return 1
        if not rows:
            return 1
        return int(rows[0].get("times_delivered") or 1)

    async def _maybe_dlq_and_ack(
        self,
        *,
        entry_id,
        fields,
        job: TtsJobPayload,
        reason: str,
        error: BaseException,
    ) -> bool:
        delivery_count = await self._delivery_count(entry_id)
        if delivery_count < self._max_retries:
            return False

        logger.error(
            "job rid=%s exceeded retries delivery_count=%s max=%s reason=%s",
            job.request_id, delivery_count, self._max_retries, reason,
        )
        await mark_terminal_failure(
            job,
            redis=self._redis,
            error_code=reason,
            message=f"{reason}: {error}",
            worker_id=self._consumer_name,
            attempt=delivery_count,
        )
        await self._send_job_to_dlq(
            entry_id=entry_id,
            fields=fields,
            job=job,
            reason=reason,
            error=error,
            delivery_count=delivery_count,
        )
        await self._ack(entry_id)
        return True

    async def _send_job_to_dlq(
        self,
        *,
        entry_id,
        fields,
        job: TtsJobPayload,
        reason: str,
        error: BaseException,
        delivery_count: int,
    ) -> None:
        await self._redis.xadd(
            self._dlq_stream,
            {
                "entry_id": self._entry_id_str(entry_id),
                "request_id": job.request_id,
                "tenant_id": job.tenant_id,
                "voice_id": job.voice_id,
                "reason": reason,
                "error": str(error),
                "delivery_count": str(delivery_count),
                "consumer": self._consumer_name,
                "payload": self._payload_from_fields(fields),
            },
        )

    async def _send_raw_to_dlq(
        self,
        *,
        entry_id,
        fields,
        reason: str,
        error: BaseException,
        delivery_count: int,
    ) -> None:
        await self._redis.xadd(
            self._dlq_stream,
            {
                "entry_id": self._entry_id_str(entry_id),
                "reason": reason,
                "error": str(error),
                "delivery_count": str(delivery_count),
                "consumer": self._consumer_name,
                "payload": self._payload_from_fields(fields),
            },
        )

    @staticmethod
    def _entry_id_str(entry_id) -> str:
        return entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)

    @staticmethod
    def _payload_from_fields(fields) -> str:
        raw = fields[b"payload"] if b"payload" in fields else fields.get("payload", "")
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            return raw
        return json.dumps(str(raw), ensure_ascii=False)

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
