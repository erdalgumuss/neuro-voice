"""VoiceConsentRecordRepo — 1:N voice → consent records (append-mostly).

A voice accumulates consent records over its lifetime: an initial
tenant-asserted attestation may later be upgraded by an operator-uploaded
signed contract, then eventually revoked. Active consent = latest row
with `revoked_at IS NULL` per voice; synthesis-time gating reads this.

Voice-scoped (the voice owner's tenant is the effective scope), but
intentionally NOT tenant-scoped in the repo constructor — operator
audit flows write records for voices across tenants. The tenant
guardrail is enforced in the route layer when a tenant-side actor
writes a record (the route already loads the voice through the
tenant-scoped VoiceRepo).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import VoiceConsentRecord


class VoiceConsentRecordRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_active(self, voice_id: uuid.UUID) -> VoiceConsentRecord | None:
        """Return the most recent unrevoked consent record for the voice.

        This is the synthesis-time gate. None means: no valid consent on
        record (the voice cannot be synthesised until a record is added).
        """
        return (await self.session.execute(
            select(VoiceConsentRecord)
            .where(
                VoiceConsentRecord.voice_id == voice_id,
                VoiceConsentRecord.revoked_at.is_(None),
            )
            .order_by(VoiceConsentRecord.recorded_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    async def list_for_voice(
        self, voice_id: uuid.UUID, *, include_revoked: bool = True,
    ) -> list[VoiceConsentRecord]:
        q = select(VoiceConsentRecord).where(
            VoiceConsentRecord.voice_id == voice_id,
        )
        if not include_revoked:
            q = q.where(VoiceConsentRecord.revoked_at.is_(None))
        q = q.order_by(VoiceConsentRecord.recorded_at.desc())
        return list((await self.session.execute(q)).scalars().all())

    async def record(
        self,
        *,
        voice_id: uuid.UUID,
        consent_kind: str,
        recorded_by_kind: str,
        recorded_by_actor_id: uuid.UUID | None = None,
        evidence_uri: str | None = None,
        evidence_sha256: str | None = None,
        evidence_notes: str | None = None,
    ) -> VoiceConsentRecord:
        # Light app-layer pre-check; the DB CHECK constraint
        # ck_voice_consent_records_evidence_presence is authoritative.
        # We raise here so callers see a useful Python exception instead
        # of an asyncpg IntegrityError at flush time.
        if consent_kind == "tenant-asserted" and evidence_uri is not None:
            raise ValueError(
                "tenant-asserted consent must have evidence_uri=None"
            )
        if consent_kind != "tenant-asserted" and evidence_uri is None:
            raise ValueError(
                f"consent_kind={consent_kind!r} requires a non-null evidence_uri"
            )
        r = VoiceConsentRecord(
            voice_id=voice_id,
            consent_kind=consent_kind,
            evidence_uri=evidence_uri,
            evidence_sha256=evidence_sha256,
            evidence_notes=evidence_notes,
            recorded_by_kind=recorded_by_kind,
            recorded_by_actor_id=recorded_by_actor_id,
        )
        self.session.add(r)
        await self.session.flush()
        return r

    async def revoke(
        self,
        consent_id: uuid.UUID,
        *,
        reason: str | None = None,
        revoked_at: datetime | None = None,
    ) -> VoiceConsentRecord | None:
        r = (await self.session.execute(
            select(VoiceConsentRecord).where(VoiceConsentRecord.id == consent_id)
        )).scalar_one_or_none()
        if r is None or r.revoked_at is not None:
            return None
        r.revoked_at = revoked_at or datetime.now(timezone.utc)
        r.revoked_reason = reason
        await self.session.flush()
        return r
