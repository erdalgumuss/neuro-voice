"""WatermarkKeyRepo — operator-managed AudioSeal payload allocations.

Each row represents one 16-bit slot allocation. Active keys gate
synthesis-time watermarking; retired keys stay in the table for
forensic queries against historical audio. See ADR-13.

NOT tenant-scoped — keys are NeuroVoice business records (similar to
talent_contracts). Voice rows reference a key via
`voices.watermark_key_id`; one key can back multiple voices
(per-tenant or per-jurisdiction allocation patterns).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WatermarkKey


_MAX_PAYLOAD = 0xFFFF  # 16-bit AudioSeal payload (0..65535)


class WatermarkKeyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key_id: uuid.UUID) -> WatermarkKey | None:
        return (await self.session.execute(
            select(WatermarkKey).where(WatermarkKey.id == key_id)
        )).scalar_one_or_none()

    async def get_active(self, key_id: uuid.UUID) -> WatermarkKey | None:
        """Return the key iff it exists and is active. Forensics paths
        that need historical lookup use `get()` instead."""
        return (await self.session.execute(
            select(WatermarkKey).where(
                WatermarkKey.id == key_id,
                WatermarkKey.retired_at.is_(None),
            )
        )).scalar_one_or_none()

    async def get_active_by_bits(
        self, message_bits: int,
    ) -> WatermarkKey | None:
        """Look up the active key carrying these bits. Used by the
        detection endpoint to map a decoded AudioSeal payload back to
        an allocation. Retired keys with the same bits are intentionally
        excluded — forensics paths that want historical matches use
        `list_by_bits` to enumerate every allocation that has ever
        carried this pattern."""
        return (await self.session.execute(
            select(WatermarkKey).where(
                WatermarkKey.message_bits == message_bits,
                WatermarkKey.retired_at.is_(None),
            )
        )).scalar_one_or_none()

    async def list_by_bits(
        self, message_bits: int,
    ) -> list[WatermarkKey]:
        """Enumerate every allocation (active + retired) that has
        carried this 16-bit pattern. Used by forensics when an old
        audio clip is being audited and its payload may belong to a
        retired allocation."""
        q = (
            select(WatermarkKey)
            .where(WatermarkKey.message_bits == message_bits)
            .order_by(WatermarkKey.allocated_at.desc())
        )
        return list((await self.session.execute(q)).scalars().all())

    async def list_active(self) -> list[WatermarkKey]:
        q = (
            select(WatermarkKey)
            .where(WatermarkKey.retired_at.is_(None))
            .order_by(WatermarkKey.allocated_at.desc())
        )
        return list((await self.session.execute(q)).scalars().all())

    async def _next_unused_bits(self) -> int | None:
        """Find a 16-bit pattern that no active key currently holds.
        Returns None if every slot in [0, 65535] is taken (operator
        must retire an existing key before allocating a new one).
        Uses a randomized probe instead of sequential scan so adversaries
        observing detected payloads can't infer allocation order."""
        # 64k slots — at most a few rounds of random probe needed even
        # at 50% occupancy. Cap iterations so we don't loop on a fully
        # saturated table.
        for _ in range(128):
            candidate = secrets.randbelow(_MAX_PAYLOAD + 1)
            existing = await self.get_active_by_bits(candidate)
            if existing is None:
                return candidate
        return None

    async def allocate(
        self,
        *,
        label: str,
        created_by_operator_id: uuid.UUID | None = None,
        notes: str | None = None,
        message_bits: int | None = None,
    ) -> WatermarkKey:
        """Allocate a new active key. If `message_bits` is omitted, a
        random unused 16-bit slot is chosen. Raises ValueError if every
        slot is already taken (operator must retire something first) or
        if the explicit `message_bits` is already active.
        """
        if message_bits is None:
            chosen = await self._next_unused_bits()
            if chosen is None:
                raise ValueError(
                    "no free 16-bit watermark slots; retire an existing "
                    "key before allocating a new one"
                )
            message_bits = chosen
        else:
            if not (0 <= message_bits <= _MAX_PAYLOAD):
                raise ValueError(
                    f"message_bits must be in 0..{_MAX_PAYLOAD}; "
                    f"got {message_bits}"
                )
            collision = await self.get_active_by_bits(message_bits)
            if collision is not None:
                raise ValueError(
                    f"message_bits {message_bits} is already held by "
                    f"active key {collision.id} ({collision.label!r})"
                )
        key = WatermarkKey(
            message_bits=message_bits,
            label=label,
            notes=notes,
            created_by_operator_id=created_by_operator_id,
        )
        self.session.add(key)
        await self.session.flush()
        return key

    async def retire(
        self,
        key_id: uuid.UUID,
        *,
        reason: str | None = None,
        retired_at: datetime | None = None,
    ) -> WatermarkKey | None:
        """Idempotent — re-retiring an already-retired key is a no-op
        (returns the row unchanged). Retiring a non-existent key
        returns None."""
        key = await self.get(key_id)
        if key is None or key.retired_at is not None:
            return key
        key.retired_at = retired_at or datetime.now(timezone.utc)
        key.retired_reason = reason
        await self.session.flush()
        return key
