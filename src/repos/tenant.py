"""TenantRepo — admin-scoped (operators manage tenants)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Tenant


class TenantRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return (await self.session.execute(
            select(Tenant).where(Tenant.slug == slug)
        )).scalar_one_or_none()

    async def list_active(self) -> list[Tenant]:
        return list((await self.session.execute(
            select(Tenant)
            .where(Tenant.deleted_at.is_(None))
            .order_by(Tenant.created_at.desc())
        )).scalars().all())

    async def create(self, *, slug: str, display_name: str,
                     metadata: dict[str, Any] | None = None) -> Tenant:
        t = Tenant(slug=slug, display_name=display_name,
                   metadata_=metadata or {})
        self.session.add(t)
        await self.session.flush()
        return t

    async def suspend(self, tenant_id: uuid.UUID) -> Tenant | None:
        t = await self.get(tenant_id)
        if t is None:
            return None
        t.status = "suspended"
        await self.session.flush()
        return t

    async def reactivate(self, tenant_id: uuid.UUID) -> Tenant | None:
        t = await self.get(tenant_id)
        if t is None:
            return None
        t.status = "active"
        await self.session.flush()
        return t
