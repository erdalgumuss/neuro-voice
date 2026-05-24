"""VoiceAccessRepo — explicit cross-tenant grants for shared voices.

Refactor R (2026-05-24): admin-operator side of the voice access model.
Used by future `POST /v1/voices/{id}/share` and admin UI flows (Faz B+);
gateway request path does not write here — only reads via VoiceRepo's
joined accessibility query.

Scoping: this repo takes the OWNER tenant as `owner_tenant_id` and only
grants voices the owner actually owns — prevents accidental grants
across ownership boundaries (D-08).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Voice, VoiceAccess


class VoiceAccessRepo:
    def __init__(self, session: AsyncSession, owner_tenant_id: uuid.UUID) -> None:
        if not isinstance(owner_tenant_id, uuid.UUID):
            raise TypeError("owner_tenant_id must be UUID")
        self.session = session
        self.owner_tenant_id = owner_tenant_id

    async def grant(
        self,
        *,
        voice_slug: str,
        grantee_tenant_id: uuid.UUID,
        permission: str = "use",
        granted_by: uuid.UUID | None = None,
    ) -> VoiceAccess | None:
        """Insert (or upsert) an access grant. Returns None if the owner
        doesn't actually own the voice (existence-leak prevention)."""
        if permission not in {"use", "read"}:
            raise ValueError(f"invalid permission '{permission}'")
        voice = (await self.session.execute(
            select(Voice).where(
                Voice.owner_tenant_id == self.owner_tenant_id,
                Voice.voice_id == voice_slug,
                Voice.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if voice is None:
            return None

        existing = (await self.session.execute(
            select(VoiceAccess).where(
                VoiceAccess.voice_id == voice.id,
                VoiceAccess.tenant_id == grantee_tenant_id,
            )
        )).scalar_one_or_none()
        if existing is not None:
            existing.permission = permission
            existing.granted_by = granted_by
            await self.session.flush()
            return existing

        row = VoiceAccess(
            tenant_id=grantee_tenant_id,
            voice_id=voice.id,
            permission=permission,
            granted_by=granted_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def revoke(
        self, *, voice_slug: str, grantee_tenant_id: uuid.UUID,
    ) -> int:
        """Remove an access grant. Returns count removed (0 if no grant
        or owner didn't own the voice)."""
        voice = (await self.session.execute(
            select(Voice).where(
                Voice.owner_tenant_id == self.owner_tenant_id,
                Voice.voice_id == voice_slug,
            )
        )).scalar_one_or_none()
        if voice is None:
            return 0
        result = await self.session.execute(
            delete(VoiceAccess).where(
                VoiceAccess.voice_id == voice.id,
                VoiceAccess.tenant_id == grantee_tenant_id,
            )
        )
        await self.session.flush()
        return result.rowcount or 0

    async def list_grants(self, voice_slug: str) -> list[VoiceAccess]:
        """Who can see this owner's voice? Empty list if voice not owned."""
        voice = (await self.session.execute(
            select(Voice).where(
                Voice.owner_tenant_id == self.owner_tenant_id,
                Voice.voice_id == voice_slug,
            )
        )).scalar_one_or_none()
        if voice is None:
            return []
        rows = (await self.session.execute(
            select(VoiceAccess)
            .where(VoiceAccess.voice_id == voice.id)
            .order_by(VoiceAccess.granted_at)
        )).scalars().all()
        return list(rows)
