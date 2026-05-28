"""Gateway-side aggregator over worker heartbeat hashes.

`read_cluster_capacity` scans Redis for keys matching `{prefix}.*`, parses
each hash, filters out stale workers (``updated_at_ms`` older than
``stale_ms``), and returns total capacity / in-flight across the cluster.

Used by `_check_queue_depth_or_503` (wired by the orchestrator) to do
capacity-aware backpressure instead of dumb XLEN-only gating: a queue
depth of 100 jobs against 10 workers × 5 capacity is not overload.

Robustness contract
-------------------
* Empty cluster (no heartbeat keys at all) → ClusterCapacity zeros,
  ``last_pickup_max_age_ms=None``. Gateway falls back to XLEN-only path.
* Malformed hash (missing fields, non-int values) → skipped silently.
  An operator manually ``HSET``ing a junk key must not break the gateway.
* Stale workers (``updated_at_ms`` older than ``stale_ms`` ago) → skipped.
  Stale check uses the **worker liveness** timestamp, not the
  ``last_pickup_ms`` job-activity one. A healthy idle worker advances
  ``updated_at_ms`` every refresh tick but its ``last_pickup_ms`` stays
  pinned at whenever the last job came through — using the latter would
  evict idle-but-alive workers from the cluster after `stale_ms`
  (Codex audit 2026-05-24). For backward compatibility with older
  worker images that pre-date the ``updated_at_ms`` field we fall back
  to ``last_pickup_ms`` if ``updated_at_ms`` is missing.
* Redis SCAN errors → re-raised. The gateway caller decides whether to
  fall back to XLEN-only or fail open / closed.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger("neurovoice.server.heartbeat")

DEFAULT_STALE_MS = 5_000
DEFAULT_PREFIX = "neurovoice.worker.heartbeat"
_SCAN_COUNT = 100


@dataclass(frozen=True)
class ClusterCapacity:
    """Aggregated capacity snapshot across all healthy workers."""

    total_capacity: int
    total_inflight: int
    worker_count: int
    healthy_worker_ids: tuple[str, ...]
    # How fresh the *most-recently-seen* heartbeat is. ``None`` when no
    # healthy workers were found (empty cluster). Larger == staler.
    last_pickup_max_age_ms: int | None


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r — using fallback %s", name, raw, fallback)
        return fallback


def _env_str(name: str, fallback: str) -> str:
    raw = os.environ.get(name)
    return raw if raw else fallback


def _now_ms() -> int:
    return int(time.time() * 1000)


def _as_str(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_hash(raw: dict) -> dict[str, int] | None:
    """Decode a Redis hash (bytes-or-str keys/values) into ``{field: int}``.

    Returns ``None`` if any required field is missing or not parseable as
    int. We do NOT log per-malformed-hash to avoid spamming — the SCAN
    log line records the worker count, which is enough signal.

    ``updated_at_ms`` is the liveness signal but is optional on the wire
    for backward-compat: old worker images (pre-Codex-audit) only wrote
    ``last_pickup_ms``. When ``updated_at_ms`` is absent we synthesise
    one from ``last_pickup_ms`` so the stale check still has a value to
    compare against (old workers behave as before — idle ones get
    marked stale, which is the regression we just fixed for new
    workers but can't retroactively help the old image).
    """
    if not raw:
        return None
    decoded: dict[str, str] = {}
    for k, v in raw.items():
        decoded[_as_str(k)] = _as_str(v)
    required = ("capacity", "in_flight", "last_pickup_ms", "started_at_ms")
    out: dict[str, int] = {}
    for field in required:
        if field not in decoded:
            return None
        try:
            out[field] = int(decoded[field])
        except (TypeError, ValueError):
            return None
    # Optional updated_at_ms — fall back to last_pickup_ms on old workers.
    if "updated_at_ms" in decoded:
        try:
            out["updated_at_ms"] = int(decoded["updated_at_ms"])
        except (TypeError, ValueError):
            return None
    else:
        out["updated_at_ms"] = out["last_pickup_ms"]
    return out


def _worker_id_from_key(key: object, prefix: str) -> str:
    s = _as_str(key)
    cut = f"{prefix}."
    return s[len(cut):] if s.startswith(cut) else s


async def read_cluster_capacity(
    redis,
    *,
    stale_ms: int | None = None,
    key_prefix: str | None = None,
) -> ClusterCapacity:
    """SCAN heartbeat hashes and aggregate live capacity.

    Args:
        redis: ``redis.asyncio.Redis`` (real or fakeredis).
        stale_ms: heartbeats older than this many ms are dropped.
            Defaults to env ``NEUROVOICE_GATEWAY_HEARTBEAT_STALE_MS`` or 5000.
        key_prefix: heartbeat key prefix (must match worker side).
            Defaults to env ``NEUROVOICE_WORKER_HEARTBEAT_PREFIX`` or
            ``neurovoice.worker.heartbeat``.

    Returns:
        ClusterCapacity. ``worker_count == 0`` signals the caller to
        fall back to the XLEN-only path.

    Raises:
        Whatever ``redis.scan_iter`` / ``redis.hgetall`` raise. Caller
        decides on fallback semantics.
    """
    threshold = (
        stale_ms
        if stale_ms is not None
        else _env_int("NEUROVOICE_GATEWAY_HEARTBEAT_STALE_MS", DEFAULT_STALE_MS)
    )
    prefix = (
        key_prefix
        if key_prefix is not None
        else _env_str("NEUROVOICE_WORKER_HEARTBEAT_PREFIX", DEFAULT_PREFIX)
    )
    match = f"{prefix}.*"
    now = _now_ms()
    cutoff_ms = now - threshold

    total_capacity = 0
    total_inflight = 0
    healthy_ids: list[str] = []
    max_pickup_ms: int | None = None
    skipped_malformed = 0
    skipped_stale = 0

    async for key in redis.scan_iter(match=match, count=_SCAN_COUNT):
        raw = await redis.hgetall(key)
        parsed = _parse_hash(raw)
        if parsed is None:
            skipped_malformed += 1
            continue
        # Liveness check: use updated_at_ms (advances every refresh tick
        # regardless of job activity). Idle-but-alive workers must NOT
        # be evicted just because no jobs arrived.
        if parsed["updated_at_ms"] < cutoff_ms:
            skipped_stale += 1
            continue
        total_capacity += parsed["capacity"]
        total_inflight += parsed["in_flight"]
        healthy_ids.append(_worker_id_from_key(key, prefix))
        # `last_pickup_max_age_ms` continues to track when the most-
        # recently-active worker last picked a job (separate from
        # liveness — useful for "is the cluster actually doing work?"
        # alerts).
        pickup = parsed["last_pickup_ms"]
        if max_pickup_ms is None or pickup > max_pickup_ms:
            max_pickup_ms = pickup

    if not healthy_ids:
        if skipped_malformed or skipped_stale:
            logger.debug(
                "no healthy heartbeats (skipped malformed=%d stale=%d)",
                skipped_malformed, skipped_stale,
            )
        return ClusterCapacity(
            total_capacity=0,
            total_inflight=0,
            worker_count=0,
            healthy_worker_ids=(),
            last_pickup_max_age_ms=None,
        )

    last_age = max(0, now - max_pickup_ms) if max_pickup_ms is not None else None
    return ClusterCapacity(
        total_capacity=total_capacity,
        total_inflight=total_inflight,
        worker_count=len(healthy_ids),
        healthy_worker_ids=tuple(sorted(healthy_ids)),
        last_pickup_max_age_ms=last_age,
    )


__all__ = [
    "ClusterCapacity",
    "read_cluster_capacity",
    "DEFAULT_STALE_MS",
    "DEFAULT_PREFIX",
]
