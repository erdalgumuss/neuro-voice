"""VoiceRepo — viewer-tenant scoped catalog access.

Refactor R (2026-05-24): the repo's `tenant_id` is the **viewer** — the
caller asking "what can I see/use". A voice is accessible to viewer T
when ANY of:

    * owner_tenant_id = T                                  (own voice)
    * visibility = 'public' AND deleted_at IS NULL         (open catalog)
    * a voice_access row exists with tenant_id = T         (explicit grant)

D-08 (tenant_id mandatory filter) is preserved — every accessibility
query carries `self.tenant_id` either as owner check or as access-grant
filter. There is no "list all voices" method; an admin operator that
needs that should go through an operator-scoped repo .

Mutation methods (create / soft_delete / set_release_status / set_visibility)
are owner-only — the viewer must also be the owner. Cross-tenant mutation
returns None (existence-leak prevention).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Voice, VoiceAccess

# ADR-11 — default retention between freeze and purge eligibility.
# GDPR Article 17 ("right to erasure") expects fulfillment "without
# undue delay" within at most one month; 30 days is the conservative
# default. Multi-jurisdiction overrides live in a future ADR.
DEFAULT_PURGE_DELAY_DAYS = 30


def lifecycle_state(voice: Voice) -> str:
    """Derive the voice's lifecycle state from its timestamp columns.

    Order matters — the most specific terminal state wins. `purged`
    is terminal (no recovery); `deleted` is recoverable by an operator
    within retention; `purge-pending` and `frozen` are reversible up
    until purge executes.
    """
    if voice.purged_at is not None:
        return "purged"
    if voice.deleted_at is not None:
        return "deleted"
    if voice.purge_after_at is not None:
        return "purge-pending"
    if voice.frozen_at is not None:
        return "frozen"
    return "active"


class VoiceRepo:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("tenant_id must be UUID")
        self.session = session
        self.tenant_id = tenant_id

    # ------------------------------------------------------------------ #
    # Read — accessible to viewer (owner ∪ public ∪ shared grants)
    # ------------------------------------------------------------------ #
    async def list_accessible(self, include_deleted: bool = False) -> list[Voice]:
        """All voices the viewer can use. Owned + public + shared-with-me.

        Ordering: owned first (alpha), then public/shared (alpha). Owner
        wins ties when the same slug exists in multiple buckets.
        """
        # Subquery: voice IDs explicitly granted to this tenant.
        granted_voice_ids = (
            select(VoiceAccess.voice_id)
            .where(VoiceAccess.tenant_id == self.tenant_id)
            .scalar_subquery()
        )
        q = select(Voice).where(
            or_(
                Voice.owner_tenant_id == self.tenant_id,
                Voice.visibility == "public",
                Voice.id.in_(granted_voice_ids),
            )
        )
        if not include_deleted:
            q = q.where(Voice.deleted_at.is_(None))
        q = q.order_by(Voice.voice_id)
        return list((await self.session.execute(q)).scalars().all())

    async def get_accessible(self, voice_id: str) -> Voice | None:
        """Resolve a voice slug to a Voice row visible to this viewer.

        Preference order when the same slug exists in multiple buckets
        (rare — would require a tenant owning a voice with the same slug
        as a public voice from another owner):
          1. owned       — viewer is owner_tenant_id
          2. shared      — voice_access grant for viewer
          3. public      — visibility='public'

        Returns None if no accessible match (caller maps to 404, never
        403 — existence-leak prevention, D-08).
        """
        owned = await self.get_owned(voice_id)
        if owned is not None:
            return owned

        # Shared — explicit grant.
        granted_voice_ids = (
            select(VoiceAccess.voice_id)
            .where(VoiceAccess.tenant_id == self.tenant_id)
            .scalar_subquery()
        )
        shared = (await self.session.execute(
            select(Voice)
            .where(
                Voice.voice_id == voice_id,
                Voice.id.in_(granted_voice_ids),
                Voice.deleted_at.is_(None),
            )
            .order_by(Voice.created_at)
            .limit(1)
        )).scalar_one_or_none()
        if shared is not None:
            return shared

        # Public — anyone can see.
        return (await self.session.execute(
            select(Voice)
            .where(
                Voice.voice_id == voice_id,
                Voice.visibility == "public",
                Voice.deleted_at.is_(None),
            )
            .order_by(Voice.created_at)
            .limit(1)
        )).scalar_one_or_none()

    async def get_owned(self, voice_id: str) -> Voice | None:
        return (await self.session.execute(
            select(Voice)
            .where(
                Voice.owner_tenant_id == self.tenant_id,
                Voice.voice_id == voice_id,
                Voice.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # Write — owner-only (viewer must be owner_tenant_id)
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        voice_id: str,
        display_name: str,
        reference_uri: str,
        reference_sha256: str,
        reference_seconds: float,
        license_kind: str,
        reference_sample_rate: int = 16000,
        language: str = "tr",
        gender: str = "neutral",
        style_tags: list[str] | None = None,
        source: str = "tenant-enroll",
        license_ref: str | None = None,
        visibility: str = "private",
        engine_params: dict[str, Any] | None = None,
        created_by_key_id: uuid.UUID | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        preview_url: str | None = None,
        voice_settings_defaults: dict[str, Any] | None = None,
    ) -> Voice:
        # `license_kind` is required (no default) — ADR-10 closes the
        # license taxonomy. Callers that previously passed `license=...`
        # must now decide a license_kind explicitly; if the caller wants
        # the prior behaviour, pass `license_kind='user-owned'`.
        v = Voice(
            owner_tenant_id=self.tenant_id,
            voice_id=voice_id,
            display_name=display_name,
            language=language,
            gender=gender,
            style_tags=style_tags or [],
            reference_uri=reference_uri,
            reference_sha256=reference_sha256,
            reference_seconds=reference_seconds,
            reference_sample_rate=reference_sample_rate,
            source=source,
            license_kind=license_kind,
            license_ref=license_ref,
            visibility=visibility,
            engine_params=engine_params or {},
            created_by_key_id=created_by_key_id,
            description=description,
            labels=labels,
            preview_url=preview_url,
            voice_settings_defaults=voice_settings_defaults,
        )
        self.session.add(v)
        await self.session.flush()
        return v

    async def update_metadata(
        self,
        voice_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        preview_url: str | None = None,
        voice_settings_defaults: dict[str, Any] | None = None,
        style_tags: list[str] | None = None,
        visibility: str | None = None,
    ) -> Voice | None:
        """owner-only metadata edit.

        Returns None if the voice is not owned by this repo's tenant
        (caller maps to 404 — same existence-leak rule as soft_delete).
        Only non-None fields are written. Reference audio + voice_id
        slug are immutable here; re-enroll for those.
        """
        v = await self.get_owned(voice_id)
        if v is None:
            return None
        if display_name is not None:
            v.display_name = display_name
        if description is not None:
            v.description = description
        if labels is not None:
            v.labels = labels
        if preview_url is not None:
            v.preview_url = preview_url
        if voice_settings_defaults is not None:
            v.voice_settings_defaults = voice_settings_defaults
        if style_tags is not None:
            v.style_tags = style_tags
        if visibility is not None:
            if visibility not in {"private", "shared", "public"}:
                raise ValueError(f"invalid visibility '{visibility}'")
            v.visibility = visibility
        await self.session.flush()
        return v

    async def soft_delete(self, voice_id: str) -> Voice | None:
        """Owner-only. Returns None if not owned (so the caller maps to
        404 — a non-owner tenant trying to delete a voice it sees via
        public/shared must NOT learn it exists)."""
        v = await self.get_owned(voice_id)
        if v is None:
            return None
        v.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return v

    async def set_release_status(self, voice_id: str, status: str) -> Voice | None:
        if status not in {"draft", "staging", "production", "deprecated"}:
            raise ValueError(f"invalid release_status '{status}'")
        v = await self.get_owned(voice_id)
        if v is None:
            return None
        v.release_status = status
        await self.session.flush()
        return v

    async def set_visibility(self, voice_id: str, visibility: str) -> Voice | None:
        """Owner-only — changes how the voice is exposed to other tenants.

        'private' → only owner; 'shared' → owner + voice_access grants;
        'public'  → every active tenant. Setting back to 'private' does
        NOT delete existing voice_access rows (they become latent — a
        future flip to 'shared' restores those grants). future admin
        flow may add an explicit `revoke_all_access` op.
        """
        if visibility not in {"private", "shared", "public"}:
            raise ValueError(f"invalid visibility '{visibility}'")
        v = await self.get_owned(voice_id)
        if v is None:
            return None
        v.visibility = visibility
        await self.session.flush()
        return v

    # ------------------------------------------------------------------ #
    # Lifecycle — freeze / unfreeze / schedule purge / execute purge
    # ------------------------------------------------------------------ #
    # All four methods operate by voice DB UUID (cross-tenant for
    # operator paths) — owner-scoped methods above use voice_id slug.
    # Routes that want owner-only enforcement pre-resolve via
    # get_owned(slug) and then pass voice.id here.
    async def get_by_id(self, voice_db_id: uuid.UUID) -> Voice | None:
        return (await self.session.execute(
            select(Voice).where(Voice.id == voice_db_id)
        )).scalar_one_or_none()

    async def freeze(
        self,
        voice_db_id: uuid.UUID,
        *,
        reason: str,
        purge_after_days: int | None = None,
    ) -> Voice | None:
        """Reversible synthesis stop. Data preserved.

        Idempotent: re-freezing an already-frozen voice updates the
        reason (and optionally extends purge_after_at) without
        resetting the original `frozen_at`. Already-purged voices are
        left alone and the call returns the row unchanged.

        `purge_after_days` set → schedule purge eligibility in N days
        from now (typically 30 per ADR-11). NULL → frozen indefinitely;
        operator schedules purge later if needed.
        """
        v = await self.get_by_id(voice_db_id)
        if v is None or v.purged_at is not None:
            return v
        now = datetime.now(timezone.utc)
        if v.frozen_at is None:
            v.frozen_at = now
        v.frozen_reason = reason
        if purge_after_days is not None:
            scheduled = now + timedelta(days=purge_after_days)
            # Don't shorten an already-scheduled purge silently.
            if v.purge_after_at is None or scheduled > v.purge_after_at:
                v.purge_after_at = scheduled
        await self.session.flush()
        return v

    async def unfreeze(self, voice_db_id: uuid.UUID) -> Voice | None:
        """Clear frozen state. Caller MUST verify the underlying cause
        is cleared (e.g. a new active consent record exists) — the repo
        does not enforce that; the orchestration route does.

        A purged voice cannot be unfrozen (terminal state). A voice
        with purge_after_at already set keeps that schedule — the
        operator must call clear_purge_schedule() explicitly to roll
        back, which we don't expose in v0 (rare; manual SQL if needed).
        """
        v = await self.get_by_id(voice_db_id)
        if v is None or v.purged_at is not None:
            return v
        v.frozen_at = None
        v.frozen_reason = None
        await self.session.flush()
        return v

    async def schedule_purge(
        self,
        voice_db_id: uuid.UUID,
        *,
        after_days: int = DEFAULT_PURGE_DELAY_DAYS,
    ) -> Voice | None:
        """Set purge_after_at and (if not already) frozen_at. Once
        set, the voice cannot be synthesised against (lifecycle gate
        treats purge-pending as a non-active state)."""
        v = await self.get_by_id(voice_db_id)
        if v is None or v.purged_at is not None:
            return v
        now = datetime.now(timezone.utc)
        if v.frozen_at is None:
            v.frozen_at = now
            v.frozen_reason = v.frozen_reason or "scheduled for purge"
        v.purge_after_at = now + timedelta(days=after_days)
        await self.session.flush()
        return v

    async def pin_eval(
        self,
        voice_db_id: uuid.UUID,
        *,
        payload: dict[str, Any],
    ) -> Voice | None:
        """Stamp an eval result onto `voices.eval_metrics` (ADR-12).

        Payload schema is the blob written under `voices.eval_metrics`
        — see `docs/decisions/2026-05-28-eval-pin.md` §4 for the full
        shape. Required top-level keys: `schema_version`, `evaluated_at`,
        `metrics`. Idempotent — re-pinning overwrites.

        Returns the mutated voice row (or None if voice_db_id is
        unknown / purged). Purged voices cannot be re-pinned; pinning
        a deleted-but-not-purged voice is allowed (operator may want
        to record a final eval before purging).
        """
        required = ("schema_version", "evaluated_at", "metrics")
        missing = [k for k in required if k not in payload]
        if missing:
            raise ValueError(
                f"eval_metrics payload missing required keys: {missing}"
            )
        if not isinstance(payload["metrics"], dict) or not payload["metrics"]:
            raise ValueError(
                "eval_metrics payload `metrics` must be a non-empty dict"
            )
        v = await self.get_by_id(voice_db_id)
        if v is None or v.purged_at is not None:
            return v
        v.eval_metrics = payload
        await self.session.flush()
        return v

    async def execute_purge(
        self,
        voice_db_id: uuid.UUID,
        *,
        purged_at: datetime | None = None,
    ) -> Voice | None:
        """Anonymise the row. R2 deletion is the caller's responsibility
        (operator route holds the R2 client + audit log writer); this
        method only mutates the DB row.

        Idempotent: re-purging a tombstoned voice is a no-op.
        Returns the mutated row (or None if voice_db_id is unknown).
        """
        v = await self.get_by_id(voice_db_id)
        if v is None or v.purged_at is not None:
            return v
        # PII + artifact scrub. usage_records + audit_log reference the
        # voice_id slug as a plain string, not a FK, so they keep
        # working after this row becomes a tombstone.
        v.reference_uri = ""              # NOT NULL column; empty string
        v.reference_sha256 = "0" * 64     # placeholder; CHECK length=64
        v.adapter_uri = None
        v.adapter_sha256 = None
        v.adapter_type = None
        v.display_name = "[purged voice]"
        v.description = None
        v.labels = None
        v.preview_url = None
        v.voice_settings_defaults = None
        v.engine_params = {}
        v.eval_metrics = None
        v.purged_at = purged_at or datetime.now(timezone.utc)
        await self.session.flush()
        return v
