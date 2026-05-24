"""Worker process entry point — `python -m worker.main`.

Lifecycle:
    boot     → build engine (warmup) + redis + R2 archive callable
    register → SIGTERM / SIGINT handlers flip the stop event
    consume  → WorkerConsumer.run() blocks; per-job semantics in
               worker.pipeline and worker.consumer
    shutdown → graceful drain (current job finishes), then exit 0

Spec: docs/architecture/worker-process.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from .consumer import WorkerConsumer
from .runtime import boot_worker

logger = logging.getLogger("nqai_voice.worker")


async def _run_async() -> int:
    """Build deps, wire consumer, install signal handlers, block on
    consumer.run() until SIGTERM/SIGINT. Returns the process exit code."""
    stop = asyncio.Event()

    def _request_shutdown(sig: signal.Signals) -> None:
        if not stop.is_set():
            logger.warning(
                "received %s — initiating graceful worker shutdown", sig.name,
            )
            stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig)
        except NotImplementedError:
            # Windows / restricted env — signal handlers not available.
            # The container orchestrator can still kill the process.
            logger.warning("signal handler for %s unavailable", sig.name)

    warmup = os.environ.get("NQAI_WARMUP_ON_BOOT", "true").lower() != "false"
    engine, redis, archive = await boot_worker(warmup=warmup)

    consumer = WorkerConsumer(
        redis=redis,
        engine=engine,
        archive_to_r2=archive,
        stop_event=stop,
        # Production-ish defaults — env override for ops tuning.
        block_ms=int(os.environ.get("NQAI_WORKER_BLOCK_MS", "5000")),
        xautoclaim_min_idle_ms=int(
            os.environ.get("NQAI_WORKER_XAUTOCLAIM_INTERVAL_S", "30")
        ) * 1000,
    )

    logger.info(
        "worker consumer ready (consumer=%s stream=%s group=%s block=%dms)",
        consumer.consumer_name, consumer._stream, consumer._group,
        consumer._block_ms,
    )
    try:
        await consumer.run()
    finally:
        try:
            await redis.aclose()
        except Exception:
            logger.exception("redis aclose failed")
    logger.info(
        "worker exited cleanly (acked=%d poisoned=%d transient=%d unknown=%d "
        "claimed=%d)",
        consumer.acked, consumer.poisoned, consumer.transient_failures,
        consumer.unknown_failures, consumer.claimed,
    )
    return 0


def run() -> int:
    """Sync entry point — `python -m worker.main` and the
    docker-compose `command:` both call this. Returns process exit code."""
    logging.basicConfig(
        level=os.environ.get("NQAI_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        return asyncio.run(_run_async())
    except KeyboardInterrupt:
        logger.info("worker interrupted")
        return 0
    except Exception:
        logger.exception("worker crashed during boot/run")
        return 1


if __name__ == "__main__":  # pragma: no cover — entry point
    sys.exit(run())
