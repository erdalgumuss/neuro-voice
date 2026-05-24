"""Worker → Redis liveness heartbeat for capacity-aware backpressure.

Each worker periodically writes its `WorkerHeartbeatState` to a Redis hash
keyed by `{prefix}.{worker_id}` and PEXPIREs it (~3 s by default). Gateway
side aggregates these via `src/server/heartbeat.py::read_cluster_capacity`
to decide whether the cluster has free capacity for an incoming job.

Design notes
------------
* Time source is the worker's wall clock (``int(time.time() * 1000)``) for
  every `*_ms` field. We deliberately do NOT use Redis `TIME` — it adds a
  round-trip per refresh and fakeredis support is patchy. Clock skew
  across a single deployment is tolerable because the gateway only uses
  the timestamp to discard stale entries (multi-second granularity).
* The loop never raises during refresh: a Redis hiccup must not kill a
  worker process. We log at warn level with a 1/min cap to avoid spam.
* Shutdown is cancellable on the order of a single tick because we
  `asyncio.wait_for(stop_event.wait(), timeout=interval_s)` instead of
  sleeping blindly.

Spec: Faz C step 3 — heartbeat-based backpressure helpers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("nqai_voice.worker.heartbeat")

DEFAULT_INTERVAL_S = 1.0
DEFAULT_TTL_S = 3.0
DEFAULT_PREFIX = "nqai.worker.heartbeat"

_WARN_THROTTLE_S = 60.0


@dataclass(frozen=True)
class WorkerHeartbeatState:
    """Snapshot of one worker's live capacity numbers."""

    capacity: int
    in_flight: int
    last_pickup_ms: int  # ms-epoch timestamp from worker's clock
    started_at_ms: int   # ms-epoch timestamp from worker's clock


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r — using fallback %s", name, raw, fallback)
        return fallback


def _env_str(name: str, fallback: str) -> str:
    raw = os.environ.get(name)
    return raw if raw else fallback


def _now_ms() -> int:
    return int(time.time() * 1000)


class _WarnThrottle:
    """Tiny 1-per-60 s warn limiter so a flaky Redis doesn't spam logs."""

    __slots__ = ("_last_emit",)

    def __init__(self) -> None:
        self._last_emit = 0.0

    def maybe(self, msg: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_emit < _WARN_THROTTLE_S:
            return
        self._last_emit = now
        logger.warning(msg, *args)


async def _write_once(
    redis,
    *,
    key: str,
    state: WorkerHeartbeatState,
    ttl_ms: int,
) -> None:
    mapping = {
        "capacity": str(state.capacity),
        "in_flight": str(state.in_flight),
        "last_pickup_ms": str(state.last_pickup_ms),
        "started_at_ms": str(state.started_at_ms),
    }
    await redis.hset(key, mapping=mapping)
    await redis.pexpire(key, ttl_ms)


async def start_heartbeat_loop(
    redis,
    *,
    worker_id: str,
    get_state: Callable[[], WorkerHeartbeatState],
    stop_event: asyncio.Event,
    interval_s: float | None = None,
    ttl_s: float | None = None,
    key_prefix: str | None = None,
) -> None:
    """Refresh the heartbeat hash until ``stop_event`` fires.

    On exit, best-effort DELete the key so the gateway sees the worker
    drop out immediately (instead of waiting for TTL). Any Redis error
    during the loop is swallowed and warn-logged at most once per minute;
    the loop continues so a transient outage does not crash the worker.
    """
    if not worker_id:
        raise ValueError("worker_id must be non-empty")
    interval = (
        interval_s
        if interval_s is not None
        else _env_float("NQAI_WORKER_HEARTBEAT_INTERVAL_S", DEFAULT_INTERVAL_S)
    )
    ttl = (
        ttl_s
        if ttl_s is not None
        else _env_float("NQAI_WORKER_HEARTBEAT_TTL_S", DEFAULT_TTL_S)
    )
    prefix = (
        key_prefix
        if key_prefix is not None
        else _env_str("NQAI_WORKER_HEARTBEAT_PREFIX", DEFAULT_PREFIX)
    )

    if interval <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval}")
    if ttl <= 0:
        raise ValueError(f"ttl_s must be > 0, got {ttl}")
    if ttl < interval:
        # Operator footgun: a TTL shorter than the refresh interval means
        # the key disappears between refreshes. Warn but don't refuse —
        # tests want flexibility.
        logger.warning(
            "ttl_s=%s < interval_s=%s — heartbeat will appear stale",
            ttl, interval,
        )

    key = f"{prefix}.{worker_id}"
    ttl_ms = int(ttl * 1000)
    throttle = _WarnThrottle()
    logger.debug(
        "heartbeat loop start worker_id=%s key=%s interval=%.2fs ttl=%.2fs",
        worker_id, key, interval, ttl,
    )

    try:
        while not stop_event.is_set():
            try:
                state = get_state()
                await _write_once(redis, key=key, state=state, ttl_ms=ttl_ms)
                logger.debug(
                    "heartbeat refresh worker_id=%s cap=%s inflight=%s",
                    worker_id, state.capacity, state.in_flight,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                throttle.maybe(
                    "heartbeat refresh failed worker_id=%s err=%s",
                    worker_id, e,
                )
            # Cancellable sleep — fires immediately on stop_event.set().
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
    finally:
        # Best-effort cleanup. Don't propagate — shutdown must stay quiet.
        try:
            await redis.delete(key)
            logger.debug("heartbeat key deleted worker_id=%s", worker_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "heartbeat cleanup delete failed worker_id=%s err=%s",
                worker_id, e,
            )


__all__ = [
    "WorkerHeartbeatState",
    "start_heartbeat_loop",
    "DEFAULT_INTERVAL_S",
    "DEFAULT_TTL_S",
    "DEFAULT_PREFIX",
]
