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
needs that should go through an operator-scoped repo (Faz B+).

Mutation methods (create / soft_delete / set_release_status / set_visibility)
are owner-only — the viewer must also be the owner. Cross-tenant mutation
returns None (existence-leak prevention).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Voice, VoiceAccess


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
        reference_sample_rate: int = 16000,
        language: str = "tr",
        gender: str = "neutral",
        style_tags: list[str] | None = None,
        source: str = "user-enroll",
        license: str = "user-owned",
        visibility: str = "private",
        engine_params: dict[str, Any] | None = None,
        created_by_key_id: uuid.UUID | None = None,
    ) -> Voice:
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
            license=license,
            visibility=visibility,
            engine_params=engine_params or {},
            created_by_key_id=created_by_key_id,
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
        """Faz B.5 Dalga 2.4 — owner-only metadata edit.

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
        future flip to 'shared' restores those grants). Faz B+ admin
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
