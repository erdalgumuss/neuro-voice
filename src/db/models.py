"""ORM models — canonical schema for the platform DB.

Tables:
    tenants                outer customer (downstream product, integration,
                           or external API consumer)
    operators              platform staff (admin UI users)
    api_keys               tenant credentials (Bearer)
    voices                 tenant-scoped voice catalog
    usage_records          time-series TTS request log
    audit_log              append-only security-relevant events
    job_idempotency        24h dedup cache for request_id

All models bind through `Base`; the naming convention is set on
`Base.metadata` so alembic --autogenerate produces stable diffs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .base import Base, BigIntPk, TimestampMixin, new_uuid, utcnow

# --------------------------------------------------------------------------- #
# Cross-dialect helpers
# --------------------------------------------------------------------------- #
# JSONB / ARRAY / INET / UUID are Postgres-specific. For SQLite test runs we
# downgrade transparently — production Alembic migrations always emit the
# Postgres types.

class _JSONBPortable(TypeDecorator):
    """JSONB on Postgres, JSON elsewhere."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class _UUIDPortable(TypeDecorator):
    """UUID on Postgres, CHAR(36) elsewhere."""
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class _StringArrayPortable(TypeDecorator):
    """TEXT[] on Postgres, JSON list elsewhere."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text()))
        return dialect.type_descriptor(JSON())


class _INETPortable(TypeDecorator):
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(INET())
        return dialect.type_descriptor(String(45))


# --------------------------------------------------------------------------- #
# Tenants
# --------------------------------------------------------------------------- #
class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(_UUIDPortable, primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _JSONBPortable, nullable=False, default=dict
    )

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
    )
    voices: Mapped[list[Voice]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True,
        foreign_keys="Voice.owner_tenant_id",
    )

    __table_args__ = (
        CheckConstraint(
            r"slug ~ '^[a-z][a-z0-9-]{1,62}[a-z0-9]$'",
            name="slug_format",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 120",
            name="display_name_length",
        ),
        CheckConstraint(
            "status IN ('active','suspended','deleted')",
            name="status_enum",
        ),
        Index("ix_tenants_status_active", "status", postgresql_where="deleted_at IS NULL"),
    )

    def __repr__(self) -> str:
        return f"<Tenant {self.slug!r}>"


# --------------------------------------------------------------------------- #
# Operators (admin UI users)
# --------------------------------------------------------------------------- #
class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(_UUIDPortable, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(
        _StringArrayPortable, nullable=False, default=lambda: ["admin"]
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(r"email ~ '^[^@]+@[^@]+\.[^@]+$'", name="email_format"),
    )

    def __repr__(self) -> str:
        return f"<Operator {self.email!r}>"


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(_UUIDPortable, primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    prefix: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        _StringArrayPortable, nullable=False,
        default=lambda: ["tts:read", "tts:write"],
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow,
    )
    created_by_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("operators.id", ondelete="SET NULL"),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")

    __table_args__ = (
        CheckConstraint(
            r"prefix ~ '^nv_(prod|staging|dev)_[a-zA-Z0-9]{14}$'",
            name="prefix_format",
        ),
        CheckConstraint("rate_limit_per_minute > 0", name="rate_limit_positive"),
        Index("ix_api_keys_tenant_id", "tenant_id"),
        Index(
            "ix_api_keys_prefix_active", "prefix",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:
        return f"<ApiKey {self.prefix!r} tenant={self.tenant_id}>"


# --------------------------------------------------------------------------- #
# Voices — owned by a tenant, visible to others via visibility + voice_access
# --------------------------------------------------------------------------- #
# Refactor R (2026-05-24): tenant = account/workspace, not product. A voice
# has a single owner_tenant_id; cross-tenant visibility is mediated by the
# `visibility` enum + the `voice_access` table:
#   visibility='private' → only owner sees/uses the voice
#   visibility='shared'  → owner + tenants listed in voice_access see it
#   visibility='public'  → every active tenant sees it
# voice_id slug uniqueness stays scoped to owner — two workspaces can each
# have a voice called "ayse" without collision.
class Voice(Base, TimestampMixin):
    __tablename__ = "voices"

    id: Mapped[uuid.UUID] = mapped_column(_UUIDPortable, primary_key=True, default=new_uuid)
    owner_tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False, default="tr")
    gender: Mapped[str] = mapped_column(Text, nullable=False, default="neutral")
    style_tags: Mapped[list[str]] = mapped_column(
        _StringArrayPortable, nullable=False, default=list
    )
    reference_uri: Mapped[str] = mapped_column(Text, nullable=False)
    reference_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    reference_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    reference_sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=16000)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # ADR-10 — closed-list taxonomy (DB CHECK constraint). Per-row
    # cross-validation with `license_ref` (e.g. 'talent-contract' must
    # carry a talent_contracts.id) is application-layer; no FK on
    # license_ref so it can also hold a partner-agreement URL or
    # public-figure rationale string (polymorphic by design).
    license_kind: Mapped[str] = mapped_column(Text, nullable=False)
    license_ref: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="private")
    engine_params: Mapped[dict[str, Any]] = mapped_column(
        _JSONBPortable, nullable=False, default=dict
    )

    # voice catalog enrichment (vendor parity).
    # All nullable so pre-existing rows stay valid; populated via
    # POST /v1/voices (enroll) or PATCH /v1/voices/{voice_id} (update).
    description: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[list[str] | None] = mapped_column(_StringArrayPortable)
    preview_url: Mapped[str | None] = mapped_column(Text)
    # voice_settings_defaults: per-voice baseline that the per-request
    # voice_settings layers on top of. Vendor-shape dict —
    # {stability, similarity_boost, speed, style, ...}. Distinct from
    # `engine_params` which holds the internal cfg_value/timesteps.
    voice_settings_defaults: Mapped[dict[str, Any] | None] = mapped_column(
        _JSONBPortable,
    )

    # LoRA / adapter columns — NULL on plain catalog rows; populated
    # once a voice ships with a fine-tuned adapter manifest.
    adapter_uri: Mapped[str | None] = mapped_column(Text)
    adapter_sha256: Mapped[str | None] = mapped_column(Text)
    adapter_type: Mapped[str | None] = mapped_column(Text)
    # ADR-13 — UUID FK to watermark_keys.id (was TEXT in ADR-7's
    # forward-shape). v0.x DROP+ADD migration converted the type.
    watermark_key_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable,
        ForeignKey("watermark_keys.id", ondelete="SET NULL"),
    )
    # ADR-13 — default TRUE; license-kind invariants enforced app-layer.
    watermark_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    eval_metrics: Mapped[dict[str, Any] | None] = mapped_column(_JSONBPortable)
    release_status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")

    created_by_key_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("api_keys.id", ondelete="SET NULL"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ADR-11 — lifecycle columns. lifecycle_state is DERIVED in app code
    # from these four timestamps + deleted_at; no stored enum, no
    # Postgres GENERATED column (kept portable for SQLite-backed tests).
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frozen_reason: Mapped[str | None] = mapped_column(Text)
    purge_after_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[Tenant] = relationship(back_populates="voices")
    access_grants: Mapped[list[VoiceAccess]] = relationship(
        back_populates="voice", cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_tenant_id", "voice_id",
            name="uq_voices_owner_tenant_id_voice_id",
        ),
        CheckConstraint(
            r"voice_id ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'",
            name="voice_id_format",
        ),
        CheckConstraint("language IN ('tr','en')", name="language_enum"),
        CheckConstraint("gender IN ('neutral','female','male')", name="gender_enum"),
        CheckConstraint("char_length(reference_sha256) = 64", name="sha256_length"),
        CheckConstraint(
            "reference_seconds > 0 AND reference_seconds <= 60",
            name="reference_seconds_range",
        ),
        CheckConstraint(
            "source IN ('bootstrap','tenant-enroll','talent-recorded',"
            "'synthetic-from-prompt','partner-import')",
            name="source_enum",
        ),
        CheckConstraint(
            "license_kind IN ('example','synthetic','user-owned',"
            "'talent-contract','public-figure','partner-licensed')",
            name="license_kind_enum",
        ),
        CheckConstraint(
            "visibility IN ('private','shared','public')",
            name="visibility_enum",
        ),
        CheckConstraint(
            "release_status IN ('draft','staging','production','deprecated')",
            name="release_status_enum",
        ),
        CheckConstraint(
            "adapter_type IS NULL OR adapter_type IN ('lora','full-finetune')",
            name="adapter_type_enum",
        ),
        Index(
            "ix_voices_owner_tenant_id_active", "owner_tenant_id",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "ix_voices_public", "visibility",
            postgresql_where="visibility = 'public' AND deleted_at IS NULL",
        ),
        Index(
            "ix_voices_release", "owner_tenant_id", "release_status",
            postgresql_where="deleted_at IS NULL",
        ),
        # ADR-11 lifecycle ordering invariants. Either timestamp can be
        # independently NULL; the order check only fires when both
        # endpoints are populated.
        CheckConstraint(
            "purge_after_at IS NULL OR frozen_at IS NULL "
            "OR purge_after_at >= frozen_at",
            name="voices_purge_after_frozen",
        ),
        CheckConstraint(
            "purged_at IS NULL OR purge_after_at IS NULL "
            "OR purged_at >= purge_after_at",
            name="voices_purged_after_purge_after",
        ),
        Index(
            "ix_voices_frozen", "frozen_at",
            postgresql_where="frozen_at IS NOT NULL AND purged_at IS NULL",
        ),
        Index(
            "ix_voices_purge_pending", "purge_after_at",
            postgresql_where="purge_after_at IS NOT NULL AND purged_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<Voice {self.voice_id!r} owner={self.owner_tenant_id}>"


# --------------------------------------------------------------------------- #
# VoiceAccess — explicit cross-tenant grants for visibility='shared' voices
# --------------------------------------------------------------------------- #
# Public voices bypass this table (every active tenant sees them). Private
# voices ignore this table entirely. Only when visibility='shared' do these
# rows mediate access. Insertion is admin-operator territory (+
# `POST /v1/voices/{id}/share` endpoint — out of scope for refactor R).
class VoiceAccess(Base):
    __tablename__ = "voice_access"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
    )
    voice_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("voices.id", ondelete="CASCADE"), nullable=False,
    )
    permission: Mapped[str] = mapped_column(Text, nullable=False, default="use")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow,
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("operators.id", ondelete="SET NULL"),
    )

    voice: Mapped[Voice] = relationship(back_populates="access_grants")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "voice_id",
            name="uq_voice_access_tenant_id_voice_id",
        ),
        CheckConstraint(
            "permission IN ('use','read')",
            name="ck_voice_access_permission_enum",
        ),
        Index("ix_voice_access_tenant_id", "tenant_id"),
        Index("ix_voice_access_voice_id", "voice_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<VoiceAccess voice={self.voice_id} tenant={self.tenant_id} "
            f"perm={self.permission}>"
        )


# --------------------------------------------------------------------------- #
# Usage records (time-series)
# --------------------------------------------------------------------------- #
class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow,
    )
    # FK ondelete=RESTRICT — usage records are financial history; a
    # tenant or key with any usage cannot be hard-deleted. Operators
    # soft-delete via Tenant.status='deleted' / ApiKey.revoked_at
    # (audit L5 H3 2026-05-25 — making the policy explicit at the
    # schema level rather than relying on the Postgres default).
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable,
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable,
        ForeignKey("api_keys.id", ondelete="RESTRICT"),
        nullable=False,
    )
    voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, nullable=False, unique=True
    )
    text_char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sentence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_wait_ms: Mapped[int | None] = mapped_column(Integer)
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    ttfb_ms: Mapped[int | None] = mapped_column(Integer)
    # Latency waterfall (migration 0004):
    # `worker_pickup_ms`     = (worker start) - payload.enqueued_at_ms
    # `reference_resolve_ms` = resolve_reference_uri duration
    # `first_pcm_ms`         = inference start → first engine SynthChunk
    # `first_audio_ms`       = inference start → first publish_chunk XADD
    worker_pickup_ms: Mapped[int | None] = mapped_column(Integer)
    reference_resolve_ms: Mapped[int | None] = mapped_column(Integer)
    first_pcm_ms: Mapped[int | None] = mapped_column(Integer)
    first_audio_ms: Mapped[int | None] = mapped_column(Integer)
    #  v1 item 1 — gateway-side TTFB (migration 0005). Only populated
    # for `/v1/tts/stream`; async jobs and sync `/v1/tts` leave NULL.
    # The gateway writes this via UPDATE after its streaming generator
    # emits its first chunk (the worker writes everything else).
    gateway_first_byte_ms: Mapped[int | None] = mapped_column(Integer)
    rtf: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    # Product attribution from the X-NV-App request header. NULL when
    # the header was absent — the field is advisory metadata, not a
    # billing primary key (tenant_id stays the source of truth for who
    # pays). Header prefix pending brand-ADR.
    app_label: Mapped[str | None] = mapped_column(Text)

    # MLOps PR #1 (2026-05-25) — reproducibility audit trail. JSONB
    # snapshot of every input the engine actually saw for THIS request:
    # `{model_id, hf_revision, preset_id, cfg_value, inference_timesteps,
    #   seed, voice_settings_resolved, pronunciation_dict_size,
    #   previous_text_len, next_text_len, reference_sha256}`.
    # NULL on rows written before this column existed. Lets a future
    # quality drift investigation answer "what changed between t-1 and
    # t" without guessing — the inputs are pinned next to the latency
    # waterfall. Schema is open-ended on purpose; consumers must
    # tolerate missing keys (forward-compat).
    engine_inputs: Mapped[dict | None] = mapped_column(_JSONBPortable)

    __table_args__ = (
        CheckConstraint("text_char_count >= 0", name="text_char_count_nonneg"),
        CheckConstraint("sentence_count >= 0", name="sentence_count_nonneg"),
        CheckConstraint("duration_ms >= 0", name="duration_ms_nonneg"),
        CheckConstraint("elapsed_ms >= 0", name="elapsed_ms_nonneg"),
        CheckConstraint(
            "queue_wait_ms IS NULL OR queue_wait_ms >= 0",
            name="queue_wait_ms_nonneg",
        ),
        CheckConstraint(
            "inference_ms IS NULL OR inference_ms >= 0",
            name="inference_ms_nonneg",
        ),
        CheckConstraint("ttfb_ms IS NULL OR ttfb_ms >= 0", name="ttfb_ms_nonneg"),
        CheckConstraint(
            "worker_pickup_ms IS NULL OR worker_pickup_ms >= 0",
            name="worker_pickup_ms_nonneg",
        ),
        CheckConstraint(
            "reference_resolve_ms IS NULL OR reference_resolve_ms >= 0",
            name="reference_resolve_ms_nonneg",
        ),
        CheckConstraint(
            "first_pcm_ms IS NULL OR first_pcm_ms >= 0",
            name="first_pcm_ms_nonneg",
        ),
        CheckConstraint(
            "first_audio_ms IS NULL OR first_audio_ms >= 0",
            name="first_audio_ms_nonneg",
        ),
        CheckConstraint(
            "gateway_first_byte_ms IS NULL OR gateway_first_byte_ms >= 0",
            name="gateway_first_byte_ms_nonneg",
        ),
        CheckConstraint(
            "status IN ('ok','error','timeout','partial')",
            name="status_enum",
        ),
        Index("ix_usage_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_usage_key_time", "api_key_id", "occurred_at"),
        Index(
            "ix_usage_records_tenant_id_app_label_created_at",
            "tenant_id", "app_label", "occurred_at",
        ),
    )


# --------------------------------------------------------------------------- #
# Audit log (append-only)
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow,
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(_UUIDPortable)
    actor_label: Mapped[str | None] = mapped_column(Text)
    # ondelete=SET NULL — audit_log MUST survive tenant deletion (D-04
    # append-only + forensic). Pre-fix the FK defaulted to RESTRICT,
    # blocking any operator who tried to remove an orphaned tenant.
    # Audit L5 H2 2026-05-25.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id", ondelete="SET NULL"),
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    ip_addr: Mapped[str | None] = mapped_column(_INETPortable)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[uuid.UUID | None] = mapped_column(_UUIDPortable)
    payload: Mapped[dict[str, Any]] = mapped_column(
        _JSONBPortable, nullable=False, default=dict
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('api_key','operator','system')",
            name="actor_type_enum",
        ),
        CheckConstraint(
            "result IN ('success','denied','error')",
            name="result_enum",
        ),
        Index("ix_audit_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_audit_action_time", "action", "occurred_at"),
    )


# --------------------------------------------------------------------------- #
# Idempotency cache (24h TTL)
# --------------------------------------------------------------------------- #
class JobIdempotency(Base):
    __tablename__ = "job_idempotency"

    request_id: Mapped[uuid.UUID] = mapped_column(_UUIDPortable, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("api_keys.id"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=utcnow,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    # per-sentence alignment for long-form playback.
    # The worker fills this when the job completes; a list of
    # `{seq, start_ms, end_ms, text}` dicts in playback order. Lets
    # async clients render subtitles + scrub-bar timestamps without
    # parsing the WAV themselves. Nullable so pre-Dalga-3.2 rows stay
    # valid; format matches the SRT/JSON shape the gateway returns on
    # GET /v1/tts/jobs/{id}.
    sentence_alignment: Mapped[list[dict[str, Any]] | None] = mapped_column(
        _JSONBPortable,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('processing','complete','failed')",
            name="status_enum",
        ),
        Index("ix_idempotency_expires", "expires_at"),
    )


# --------------------------------------------------------------------------- #
# Talent contracts — NeuroVoice-side signed agreements for talent voices
# --------------------------------------------------------------------------- #
# Operator-managed; NOT tenant-scoped. A voice with
# `license_kind='talent-contract'` carries a talent_contracts.id in its
# `license_ref` column. See ADR-10.
class TalentContract(Base):
    __tablename__ = "talent_contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, primary_key=True, default=new_uuid,
    )
    talent_full_name: Mapped[str] = mapped_column(Text, nullable=False)
    contract_pdf_uri: Mapped[str] = mapped_column(Text, nullable=False)
    contract_pdf_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("operators.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "char_length(talent_full_name) BETWEEN 1 AND 200",
            name="talent_contracts_name_length",
        ),
        CheckConstraint(
            r"contract_pdf_sha256 ~ '^[0-9a-f]{64}$'",
            name="talent_contracts_sha256_format",
        ),
        CheckConstraint(
            "jurisdiction IS NULL OR jurisdiction = 'EU' "
            r"OR jurisdiction ~ '^[A-Z]{2}$'",
            name="talent_contracts_jurisdiction_format",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > signed_at",
            name="talent_contracts_expires_after_signed",
        ),
        Index(
            "ix_talent_contracts_active", "signed_at",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<TalentContract id={self.id} name={self.talent_full_name!r}>"


# --------------------------------------------------------------------------- #
# Voice consent records — 1:N voice → records (append-mostly)
# --------------------------------------------------------------------------- #
# A voice accumulates consent records over its lifetime: an initial
# tenant-asserted attestation may later be upgraded to a signed-contract
# upload, and eventually revoked. Active consent = latest row with
# `revoked_at IS NULL` per voice; application layer enforces the
# read-side semantics. See ADR-10.
class VoiceConsentRecord(Base):
    __tablename__ = "voice_consent_records"

    id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, primary_key=True, default=new_uuid,
    )
    voice_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("voices.id", ondelete="CASCADE"), nullable=False,
    )
    consent_kind: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL only when consent_kind='tenant-asserted' (no artifact in our
    # system; tenant accepts liability via the API call). Enforced by
    # ck_voice_consent_records_evidence_presence in migration 0010.
    evidence_uri: Mapped[str | None] = mapped_column(Text)
    evidence_sha256: Mapped[str | None] = mapped_column(Text)
    evidence_notes: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow,
    )
    recorded_by_kind: Mapped[str] = mapped_column(Text, nullable=False)
    # polymorphic: api_keys.id (tenant path) or operators.id (operator
    # path), keyed by recorded_by_kind. No FK so a single column carries
    # either without referential-integrity contortions.
    recorded_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(_UUIDPortable)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "consent_kind IN ('tenant-asserted','recorded-statement',"
            "'signed-contract','estate-permission')",
            name="voice_consent_records_consent_kind_enum",
        ),
        CheckConstraint(
            "recorded_by_kind IN ('tenant','operator')",
            name="voice_consent_records_recorded_by_kind_enum",
        ),
        CheckConstraint(
            "(consent_kind = 'tenant-asserted' AND evidence_uri IS NULL) "
            "OR (consent_kind <> 'tenant-asserted' AND evidence_uri IS NOT NULL)",
            name="voice_consent_records_evidence_presence",
        ),
        CheckConstraint(
            "evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="voice_consent_records_evidence_sha256_format",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= recorded_at",
            name="voice_consent_records_revoked_after_recorded",
        ),
        Index(
            "ix_voice_consent_records_active", "voice_id", "recorded_at",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<VoiceConsentRecord voice={self.voice_id} kind={self.consent_kind} "
            f"recorded_at={self.recorded_at}>"
        )


# --------------------------------------------------------------------------- #
# Data deletion requests — KVKK/GDPR Article 17 audit trail (ADR-11)
# --------------------------------------------------------------------------- #
# A tenant-initiated erasure ticket. Creating one DOES NOT immediately
# delete data — it freezes the named voices and sets purge_after_at on
# them. An operator then executes purge via the admin endpoint, which
# scrubs reference audio + adapter weights from R2 and anonymises the
# voice rows (tombstone state). The request row stays as a permanent
# audit record.
class DataDeletionRequest(Base):
    __tablename__ = "data_deletion_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, primary_key=True, default=new_uuid,
    )
    # RESTRICT — a tenant with deletion requests on file cannot be
    # hard-deleted (tenants.status='deleted' soft is the canonical
    # lifecycle).
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Empty list = "delete every voice this tenant owns". Slugs (not
    # FKs) so requests stay meaningful after voices are deleted/purged.
    voice_slugs: Mapped[list[str]] = mapped_column(
        _StringArrayPortable, nullable=False, default=list,
    )
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow,
    )
    # Polymorphic: api_keys.id (tenant-side) on creation, operators.id
    # on operator-driven processing. No FK; mirrors ADR-10
    # voice_consent_records.recorded_by_actor_id pattern.
    requested_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(_UUIDPortable)
    reason: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','in-progress','completed','rejected')",
            name="data_deletion_requests_status_enum",
        ),
        CheckConstraint(
            "jurisdiction IS NULL OR jurisdiction = 'EU' "
            r"OR jurisdiction ~ '^[A-Z]{2}$'",
            name="data_deletion_requests_jurisdiction_format",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="data_deletion_requests_completed_after_requested",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="data_deletion_requests_completed_at_consistency",
        ),
        Index(
            "ix_data_deletion_pending", "status", "requested_at",
            postgresql_where="status IN ('pending','in-progress')",
        ),
        Index(
            "ix_data_deletion_tenant", "tenant_id", "requested_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DataDeletionRequest tenant={self.tenant_id} status={self.status} "
            f"voices={len(self.voice_slugs)}>"
        )


# --------------------------------------------------------------------------- #
# Watermark keys — operator-managed AudioSeal 16-bit payload allocations
# --------------------------------------------------------------------------- #
# Each row is one allocation. Voice.watermark_key_id points here. The
# 16-bit `message_bits` is the value AudioSeal embeds into synthesized
# audio. Retired keys are kept (not deleted) so historical detection
# results stay resolvable to a key + allocation context. See ADR-13.
class WatermarkKey(Base):
    __tablename__ = "watermark_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, primary_key=True, default=new_uuid,
    )
    # 0..65535 (AudioSeal default 16-bit payload). Stored as INTEGER
    # for SQL ergonomics; worker converts to/from the bit pattern.
    message_bits: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow,
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("operators.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        CheckConstraint(
            "message_bits BETWEEN 0 AND 65535",
            name="watermark_keys_message_bits_range",
        ),
        CheckConstraint(
            "char_length(label) BETWEEN 1 AND 200",
            name="watermark_keys_label_length",
        ),
        CheckConstraint(
            "retired_at IS NULL OR retired_at >= allocated_at",
            name="watermark_keys_retired_after_allocated",
        ),
        # Partial unique — active keys hold a unique 16-bit slot.
        # Retired keys can share bit patterns (different allocation
        # contexts at different points in time).
        Index(
            "ix_watermark_keys_message_bits_active", "message_bits",
            unique=True, postgresql_where="retired_at IS NULL",
        ),
        Index(
            "ix_watermark_keys_active_allocated", "allocated_at",
            postgresql_where="retired_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        status = "retired" if self.retired_at else "active"
        return (
            f"<WatermarkKey {self.label!r} bits={self.message_bits} "
            f"{status}>"
        )
