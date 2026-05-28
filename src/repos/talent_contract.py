"""TalentContractRepo — operator-managed signed agreements.

NOT tenant-scoped. Talent contracts are NeuroVoice's business records;
tenants neither see nor write them. A voice with
`license_kind='talent-contract'` carries the contract's UUID in its
`license_ref` column (application-layer integrity — no FK, see ADR-10).

This repo is consumed by operator endpoints (JWT-authenticated admin
surface) and by the enroll path's app-layer validation when a tenant
attempts to reference an existing contract by id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TalentContract


class TalentContractRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, contract_id: uuid.UUID) -> TalentContract | None:
        return (await self.session.execute(
            select(TalentContract).where(TalentContract.id == contract_id)
        )).scalar_one_or_none()

    async def get_active(self, contract_id: uuid.UUID) -> TalentContract | None:
        """Return the contract iff it exists and is currently active.

        Active means: not revoked AND (no expiry OR expires in the future).
        Returns None for nonexistent, revoked, or expired contracts —
        callers map all three to a single "this license_ref is not
        currently honourable" failure mode to avoid leaking which one
        applies.
        """
        now = datetime.now(timezone.utc)
        return (await self.session.execute(
            select(TalentContract).where(
                TalentContract.id == contract_id,
                TalentContract.revoked_at.is_(None),
                (TalentContract.expires_at.is_(None))
                | (TalentContract.expires_at > now),
            )
        )).scalar_one_or_none()

    async def list_active(self) -> list[TalentContract]:
        now = datetime.now(timezone.utc)
        q = (
            select(TalentContract)
            .where(
                TalentContract.revoked_at.is_(None),
                (TalentContract.expires_at.is_(None))
                | (TalentContract.expires_at > now),
            )
            .order_by(TalentContract.signed_at.desc())
        )
        return list((await self.session.execute(q)).scalars().all())

    async def create(
        self,
        *,
        talent_full_name: str,
        contract_pdf_uri: str,
        contract_pdf_sha256: str,
        signed_at: datetime,
        created_by_operator_id: uuid.UUID | None = None,
        expires_at: datetime | None = None,
        jurisdiction: str | None = None,
        notes: str | None = None,
    ) -> TalentContract:
        c = TalentContract(
            talent_full_name=talent_full_name,
            contract_pdf_uri=contract_pdf_uri,
            contract_pdf_sha256=contract_pdf_sha256,
            signed_at=signed_at,
            expires_at=expires_at,
            jurisdiction=jurisdiction,
            notes=notes,
            created_by_operator_id=created_by_operator_id,
        )
        self.session.add(c)
        await self.session.flush()
        return c

    async def revoke(
        self, contract_id: uuid.UUID, *, revoked_at: datetime | None = None,
    ) -> TalentContract | None:
        c = await self.get(contract_id)
        if c is None or c.revoked_at is not None:
            return None
        c.revoked_at = revoked_at or datetime.now(timezone.utc)
        await self.session.flush()
        return c
