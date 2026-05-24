"""Tests for capacity-aware heartbeat backpressure helpers (Faz C step 3).

Worker side: `src/worker/heartbeat.py` writes a TTL-bounded hash and
gracefully exits on a stop event.

Gateway side: `src/server/heartbeat.py` aggregates the cluster's
healthy capacity over Redis SCAN.

We use ``fakeredis.aioredis.FakeRedis`` so these tests stay fast and
deterministic; the heartbeat module talks only async ``hset``,
``pexpire``, ``delete``, ``scan_iter``, ``hgetall`` — all supported.
"""

from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis
import pytest

from server.heartbeat import (
    DEFAULT_PREFIX,
    ClusterCapacity,
    read_cluster_capacity,
)
from worker.heartbeat import (
    WorkerHeartbeatState,
    start_heartbeat_loop,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _state(capacity: int = 4, in_flight: int = 1, *, last_pickup_ms: int | None = None) -> WorkerHeartbeatState:
    return WorkerHeartbeatState(
        capacity=capacity,
        in_flight=in_flight,
        last_pickup_ms=last_pickup_ms if last_pickup_ms is not None else _now_ms(),
        started_at_ms=_now_ms() - 10_000,
    )


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------- #
# Worker-side heartbeat loop
# ---------------------------------------------------------------------- #

async def test_worker_heartbeat_writes_expected_fields(redis):
    stop = asyncio.Event()
    state = _state(capacity=8, in_flight=2)

    task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id="w1",
            get_state=lambda: state,
            stop_event=stop,
            interval_s=0.05,
            ttl_s=1.0,
        )
    )
    # Let the loop write at least once.
    await asyncio.sleep(0.1)
    raw = await redis.hgetall(f"{DEFAULT_PREFIX}.w1")
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    decoded = {k.decode(): v.decode() for k, v in raw.items()}
    assert decoded["capacity"] == "8"
    assert decoded["in_flight"] == "2"
    assert int(decoded["updated_at_ms"]) > 0
    assert int(decoded["last_pickup_ms"]) > 0
    assert int(decoded["started_at_ms"]) > 0


async def test_worker_heartbeat_updated_at_ms_advances_each_tick(redis):
    """`updated_at_ms` MUST advance every refresh tick, regardless of
    whether the worker actually picked a job. This is the liveness
    signal the gateway uses to filter stale workers — pinning it.

    Regression guard: Codex audit 2026-05-24 caught the bug where the
    gateway stale-checked `last_pickup_ms`, which only advances on
    activity, so idle-but-alive workers got marked dead.
    """
    stop = asyncio.Event()
    # Frozen pickup timestamp — caller never advances it.
    frozen_pickup_ms = int(time.time() * 1000) - 30_000  # 30 s old
    state = WorkerHeartbeatState(
        capacity=4,
        in_flight=0,
        last_pickup_ms=frozen_pickup_ms,
        started_at_ms=frozen_pickup_ms,
    )

    task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id="w-idle",
            get_state=lambda: state,
            stop_event=stop,
            interval_s=0.05,
            ttl_s=1.0,
        )
    )
    await asyncio.sleep(0.06)
    first = await redis.hgetall(f"{DEFAULT_PREFIX}.w-idle")
    updated_first = int(first[b"updated_at_ms"])
    pickup_first = int(first[b"last_pickup_ms"])

    await asyncio.sleep(0.12)  # two more ticks
    second = await redis.hgetall(f"{DEFAULT_PREFIX}.w-idle")
    updated_second = int(second[b"updated_at_ms"])
    pickup_second = int(second[b"last_pickup_ms"])

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    # Liveness advanced.
    assert updated_second > updated_first
    # Activity did NOT advance (frozen by caller).
    assert pickup_second == pickup_first == frozen_pickup_ms


async def test_worker_heartbeat_sets_ttl(redis):
    stop = asyncio.Event()
    task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id="w-ttl",
            get_state=lambda: _state(),
            stop_event=stop,
            interval_s=0.05,
            ttl_s=2.0,
        )
    )
    await asyncio.sleep(0.1)
    pttl = await redis.pttl(f"{DEFAULT_PREFIX}.w-ttl")
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    # pttl must be in (0, ttl_ms]. fakeredis returns int ms.
    assert 0 < pttl <= 2000


