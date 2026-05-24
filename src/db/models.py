"""ORM models — kanonik şema [data-model.md](../../docs/architecture/data-model.md).

Tablolar:
    tenants                outer customer (NEEKO/NIVA/NeuroCourse/NARO)
    operators              NQAI staff (admin UI users)
    api_keys               tenant credentials (Bearer)
    voices                 tenant-scoped voice catalog
    usage_records          time-series TTS request log
    audit_log              append-only security-relevant events
    job_idempotency        24h dedup cache for request_id

Tüm modeller `Base` üzerinden bağlanır; alembic --autogenerate'in tutarlı
diff üretmesi için naming convention `Base.metadata`'ya işli.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
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

from .base import Base, BigIntPk, TimestampMixin, new_uuid


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

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
    )
    voices: Mapped[list["Voice"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
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
        DateTime(timezone=True), nullable=False, default=lambda: __import__("datetime").datetime.utcnow()
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
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    created_by_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("operators.id", ondelete="SET NULL"),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)

    tenant: Mapped["Tenant"] = relationship(back_populates="api_keys")

    __table_args__ = (
        CheckConstraint(
            r"prefix ~ '^nqai_(prod|staging|dev)_[a-zA-Z0-9]{14}$'",
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
# Voices (tenant-scoped catalog)
# --------------------------------------------------------------------------- #
class Voice(Base, TimestampMixin):
    __tablename__ = "voices"

    id: Mapped[uuid.UUID] = mapped_column(_UUIDPortable, primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
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
    license: Mapped[str] = mapped_column(Text, nullable=False)
    engine_params: Mapped[dict[str, Any]] = mapped_column(
        _JSONBPortable, nullable=False, default=dict
    )

    # Faz 3+ (NULL ile başlar)
    adapter_uri: Mapped[str | None] = mapped_column(Text)
    adapter_sha256: Mapped[str | None] = mapped_column(Text)
    adapter_type: Mapped[str | None] = mapped_column(Text)
    watermark_key_id: Mapped[str | None] = mapped_column(Text)
    eval_metrics: Mapped[dict[str, Any] | None] = mapped_column(_JSONBPortable)
    release_status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")

    created_by_key_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("api_keys.id", ondelete="SET NULL"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped["Tenant"] = relationship(back_populates="voices")

    __table_args__ = (
        UniqueConstraint("tenant_id", "voice_id", name="tenant_voice_unique"),
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
            "source IN ('elevenlabs','voice-talent','user-enroll','placeholder','bootstrap')",
            name="source_enum",
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
            "ix_voices_tenant_active", "tenant_id",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "ix_voices_release", "tenant_id", "release_status",
            postgresql_where="deleted_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<Voice {self.voice_id!r} tenant={self.tenant_id}>"


# --------------------------------------------------------------------------- #
# Usage records (time-series)
# --------------------------------------------------------------------------- #
class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, ForeignKey("api_keys.id"), nullable=False
    )
    voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(
        _UUIDPortable, nullable=False, unique=True
    )
    text_char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sentence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ttfb_ms: Mapped[int | None] = mapped_column(Integer)
    rtf: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("text_char_count >= 0", name="text_char_count_nonneg"),
        CheckConstraint("sentence_count >= 0", name="sentence_count_nonneg"),
        CheckConstraint("duration_ms >= 0", name="duration_ms_nonneg"),
        CheckConstraint("elapsed_ms >= 0", name="elapsed_ms_nonneg"),
        CheckConstraint("ttfb_ms IS NULL OR ttfb_ms >= 0", name="ttfb_ms_nonneg"),
        CheckConstraint(
            "status IN ('ok','error','timeout','partial')",
            name="status_enum",
        ),
        Index("ix_usage_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_usage_key_time", "api_key_id", "occurred_at"),
    )


# --------------------------------------------------------------------------- #
# Audit log (append-only)
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(_UUIDPortable)
    actor_label: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUIDPortable, ForeignKey("tenants.id")
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
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('processing','complete','failed')",
            name="status_enum",
        ),
        Index("ix_idempotency_expires", "expires_at"),
    )
