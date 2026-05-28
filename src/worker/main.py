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

from prometheus_client import start_http_server

from observability import REGISTRY

from .consumer import WorkerConsumer
from .heartbeat import WorkerHeartbeatState, start_heartbeat_loop
from .runtime import boot_worker

logger = logging.getLogger("neurovoice.worker")


_metrics_server_started = False


def _start_metrics_server() -> None:
    """Bind Prometheus exposition on NEUROVOICE_WORKER_METRICS_PORT (default 9100).

    Skip if the env var is '0' / 'off' (single-box dev, tests).
    Idempotent: only the first call actually binds; subsequent calls in
    the same process are no-ops. This matters in tests that invoke
    `worker.main.run()` multiple times within one pytest process.
    `OSError` (address in use, permission denied) is logged and swallowed
    so a metrics-port glitch can never block worker startup."""
    global _metrics_server_started
    if _metrics_server_started:
        return
    raw = os.environ.get("NEUROVOICE_WORKER_METRICS_PORT", "9100").strip()
    if raw in {"0", "off", "false", "no", ""}:
        logger.info("worker metrics http server disabled (NEUROVOICE_WORKER_METRICS_PORT=%r)", raw)
        _metrics_server_started = True
        return
    try:
        port = int(raw)
    except ValueError:
        logger.warning("invalid NEUROVOICE_WORKER_METRICS_PORT=%r — disabling", raw)
        _metrics_server_started = True
        return
    addr = os.environ.get("NEUROVOICE_WORKER_METRICS_BIND", "0.0.0.0")
    try:
        start_http_server(port, addr=addr, registry=REGISTRY)
        logger.info("worker metrics http server listening on %s:%d", addr, port)
    except OSError as e:
        logger.warning(
            "metrics http server bind on %s:%d failed (%s) — continuing without /metrics",
            addr, port, e,
        )
    _metrics_server_started = True


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

    warmup = os.environ.get("NEUROVOICE_WARMUP_ON_BOOT", "true").lower() != "false"
    engine, redis, archive = await boot_worker(warmup=warmup)

    consumer = WorkerConsumer(
        redis=redis,
        engine=engine,
        archive_to_r2=archive,
        stop_event=stop,
        # Production-ish defaults — env override for ops tuning.
        block_ms=int(os.environ.get("NEUROVOICE_WORKER_BLOCK_MS", "5000")),
        # Two distinct knobs (audit L2 H2 2026-05-25):
        #   * `xautoclaim_min_idle_ms` — how long a PEL message must
        #     sit idle before another worker claims it. Sets the
        #     "another worker assumes you're dead" threshold.
        #     env: NEUROVOICE_WORKER_XAUTOCLAIM_IDLE_S (default 30 s)
        #   * `xautoclaim_period_s` — how often a healthy worker
        #     proactively sweeps for stale PEL entries even while
        #     busy. Sets the "look for orphan work" cadence.
        #     env: NEUROVOICE_WORKER_XAUTOCLAIM_PERIOD_S (default 5 s)
        # Pre-fix the single `NEUROVOICE_WORKER_XAUTOCLAIM_INTERVAL_S` env
        # var bled into the idle threshold but operators read it as
        # the sweep cadence — opposite semantics. We keep the OLD name
        # as a back-compat alias for the idle threshold so any
        # production env-file mid-flight doesn't suddenly change
        # meaning.
        xautoclaim_min_idle_ms=int(
            os.environ.get(
                "NEUROVOICE_WORKER_XAUTOCLAIM_IDLE_S",
                os.environ.get("NEUROVOICE_WORKER_XAUTOCLAIM_INTERVAL_S", "30"),
            )
        ) * 1000,
        xautoclaim_period_s=float(
            os.environ.get("NEUROVOICE_WORKER_XAUTOCLAIM_PERIOD_S", "5.0")
        ),
    )

    logger.info(
        "worker consumer ready (consumer=%s stream=%s group=%s block=%dms cap=%d)",
        consumer.consumer_name, consumer._stream, consumer._group,
        consumer._block_ms, consumer.capacity,
    )

    # heartbeat: publish capacity/in_flight to Redis so the gateway
    # can do capacity-aware admission instead of XLEN-only backpressure.
    def _get_heartbeat_state() -> WorkerHeartbeatState:
        return WorkerHeartbeatState(
            capacity=consumer.capacity,
            in_flight=consumer.in_flight,
            last_pickup_ms=consumer.last_pickup_ms,
            started_at_ms=consumer.started_at_ms,
        )

    heartbeat_task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id=consumer.consumer_name,
            get_state=_get_heartbeat_state,
            stop_event=stop,
        ),
        name="worker-heartbeat",
    )

    try:
        await consumer.run()
    finally:
        # Stop the heartbeat loop and wait for its best-effort DEL.
        stop.set()
        try:
            await asyncio.wait_for(heartbeat_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("heartbeat task did not exit within 5s — cancelling")
            heartbeat_task.cancel()
        except Exception:
            logger.exception("heartbeat task raised on shutdown")
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
        level=os.environ.get("NEUROVOICE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _start_metrics_server()
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