async def test_worker_heartbeat_loop_terminates_on_stop_event(redis):
    stop = asyncio.Event()
    task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id="w-stop",
            get_state=lambda: _state(),
            stop_event=stop,
            interval_s=0.05,
            ttl_s=1.0,
        )
    )
    await asyncio.sleep(0.05)
    stop.set()
    # Must exit within ~2 intervals.
    await asyncio.wait_for(task, timeout=0.5)
    assert task.done()


async def test_worker_heartbeat_best_effort_del_on_stop(redis):
    stop = asyncio.Event()
    task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id="w-cleanup",
            get_state=lambda: _state(),
            stop_event=stop,
            interval_s=0.05,
            ttl_s=2.0,
        )
    )
    await asyncio.sleep(0.1)
    assert await redis.exists(f"{DEFAULT_PREFIX}.w-cleanup") == 1
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    # Best-effort DEL fired in the finally block.
    assert await redis.exists(f"{DEFAULT_PREFIX}.w-cleanup") == 0


async def test_worker_heartbeat_swallows_get_state_error(redis):
    """If get_state raises, the loop must keep going (not crash the worker)."""
    stop = asyncio.Event()
    calls = {"n": 0}

    def flaky_state() -> WorkerHeartbeatState:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return _state()

    task = asyncio.create_task(
        start_heartbeat_loop(
            redis,
            worker_id="w-flaky",
            get_state=flaky_state,
            stop_event=stop,
            interval_s=0.05,
            ttl_s=1.0,
        )
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    # Loop survived the first call and ran again.
    assert calls["n"] >= 2


# ---------------------------------------------------------------------- #
# Gateway-side cluster aggregation
# ---------------------------------------------------------------------- #

async def test_cluster_capacity_aggregates_multiple_workers(redis):
    now = _now_ms()
    for wid, cap, inf in (("w1", 4, 1), ("w2", 6, 2), ("w3", 2, 0)):
        await redis.hset(
            f"{DEFAULT_PREFIX}.{wid}",
            mapping={
                "capacity": str(cap),
                "in_flight": str(inf),
                "updated_at_ms": str(now),
                "last_pickup_ms": str(now),
                "started_at_ms": str(now - 1000),
            },
        )

    cap = await read_cluster_capacity(redis, stale_ms=5_000)

    assert cap.worker_count == 3
    assert cap.total_capacity == 12
    assert cap.total_inflight == 3
    assert set(cap.healthy_worker_ids) == {"w1", "w2", "w3"}
    assert cap.last_pickup_max_age_ms is not None
    assert cap.last_pickup_max_age_ms < 5_000


async def test_cluster_capacity_excludes_stale_workers(redis):
    now = _now_ms()
    fresh = now
    stale_updated = now - 60_000  # 60 s ago, way beyond 5 s cutoff

    for wid, updated in (
        ("fresh1", fresh),
        ("fresh2", fresh),
        ("stale", stale_updated),
    ):
        await redis.hset(
            f"{DEFAULT_PREFIX}.{wid}",
            mapping={
                "capacity": "4",
                "in_flight": "1",
                "updated_at_ms": str(updated),
                "last_pickup_ms": str(updated),
                "started_at_ms": str(now - 1000),
            },
        )

    cap = await read_cluster_capacity(redis, stale_ms=5_000)
    assert cap.worker_count == 2
    assert set(cap.healthy_worker_ids) == {"fresh1", "fresh2"}
    assert "stale" not in cap.healthy_worker_ids
    assert cap.total_capacity == 8
    assert cap.total_inflight == 2


async def test_cluster_capacity_idle_worker_stays_healthy(redis):
    """Idle-but-alive worker (fresh `updated_at_ms`, old `last_pickup_ms`)
    must NOT be evicted. This is the Codex audit 2026-05-24 regression:
    pre-fix the gateway stale-checked `last_pickup_ms`, evicting healthy
    idle workers and falling back to XLEN-only as soon as traffic
    quieted down.
    """
    now = _now_ms()
    # Worker hasn't picked a job in 60 s, but is still refreshing its
    # heartbeat every second (so updated_at_ms is fresh).
    await redis.hset(
        f"{DEFAULT_PREFIX}.idle-but-alive",
        mapping={
            "capacity": "4",
            "in_flight": "0",
            "updated_at_ms": str(now),
            "last_pickup_ms": str(now - 60_000),
            "started_at_ms": str(now - 300_000),
        },
    )

    cap = await read_cluster_capacity(redis, stale_ms=5_000)
    assert cap.worker_count == 1
    assert cap.healthy_worker_ids == ("idle-but-alive",)
    assert cap.total_capacity == 4
    assert cap.total_inflight == 0


async def test_cluster_capacity_falls_back_to_last_pickup_for_old_workers(redis):
    """Backward compat: a worker hash without `updated_at_ms` (pre-fix
    image) should still be evaluated against `last_pickup_ms`. That
    means OLD idle workers still get marked stale — but new workers
    write `updated_at_ms` and are protected. Both can coexist during
    a rollout."""
    now = _now_ms()
    # Old-style worker, fresh pickup → healthy.
    await redis.hset(
        f"{DEFAULT_PREFIX}.old-active",
        mapping={
            "capacity": "4",
            "in_flight": "1",
            "last_pickup_ms": str(now),
            "started_at_ms": str(now - 1000),
        },
    )
    # Old-style worker, stale pickup → evicted (no `updated_at_ms` to
    # save it; rolling images forward gets us out of this).
    await redis.hset(
        f"{DEFAULT_PREFIX}.old-idle",
        mapping={
            "capacity": "4",
            "in_flight": "0",
            "last_pickup_ms": str(now - 60_000),
            "started_at_ms": str(now - 300_000),
        },
    )

    cap = await read_cluster_capacity(redis, stale_ms=5_000)
    assert cap.healthy_worker_ids == ("old-active",)


async def test_cluster_capacity_empty(redis):
    cap = await read_cluster_capacity(redis, stale_ms=5_000)
    assert cap == ClusterCapacity(
        total_capacity=0,
        total_inflight=0,
        worker_count=0,
        healthy_worker_ids=(),
        last_pickup_max_age_ms=None,
    )


async def test_cluster_capacity_skips_malformed_hash(redis):
    now = _now_ms()
    # Good worker
    await redis.hset(
        f"{DEFAULT_PREFIX}.good",
        mapping={
            "capacity": "4",
            "in_flight": "1",
            "updated_at_ms": str(now),
            "last_pickup_ms": str(now),
            "started_at_ms": str(now - 1000),
        },
    )
    # Missing required field (no started_at_ms)
    await redis.hset(
        f"{DEFAULT_PREFIX}.missing",
        mapping={
            "capacity": "4",
            "in_flight": "1",
            "updated_at_ms": str(now),
            "last_pickup_ms": str(now),
        },
    )
    # Non-int value
    await redis.hset(
        f"{DEFAULT_PREFIX}.garbage",
        mapping={
            "capacity": "nope",
            "in_flight": "0",
            "updated_at_ms": str(now),
            "last_pickup_ms": str(now),
            "started_at_ms": str(now - 1000),
        },
    )

    cap = await read_cluster_capacity(redis, stale_ms=5_000)
    assert cap.worker_count == 1
    assert cap.healthy_worker_ids == ("good",)


async def test_cluster_capacity_uses_custom_prefix(redis):
    now = _now_ms()
    await redis.hset(
        "custom.prefix.w1",
        mapping={
            "capacity": "4",
            "in_flight": "1",
            "updated_at_ms": str(now),
            "last_pickup_ms": str(now),
            "started_at_ms": str(now - 1000),
        },
    )
    cap = await read_cluster_capacity(redis, stale_ms=5_000, key_prefix="custom.prefix")
    assert cap.worker_count == 1
    assert cap.healthy_worker_ids == ("w1",)
