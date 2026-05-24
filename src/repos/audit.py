"""AuditRepo — append-only. UPDATE/DELETE are not exposed on the repo
surface; if a write fails the caller swallows the exception (audit is
non-critical-path, fail-quiet but log-loud)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditLog


class AuditRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        actor_type: str,
        action: str,
        result: str,
        actor_id: uuid.UUID | None = None,
        actor_label: str | None = None,
        tenant_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        ip_addr: str | None = None,
        user_agent: str | None = None,
        request_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        row = AuditLog(
            actor_type=actor_type,
            action=action,
            result=result,
            actor_id=actor_id,
            actor_label=actor_label,
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            ip_addr=ip_addr,
            user_agent=user_agent,
            request_id=request_id,
            payload=payload or {},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def for_tenant(self, tenant_id: uuid.UUID, limit: int = 50) -> list[AuditLog]:
        return list((await self.session.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.occurred_at.desc())
            .limit(limit)
        )).scalars().all())
