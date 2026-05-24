"""Live TTS session metadata store."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

LIVE_SESSION_PREFIX = "nqai.tts.live.sessions."
LIVE_ASSIGNMENT_PREFIX = "nqai.tts.live.assignments."


def live_session_key(session_id: str) -> str:
    return f"{LIVE_SESSION_PREFIX}{session_id}"


def live_assignment_stream(worker_id: str) -> str:
    return f"{LIVE_ASSIGNMENT_PREFIX}{worker_id}"


@dataclass(frozen=True)
class LiveSession:
    session_id: str
    tenant_id: str
    api_key_id: str
    voice_id: str
    worker_id: str
    room_name: str
    created_at_ms: int
    expires_at_ms: int
    protocol: str

    @classmethod
    def new_id(cls) -> str:
        return str(uuid.uuid4())


@dataclass(frozen=True)
class LiveSessionAssignment:
    session: LiveSession
    livekit_url: str
    assigned_at_ms: int

    def encode(self) -> dict[str, str]:
        return {"payload": json.dumps(asdict(self), ensure_ascii=False)}

    @classmethod
    def decode(cls, fields: dict[bytes | str, bytes | str]) -> LiveSessionAssignment:
        def _s(v: bytes | str) -> str:
            return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v

        raw = _s(fields[b"payload"] if b"payload" in fields else fields["payload"])
        payload = json.loads(raw)
        return cls(
            session=LiveSession(**payload["session"]),
            livekit_url=payload["livekit_url"],
            assigned_at_ms=int(payload["assigned_at_ms"]),
        )


class LiveSessionStore:
    def __init__(self, redis: Redis, *, ttl_s: int = 600) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def save(self, session: LiveSession) -> None:
        await self._redis.set(
            live_session_key(session.session_id),
            json.dumps(asdict(session), ensure_ascii=False),
            ex=self._ttl_s,
        )

    async def enqueue_assignment(
        self,
        session: LiveSession,
        *,
        livekit_url: str,
        assigned_at_ms: int,
    ) -> str:
        assignment = LiveSessionAssignment(
            session=session,
            livekit_url=livekit_url,
            assigned_at_ms=assigned_at_ms,
        )
        msg_id = await self._redis.xadd(
            live_assignment_stream(session.worker_id),
            assignment.encode(),
            maxlen=10_000,
            approximate=True,
        )
        return msg_id.decode("utf-8") if isinstance(msg_id, (bytes, bytearray)) else str(msg_id)

    async def get(self, session_id: str) -> LiveSession | None:
        raw = await self._redis.get(live_session_key(session_id))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        return LiveSession(**json.loads(raw))
