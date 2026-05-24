"""Sliding-window rate limiter contract tests (fakeredis backend)."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
import fakeredis.aioredis

from server.rate_limit import RateLimiter


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
async def limiter(redis):
    return RateLimiter(redis)


async def test_first_request_allowed(limiter):
    r = await limiter.check("test:bucket:1", limit=5, window_ms=60_000)
    assert r.allowed is True
    assert r.count == 1
    assert r.retry_after_ms == 0


async def test_under_limit_continues_to_allow(limiter):
    for i in range(5):
        r = await limiter.check("test:bucket:2", limit=5, window_ms=60_000)
        assert r.allowed is True
        assert r.count == i + 1


async def test_at_limit_denies_extra_request(limiter):
    for _ in range(5):
        await limiter.check("test:bucket:3", limit=5, window_ms=60_000)
    r = await limiter.check("test:bucket:3", limit=5, window_ms=60_000)
    assert r.allowed is False
    assert r.count == 5
    assert r.retry_after_ms > 0


async def test_independent_buckets(limiter):
    for _ in range(5):
        await limiter.check("bucket:a", limit=5, window_ms=60_000)
    # Different bucket — still fresh
    r = await limiter.check("bucket:b", limit=5, window_ms=60_000)
    assert r.allowed is True


async def test_window_expires_releases_quota(limiter):
    # 200 ms window — sleeps are practical for the test
    for _ in range(3):
        await limiter.check("bucket:short", limit=3, window_ms=200)
    r = await limiter.check("bucket:short", limit=3, window_ms=200)
    assert r.allowed is False
    await asyncio.sleep(0.25)
    r2 = await limiter.check("bucket:short", limit=3, window_ms=200)
    assert r2.allowed is True


async def test_invalid_limits_rejected(limiter):
    with pytest.raises(ValueError):
        await limiter.check("x", limit=0, window_ms=1000)
    with pytest.raises(ValueError):
        await limiter.check("x", limit=10, window_ms=0)


async def test_api_key_helper(limiter):
    key_id = uuid.uuid4()
    r1 = await limiter.check_api_key(key_id, per_minute=2)
    r2 = await limiter.check_api_key(key_id, per_minute=2)
    r3 = await limiter.check_api_key(key_id, per_minute=2)
    assert r1.allowed and r2.allowed and not r3.allowed


async def test_tenant_helper_independent_from_key(limiter):
    tid = uuid.uuid4()
    # Tenant bucket has separate counter from any key bucket
    r1 = await limiter.check_tenant(tid, per_minute=100)
    assert r1.allowed
    assert r1.count == 1


async def test_retry_after_decreases_with_time(limiter):
    """As the window slides, the time-until-reset shrinks."""
    for _ in range(2):
        await limiter.check("bucket:slide", limit=2, window_ms=2_000)
    r1 = await limiter.check("bucket:slide", limit=2, window_ms=2_000)
    assert not r1.allowed
    await asyncio.sleep(0.5)
    r2 = await limiter.check("bucket:slide", limit=2, window_ms=2_000)
    assert not r2.allowed
    # r2 should report less retry time than r1 (sliding forward)
    assert r2.retry_after_ms < r1.retry_after_ms
