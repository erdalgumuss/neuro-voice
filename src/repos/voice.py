"""VoiceRepo — tenant-scoped. D-08 mandatory filter enforced at the
constructor level: the repo only operates on one tenant per instance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Voice


class VoiceRepo:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("tenant_id must be UUID")
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, include_deleted: bool = False) -> list[Voice]:
        q = select(Voice).where(Voice.tenant_id == self.tenant_id)
        if not include_deleted:
            q = q.where(Voice.deleted_at.is_(None))
        q = q.order_by(Voice.voice_id)
        return list((await self.session.execute(q)).scalars().all())

    async def get(self, voice_id: str) -> Voice | None:
        return (await self.session.execute(
            select(Voice)
            .where(
                Voice.tenant_id == self.tenant_id,
                Voice.voice_id == voice_id,
                Voice.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

    async def create(
        self,
        *,
        voice_id: str,
        display_name: str,
        reference_uri: str,
        reference_sha256: str,
        reference_seconds: float,
        reference_sample_rate: int = 16000,
        language: str = "tr",
        gender: str = "neutral",
        style_tags: list[str] | None = None,
        source: str = "user-enroll",
        license: str = "user-owned",
        engine_params: dict[str, Any] | None = None,
        created_by_key_id: uuid.UUID | None = None,
    ) -> Voice:
        v = Voice(
            tenant_id=self.tenant_id,
            voice_id=voice_id,
            display_name=display_name,
            language=language,
            gender=gender,
            style_tags=style_tags or [],
            reference_uri=reference_uri,
            reference_sha256=reference_sha256,
            reference_seconds=reference_seconds,
            reference_sample_rate=reference_sample_rate,
            source=source,
            license=license,
            engine_params=engine_params or {},
            created_by_key_id=created_by_key_id,
        )
        self.session.add(v)
        await self.session.flush()
        return v

    async def soft_delete(self, voice_id: str) -> Voice | None:
        v = await self.get(voice_id)
        if v is None:
            return None
        v.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return v

    async def set_release_status(self, voice_id: str, status: str) -> Voice | None:
        if status not in {"draft", "staging", "production", "deprecated"}:
            raise ValueError(f"invalid release_status '{status}'")
        v = await self.get(voice_id)
        if v is None:
            return None
        v.release_status = status
        await self.session.flush()
        return v
