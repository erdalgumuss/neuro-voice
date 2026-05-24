"""ApiKeyRepo — auth-critical path. Lookup by prefix is the hot path.

Scoping rules:
    - `lookup_by_prefix()` is the ONLY auth path; returns key + tenant via
      join (single query). Never returns revoked keys.
    - `list_for_tenant()` admin view, tenant-scoped.
    - `create()` admin action, requires tenant_id + secret_hash.
    - `revoke()` admin action, mutates revoked_at.

Repo never sees the plaintext secret — caller hashes with argon2id and
passes only the hash. See src/server/security/api_keys.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import ApiKey, Tenant


class ApiKeyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lookup_active_by_prefix(self, prefix: str) -> tuple[ApiKey, Tenant] | None:
        """Auth hot path: prefix → (key, tenant). NULL revoked only."""
        row = (await self.session.execute(
            select(ApiKey)
            .options(selectinload(ApiKey.tenant))
            .where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
        )).scalar_one_or_none()
        if row is None:
            return None
        return row, row.tenant

    async def list_for_tenant(self, tenant_id: uuid.UUID,
                              include_revoked: bool = False) -> list[ApiKey]:
        q = select(ApiKey).where(ApiKey.tenant_id == tenant_id)
        if not include_revoked:
            q = q.where(ApiKey.revoked_at.is_(None))
        q = q.order_by(ApiKey.created_at.desc())
        return list((await self.session.execute(q)).scalars().all())

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        prefix: str,
        secret_hash: str,
        scopes: list[str] | None = None,
        rate_limit_per_minute: int = 60,
        label: str | None = None,
        created_by_operator_id: uuid.UUID | None = None,
    ) -> ApiKey:
        key = ApiKey(
            tenant_id=tenant_id,
            prefix=prefix,
            secret_hash=secret_hash,
            scopes=scopes or ["tts:read", "tts:write"],
            rate_limit_per_minute=rate_limit_per_minute,
            label=label,
            created_by_operator_id=created_by_operator_id,
        )
        self.session.add(key)
        await self.session.flush()
        return key

    async def revoke(self, key_id: uuid.UUID, reason: str | None = None) -> ApiKey | None:
        key = await self.session.get(ApiKey, key_id)
        if key is None or key.revoked_at is not None:
            return key
        key.revoked_at = datetime.now(timezone.utc)
        key.revoked_reason = reason
        await self.session.flush()
        return key

    async def touch_last_used(self, key_id: uuid.UUID) -> None:
        """Async update, no return — best-effort outside the auth critical path."""
        key = await self.session.get(ApiKey, key_id)
        if key is not None:
            key.last_used_at = datetime.now(timezone.utc)
            await self.session.flush()
