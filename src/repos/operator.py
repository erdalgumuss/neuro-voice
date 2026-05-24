"""OperatorRepo — admin UI user management. Password hashing handled by
caller (src/server/security/passwords.py) — repo never sees plaintext."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Operator


class OperatorRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, operator_id: uuid.UUID) -> Operator | None:
        return await self.session.get(Operator, operator_id)

    async def get_by_email(self, email: str) -> Operator | None:
        return (await self.session.execute(
            select(Operator).where(Operator.email == email.lower())
        )).scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        full_name: str | None = None,
        roles: list[str] | None = None,
    ) -> Operator:
        op = Operator(
            email=email.lower(),
            password_hash=password_hash,
            full_name=full_name,
            roles=roles or ["admin"],
        )
        self.session.add(op)
        await self.session.flush()
        return op

    async def touch_login(self, operator_id: uuid.UUID) -> None:
        op = await self.get(operator_id)
        if op is not None:
            op.last_login_at = datetime.now(timezone.utc)
            await self.session.flush()
