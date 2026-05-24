"""IdempotencyRepo — tenant-scoped, 24h TTL. Backs D-05.

Stripe-style semantics (decision log 2026-05-24, Ö4):
  * Same Idempotency-Key + same body → cached row returned (replay)
  * Same Idempotency-Key + DIFFERENT body → 409 Conflict
    (IdempotencyConflict raised by reserve_or_get())
  * Body hash is a stable SHA256 of the canonical request payload.

The reserve() primitive still exists for callers that want the raw
insert (e.g. tests, fixture scripts); production code paths should
use reserve_or_get() which transparently handles the replay vs
conflict vs first-time-reservation triad.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import JobIdempotency


class IdempotencyConflict(Exception):
    """Same Idempotency-Key reused with a different request body.

    Carries the existing row so the HTTP layer can include the conflict
    details (created_at, original status) in the 409 response — clients
    that intentionally replay get visibility into which prior call
    they're colliding with.
    """

    def __init__(self, existing: JobIdempotency) -> None:
        self.existing = existing
        super().__init__(
            f"Idempotency-Key {existing.request_id} reused with a different body"
        )


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

    async def reserve_or_get(
        self,
        *,
        request_id: uuid.UUID,
        api_key_id: uuid.UUID,
        request_hash: str,
        ttl: timedelta | None = None,
    ) -> tuple[JobIdempotency, bool]:
        """Stripe-style guarded reserve. Returns (row, reserved_new).

        * (row, True)  — first time we've seen this key; new row inserted.
        * (row, False) — same key + same body_hash; cached row returned.
        * raises IdempotencyConflict — same key + different body_hash.

        The body_hash check is the *whole point* of Stripe idempotency:
        without it, a typo-fix POST under the same key silently no-ops
        instead of surfacing the divergence.
        """
        existing = await self.get(request_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflict(existing)
            return existing, False
        row = await self.reserve(
            request_id=request_id,
            api_key_id=api_key_id,
            request_hash=request_hash,
            ttl=ttl,
        )
        return row, True

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
