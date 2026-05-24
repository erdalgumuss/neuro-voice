from __future__ import annotations

import fakeredis.aioredis

from live import LiveWorkerRegistry


async def test_live_worker_heartbeat_and_selection():
    redis = fakeredis.aioredis.FakeRedis()
    registry = LiveWorkerRegistry(redis, ttl_s=3)

    await registry.heartbeat(
        worker_id="worker-a",
        model_id="openbmb/VoxCPM2",
        device="cuda",
        warm=True,
        active_live_sessions=0,
        max_live_sessions=1,
        current_voice_ids=[],
    )

    worker = await registry.select_available_worker(voice_id="neeko-v01")
    assert worker is not None
    assert worker.worker_id == "worker-a"
    assert worker.has_capacity is True


async def test_live_worker_selection_respects_capacity_and_voice_filter():
    redis = fakeredis.aioredis.FakeRedis()
    registry = LiveWorkerRegistry(redis, ttl_s=3)

    await registry.heartbeat(
        worker_id="busy",
        model_id="m",
        device="cuda",
        warm=True,
        active_live_sessions=1,
        max_live_sessions=1,
        current_voice_ids=[],
    )
    await registry.heartbeat(
        worker_id="voice-specific",
        model_id="m",
        device="cuda",
        warm=True,
        active_live_sessions=0,
        max_live_sessions=1,
        current_voice_ids=["neeko-v01"],
    )

    assert await registry.select_available_worker(voice_id="other-voice") is None
    worker = await registry.select_available_worker(voice_id="neeko-v01")
    assert worker is not None
    assert worker.worker_id == "voice-specific"
