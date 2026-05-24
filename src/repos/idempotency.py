"""IdempotencyRepo — tenant-scoped, 24h TTL. Backs D-05."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import JobIdempotency


class IdempotencyRepo:
    DEFAULT_TTL = timedelta(hours=24)

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("tenant_id must be UUID")
        self.session = session
        self.tenant_id = tenant_id

    async def get(self, request_id: uuid.UUID) -> JobIdempotency | None:
        row = (await self.session.execute(
            select(JobIdempotency)
            .where(
                JobIdempotency.request_id == request_id,
                JobIdempotency.tenant_id == self.tenant_id,
                JobIdempotency.expires_at > datetime.now(timezone.utc),
            )
        )).scalar_one_or_none()
        return row

    async def reserve(
        self,
        *,
        request_id: uuid.UUID,
        api_key_id: uuid.UUID,
        request_hash: str,
        ttl: timedelta | None = None,
    ) -> JobIdempotency:
        row = JobIdempotency(
            request_id=request_id,
            tenant_id=self.tenant_id,
            api_key_id=api_key_id,
            request_hash=request_hash,
            status="processing",
            expires_at=datetime.now(timezone.utc) + (ttl or self.DEFAULT_TTL),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def complete(self, request_id: uuid.UUID, response_uri: str | None = None) -> None:
        row = await self.get(request_id)
        if row is None:
            return
        row.status = "complete"
        if response_uri is not None:
            row.response_uri = response_uri
        await self.session.flush()

    async def fail(self, request_id: uuid.UUID) -> None:
        row = await self.get(request_id)
        if row is None:
            return
        row.status = "failed"
        await self.session.flush()

    async def purge_expired(self) -> int:
        """Cron-callable cleanup. Returns count of rows removed."""
        result = await self.session.execute(
            delete(JobIdempotency).where(
                JobIdempotency.expires_at < datetime.now(timezone.utc)
            )
        )
        await self.session.flush()
        return result.rowcount or 0
