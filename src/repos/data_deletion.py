"""DataDeletionRequestRepo — KVKK/GDPR Article 17 audit ticket (ADR-11).

Creating a request DOES NOT delete data — the audit ticket records the
ask. An orchestration layer (tenant route on create, operator route on
process) freezes the named voices and sets `purge_after_at` via
VoiceRepo. The ticket's status reflects fulfillment progress:

* pending      — created by tenant, not yet acknowledged by operator
* in-progress  — operator has frozen the voices; awaiting purge window
* completed    — operator executed purge on all named voices
* rejected     — operator declined (e.g. legal hold); reason in
                 completion_notes

Tenant-scoped on read; operator-side flows (process / reject) use a
separate operator-scoped path that ignores the tenant filter.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DataDeletionRequest


_TERMINAL_STATUSES = {"completed", "rejected"}


class DataDeletionRequestRepo:
    """Tenant-scoped read + create. Operator-side mutations use
    `as_operator()` to bypass the tenant filter.
    """

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID | None = None) -> None:
        # tenant_id=None puts the repo in operator mode; cross-tenant
        # reads (operator inbox) become valid. Tenant-side callers
        # MUST pass tenant_id.
        self.session = session
        self.tenant_id = tenant_id

    @classmethod
    def as_operator(cls, session: AsyncSession) -> "DataDeletionRequestRepo":
        return cls(session, tenant_id=None)

    async def get(self, request_id: uuid.UUID) -> DataDeletionRequest | None:
        q = select(DataDeletionRequest).where(DataDeletionRequest.id == request_id)
        if self.tenant_id is not None:
            q = q.where(DataDeletionRequest.tenant_id == self.tenant_id)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_for_tenant(self) -> list[DataDeletionRequest]:
        if self.tenant_id is None:
            raise RuntimeError(
                "list_for_tenant requires a tenant-scoped repo; use "
                "list_pending() for the operator inbox"
            )
        q = (
            select(DataDeletionRequest)
            .where(DataDeletionRequest.tenant_id == self.tenant_id)
            .order_by(DataDeletionRequest.requested_at.desc())
        )
        return list((await self.session.execute(q)).scalars().all())

    async def list_pending(self) -> list[DataDeletionRequest]:
        """Operator inbox — pending + in-progress requests across all
        tenants, oldest first (FIFO processing). Raises if called on a
        tenant-scoped repo to keep the boundary explicit."""
        if self.tenant_id is not None:
            raise RuntimeError("list_pending requires an operator-scoped repo")
        q = (
            select(DataDeletionRequest)
            .where(DataDeletionRequest.status.in_(["pending", "in-progress"]))
            .order_by(DataDeletionRequest.requested_at)
        )
        return list((await self.session.execute(q)).scalars().all())

    async def create(
        self,
        *,
        voice_slugs: Iterable[str],
        requested_by_actor_id: uuid.UUID,
        jurisdiction: str | None = None,
        reason: str | None = None,
    ) -> DataDeletionRequest:
        if self.tenant_id is None:
            raise RuntimeError("create requires a tenant-scoped repo")
        r = DataDeletionRequest(
            tenant_id=self.tenant_id,
            voice_slugs=list(voice_slugs),
            jurisdiction=jurisdiction,
            status="pending",
            requested_by_actor_id=requested_by_actor_id,
            reason=reason,
        )
        self.session.add(r)
        await self.session.flush()
        return r

    async def mark_in_progress(
        self, request_id: uuid.UUID,
    ) -> DataDeletionRequest | None:
        """Operator started processing — voices frozen, awaiting purge
        window. Idempotent: already in-progress stays in-progress;
        terminal states reject the transition."""
        if self.tenant_id is not None:
            raise RuntimeError("mark_in_progress requires an operator-scoped repo")
        r = await self.get(request_id)
        if r is None:
            return None
        if r.status in _TERMINAL_STATUSES:
            return r  # no-op; caller can detect via status field
        r.status = "in-progress"
        await self.session.flush()
        return r

    async def mark_completed(
        self,
        request_id: uuid.UUID,
        *,
        notes: str | None = None,
        completed_at: datetime | None = None,
    ) -> DataDeletionRequest | None:
        if self.tenant_id is not None:
            raise RuntimeError("mark_completed requires an operator-scoped repo")
        r = await self.get(request_id)
        if r is None or r.status in _TERMINAL_STATUSES:
            return r
        r.status = "completed"
        r.completed_at = completed_at or datetime.now(timezone.utc)
        if notes is not None:
            r.completion_notes = notes
        await self.session.flush()
        return r

    async def mark_rejected(
        self,
        request_id: uuid.UUID,
        *,
        reason: str,
    ) -> DataDeletionRequest | None:
        if self.tenant_id is not None:
            raise RuntimeError("mark_rejected requires an operator-scoped repo")
        r = await self.get(request_id)
        if r is None or r.status in _TERMINAL_STATUSES:
            return r
        r.status = "rejected"
        r.completion_notes = reason
        # completed_at stays NULL — CHECK constraint requires that for
        # non-completed statuses.
        await self.session.flush()
        return r
