"""Redis-backed live worker heartbeat and admission helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass

from redis.asyncio import Redis

from .protocol import now_ms

LIVE_WORKER_PREFIX = "nqai.worker.live."


def live_worker_key(worker_id: str) -> str:
    return f"{LIVE_WORKER_PREFIX}{worker_id}"


@dataclass(frozen=True)
class LiveWorkerInfo:
    worker_id: str
    model_id: str
    device: str
    warm: bool
    active_live_sessions: int
    max_live_sessions: int
    current_voice_ids: list[str]
    updated_at_ms: int

    @property
    def has_capacity(self) -> bool:
        return self.warm and self.active_live_sessions < self.max_live_sessions

    def supports_voice(self, voice_id: str) -> bool:
        return not self.current_voice_ids or voice_id in self.current_voice_ids

    @classmethod
    def from_hash(cls, fields: dict[bytes | str, bytes | str]) -> LiveWorkerInfo:
        def _get(key: str, default: str = "") -> str:
            raw = fields.get(key) if key in fields else fields.get(key.encode())
            if raw is None:
                return default
            return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)

        raw_voices = _get("current_voice_ids", "[]")
        try:
            current_voice_ids = json.loads(raw_voices)
        except json.JSONDecodeError:
            current_voice_ids = []
        return cls(
            worker_id=_get("worker_id"),
            model_id=_get("model_id"),
            device=_get("device"),
            warm=_get("warm", "false") == "true",
            active_live_sessions=int(_get("active_live_sessions", "0")),
            max_live_sessions=max(1, int(_get("max_live_sessions", "1"))),
            current_voice_ids=[str(v) for v in current_voice_ids],
            updated_at_ms=int(_get("updated_at_ms", "0")),
        )


class LiveWorkerRegistry:
    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = LIVE_WORKER_PREFIX,
        ttl_s: int = 3,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl_s = ttl_s

    async def heartbeat(
        self,
        *,
        worker_id: str,
        model_id: str,
        device: str,
        warm: bool,
        active_live_sessions: int,
        max_live_sessions: int = 1,
        current_voice_ids: list[str] | None = None,
    ) -> None:
        key = f"{self._prefix}{worker_id}"
        await self._redis.hset(
            key,
            mapping={
                "worker_id": worker_id,
                "model_id": model_id,
                "device": device,
                "warm": "true" if warm else "false",
                "active_live_sessions": str(max(0, active_live_sessions)),
                "max_live_sessions": str(max(1, max_live_sessions)),
                "current_voice_ids": json.dumps(current_voice_ids or []),
                "updated_at_ms": str(now_ms()),
            },
        )
        await self._redis.expire(key, self._ttl_s)

    async def list_workers(self) -> list[LiveWorkerInfo]:
        keys = await self._redis.keys(f"{self._prefix}*")
        workers: list[LiveWorkerInfo] = []
        for key in keys:
            fields = await self._redis.hgetall(key)
            if fields:
                workers.append(LiveWorkerInfo.from_hash(fields))
        return workers

    async def select_available_worker(
        self,
        *,
        voice_id: str,
    ) -> LiveWorkerInfo | None:
        candidates = [
            worker for worker in await self.list_workers()
            if worker.has_capacity and worker.supports_voice(voice_id)
        ]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda w: (w.active_live_sessions, -w.updated_at_ms, w.worker_id),
        )[0]
