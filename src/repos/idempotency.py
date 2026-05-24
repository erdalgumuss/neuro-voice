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
from sqlalchemy.exc import IntegrityError
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

        Race-safety (audit F1 fix, 2026-05-24):
        ----------------------------------------
        Two concurrent callers can both pass the upfront `get()` check
        when the row doesn't exist yet. The loser's INSERT then hits the
        PK uniqueness on `request_id` and raises `IntegrityError`. We
        catch that, re-read, and classify exactly the same way as a
        second-time call — body_hash match → replay, mismatch → conflict.
        Without this catch the loser would surface a raw IntegrityError
        to the caller, defeating the whole point of guarded reserve.
        """
        existing = await self.get(request_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflict(existing)
            return existing, False
        try:
            row = await self.reserve(
                request_id=request_id,
                api_key_id=api_key_id,
                request_hash=request_hash,
                ttl=ttl,
            )
            return row, True
        except IntegrityError:
            # Concurrent reserve won the race — roll back our flush and
            # re-read. The row is now durable from the winner's commit
            # (or about to be — same session sees it via the PK lookup).
            await self.session.rollback()
            winner = await self.get(request_id)
            if winner is None:
                # Extraordinarily rare — the row got inserted then
                # immediately expired/purged. Treat as a fresh request.
                row = await self.reserve(
                    request_id=request_id,
                    api_key_id=api_key_id,
                    request_hash=request_hash,
                    ttl=ttl,
                )
                return row, True
            if winner.request_hash != request_hash:
                raise IdempotencyConflict(winner) from None
            return winner, False

    async def delete(self, request_id: uuid.UUID) -> int:
        """Remove a reserved row by request_id within this tenant.

        Used by the gateway when XADD fails right after reserve — the
        worker never saw the job, so the reservation is bogus and the
        client must be able to retry the *same* Idempotency-Key (audit
        F5, 2026-05-24). Returns the row count removed (0 or 1).
        """
        result = await self.session.execute(
            delete(JobIdempotency).where(
                JobIdempotency.request_id == request_id,
                JobIdempotency.tenant_id == self.tenant_id,
            )
        )
        await self.session.flush()
        return result.rowcount or 0

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
        """Cron-callable cleanup, tenant-scoped (D-08).

        Audit L5 H1 2026-05-25: pre-fix this method silently bypassed
        the tenant_id filter that the repo constructor enforces — a
        cross-tenant DELETE driven by any operator calling
        `purge_expired()` on a tenant-scoped repo instance. The cleanup
        cron should still run per tenant; if global-scope purge is
        needed (e.g. for the operator UI's bulk maintenance), add a
        separate maintenance helper that takes no tenant_id and is
        explicitly documented as cross-tenant.
        """
        result = await self.session.execute(
            delete(JobIdempotency).where(
                JobIdempotency.tenant_id == self.tenant_id,
                JobIdempotency.expires_at < datetime.now(timezone.utc),
            )
        )
        await self.session.flush()
        return result.rowcount or 0
