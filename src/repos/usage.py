"""UsageRepo — tenant-scoped time-series writes + aggregate reads."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UsageRecord


class UsageRepo:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("tenant_id must be UUID")
        self.session = session
        self.tenant_id = tenant_id

    async def record(
        self,
        *,
        api_key_id: uuid.UUID,
        voice_id: str,
        request_id: uuid.UUID,
        text_char_count: int,
        sentence_count: int,
        duration_ms: int,
        elapsed_ms: int,
        ttfb_ms: int | None = None,
        rtf: float | None = None,
        status: str = "ok",
        error_code: str | None = None,
        worker_id: str | None = None,
        model_version: str | None = None,
    ) -> UsageRecord:
        rec = UsageRecord(
            tenant_id=self.tenant_id,
            api_key_id=api_key_id,
            voice_id=voice_id,
            request_id=request_id,
            text_char_count=text_char_count,
            sentence_count=sentence_count,
            duration_ms=duration_ms,
            elapsed_ms=elapsed_ms,
            ttfb_ms=ttfb_ms,
            rtf=rtf,
            status=status,
            error_code=error_code,
            worker_id=worker_id,
            model_version=model_version,
        )
        self.session.add(rec)
        await self.session.flush()
        return rec

    async def recent(self, limit: int = 100) -> list[UsageRecord]:
        return list((await self.session.execute(
            select(UsageRecord)
            .where(UsageRecord.tenant_id == self.tenant_id)
            .order_by(UsageRecord.occurred_at.desc())
            .limit(limit)
        )).scalars().all())

    async def summary_last_n_days(self, days: int = 30) -> dict:
        """Aggregate by status for the dashboard usage panel."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = (await self.session.execute(
            select(
                UsageRecord.status,
                func.count(UsageRecord.id).label("count"),
                func.coalesce(func.sum(UsageRecord.text_char_count), 0).label("chars"),
                func.coalesce(func.sum(UsageRecord.duration_ms), 0).label("audio_ms"),
                func.coalesce(func.avg(UsageRecord.rtf), 0.0).label("avg_rtf"),
            )
            .where(
                UsageRecord.tenant_id == self.tenant_id,
                UsageRecord.occurred_at >= since,
            )
            .group_by(UsageRecord.status)
        )).all()
        return {
            r.status: {
                "count": r.count,
                "chars": r.chars,
                "audio_ms": r.audio_ms,
                "avg_rtf": float(r.avg_rtf or 0.0),
            }
            for r in rows
        }
