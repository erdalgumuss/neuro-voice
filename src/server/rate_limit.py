"""Sliding-window rate limiter on Redis.

Per docs/architecture/auth-multi-tenant.md §1.6 — sliding window log with
Lua script for atomic check + insert. Three independent dimensions are
checked in sequence (per-key, per-tenant, per-IP); first that fails wins.

The Lua script is loaded once and called via SCRIPT EVALSHA; if the
server hot-reloads the script cache (EVALSHA → NOSCRIPT), we fall back
to EVAL and re-register.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import NoScriptError, ResponseError

SLIDING_WINDOW_LUA = """
-- KEYS[1] = window-set key (sorted set)
-- ARGV[1] = now_ms
-- ARGV[2] = window_ms
-- ARGV[3] = limit
-- ARGV[4] = member (unique entry id)

local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])

if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local oldest_ts = 0
    if oldest[2] then oldest_ts = tonumber(oldest[2]) end
    local retry_ms = math.max(1, (oldest_ts + window) - now)
    return {0, count, retry_ms}
end

redis.call('ZADD', KEYS[1], now, member)
redis.call('PEXPIRE', KEYS[1], window)
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    retry_after_ms: int  # 0 when allowed


class RateLimiter:
    """Sliding window rate limiter backed by Redis.

    Usage:
        rl = RateLimiter(redis_client)
        result = await rl.check("rl:key:<uuid>", limit=60, window_ms=60_000)
        if not result.allowed:
            raise HTTPException(429, headers={"Retry-After": str(result.retry_after_ms // 1000)})
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._script_sha: str | None = None

    async def _ensure_script_loaded(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(SLIDING_WINDOW_LUA)
        return self._script_sha

    async def check(
        self,
        bucket_key: str,
        *,
        limit: int,
        window_ms: int = 60_000,
        member: str | None = None,
    ) -> RateLimitResult:
        if limit <= 0:
            raise ValueError("limit must be > 0")
        if window_ms <= 0:
            raise ValueError("window_ms must be > 0")
        now_ms = int(time.time() * 1000)
        # Member must be unique inside the window to keep the ZADD distinct.
        m = member or f"{now_ms}-{uuid.uuid4().hex[:8]}"

        try:
            sha = await self._ensure_script_loaded()
            raw = await self._redis.evalsha(
                sha, 1, bucket_key, now_ms, window_ms, limit, m
            )
        except (NoScriptError, ResponseError):
            # Cache miss or fakeredis lacking EVALSHA — re-EVAL inline.
            raw = await self._redis.eval(
                SLIDING_WINDOW_LUA, 1, bucket_key, now_ms, window_ms, limit, m
            )
            self._script_sha = None  # force reload on next call

        # Redis returns Lua tables as Python lists; values come back as ints
        # or bytes depending on the client decode mode.
        def _i(v):
            return int(v) if not isinstance(v, (bytes, bytearray)) else int(v.decode())

        allowed, count, retry_ms = (_i(raw[0]), _i(raw[1]), _i(raw[2]))
        return RateLimitResult(
            allowed=bool(allowed),
            count=count,
            retry_after_ms=retry_ms,
        )

    async def check_api_key(
        self,
        key_id: uuid.UUID,
        *,
        per_minute: int,
    ) -> RateLimitResult:
        return await self.check(
            f"nqai:rl:key:{key_id}",
            limit=per_minute,
            window_ms=60_000,
        )

    async def check_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        per_minute: int = 600,
    ) -> RateLimitResult:
        return await self.check(
            f"nqai:rl:tenant:{tenant_id}",
            limit=per_minute,
            window_ms=60_000,
        )
