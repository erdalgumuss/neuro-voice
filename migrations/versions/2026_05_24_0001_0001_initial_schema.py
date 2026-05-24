"""initial schema — 7 tables per docs/architecture/data-model.md §1

Revision ID: 0001
Revises:
Create Date: 2026-05-24

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgcrypto provides gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ---- tenants ----------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        sa.CheckConstraint(r"slug ~ '^[a-z][a-z0-9-]{1,62}[a-z0-9]$'",
                           name="ck_tenants_slug_format"),
        sa.CheckConstraint("char_length(display_name) BETWEEN 1 AND 120",
                           name="ck_tenants_display_name_length"),
        sa.CheckConstraint("status IN ('active','suspended','deleted')",
                           name="ck_tenants_status_enum"),
    )
    op.create_index("ix_tenants_status_active", "tenants", ["status"],
                    postgresql_where=sa.text("deleted_at IS NULL"))

    # ---- operators --------------------------------------------------------
    op.create_table(
        "operators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text()),
        sa.Column("roles", postgresql.ARRAY(sa.Text()),
                  nullable=False, server_default=sa.text("ARRAY['admin']::TEXT[]")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email", name="uq_operators_email"),
        sa.CheckConstraint(r"email ~ '^[^@]+@[^@]+\.[^@]+$'",
                           name="ck_operators_email_format"),
    )

    # ---- api_keys ---------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()),
                  nullable=False,
                  server_default=sa.text("ARRAY['tts:read','tts:write']::TEXT[]")),
        sa.Column("rate_limit_per_minute", sa.Integer(),
                  nullable=False, server_default="60"),
        sa.Column("label", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by_operator_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE",
                                name="fk_api_keys_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["created_by_operator_id"], ["operators.id"],
                                ondelete="SET NULL",
                                name="fk_api_keys_created_by_operator_id_operators"),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
        sa.CheckConstraint(r"prefix ~ '^nqai_(prod|staging|dev)_[a-zA-Z0-9]{14}$'",
                           name="ck_api_keys_prefix_format"),
        sa.CheckConstraint("rate_limit_per_minute > 0",
                           name="ck_api_keys_rate_limit_positive"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_prefix_active", "api_keys", ["prefix"],
                    postgresql_where=sa.text("revoked_at IS NULL"))

    # ---- voices -----------------------------------------------------------
    op.create_table(
        "voices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voice_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="tr"),
        sa.Column("gender", sa.Text(), nullable=False, server_default="neutral"),
        sa.Column("style_tags", postgresql.ARRAY(sa.Text()),
                  nullable=False, server_default=sa.text("ARRAY[]::TEXT[]")),
        sa.Column("reference_uri", sa.Text(), nullable=False),
        sa.Column("reference_sha256", sa.Text(), nullable=False),
        sa.Column("reference_seconds", sa.Float(), nullable=False),
        sa.Column("reference_sample_rate", sa.Integer(),
                  nullable=False, server_default="16000"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("engine_params", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Faz 3+ alanları
        sa.Column("adapter_uri", sa.Text()),
        sa.Column("adapter_sha256", sa.Text()),
        sa.Column("adapter_type", sa.Text()),
        sa.Column("watermark_key_id", sa.Text()),
        sa.Column("eval_metrics", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("release_status", sa.Text(),
                  nullable=False, server_default="draft"),
        sa.Column("created_by_key_id", postgresql.UUID(as_uuid=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE",
                                name="fk_voices_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["created_by_key_id"], ["api_keys.id"],
                                ondelete="SET NULL",
                                name="fk_voices_created_by_key_id_api_keys"),
        sa.UniqueConstraint("tenant_id", "voice_id", name="uq_voices_tenant_voice"),
        sa.CheckConstraint(r"voice_id ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'",
                           name="ck_voices_voice_id_format"),
        sa.CheckConstraint("language IN ('tr','en')", name="ck_voices_language_enum"),
        sa.CheckConstraint("gender IN ('neutral','female','male')",
                           name="ck_voices_gender_enum"),
        sa.CheckConstraint("char_length(reference_sha256) = 64",
                           name="ck_voices_sha256_length"),
        sa.CheckConstraint(
            "reference_seconds > 0 AND reference_seconds <= 60",
            name="ck_voices_reference_seconds_range"),
        sa.CheckConstraint(
            "source IN ('elevenlabs','voice-talent','user-enroll','placeholder','bootstrap')",
            name="ck_voices_source_enum"),
        sa.CheckConstraint(
            "release_status IN ('draft','staging','production','deprecated')",
            name="ck_voices_release_status_enum"),
        sa.CheckConstraint(
            "adapter_type IS NULL OR adapter_type IN ('lora','full-finetune')",
            name="ck_voices_adapter_type_enum"),
    )
    op.create_index("ix_voices_tenant_active", "voices", ["tenant_id"],
                    postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("ix_voices_release", "voices", ["tenant_id", "release_status"],
                    postgresql_where=sa.text("deleted_at IS NULL"))

    # ---- usage_records ----------------------------------------------------
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voice_id", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text_char_count", sa.Integer(), nullable=False),
        sa.Column("sentence_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("ttfb_ms", sa.Integer()),
        sa.Column("rtf", sa.Float()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("worker_id", sa.Text()),
        sa.Column("model_version", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                name="fk_usage_records_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"],
                                name="fk_usage_records_api_key_id_api_keys"),
        sa.UniqueConstraint("request_id", name="uq_usage_records_request_id"),
        sa.CheckConstraint("text_char_count >= 0",
                           name="ck_usage_records_text_char_count_nonneg"),
        sa.CheckConstraint("sentence_count >= 0",
                           name="ck_usage_records_sentence_count_nonneg"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_usage_records_duration_ms_nonneg"),
        sa.CheckConstraint("elapsed_ms >= 0", name="ck_usage_records_elapsed_ms_nonneg"),
        sa.CheckConstraint("ttfb_ms IS NULL OR ttfb_ms >= 0",
                           name="ck_usage_records_ttfb_ms_nonneg"),
        sa.CheckConstraint(
            "status IN ('ok','error','timeout','partial')",
            name="ck_usage_records_status_enum"),
    )
    op.create_index("ix_usage_tenant_time", "usage_records",
                    ["tenant_id", "occurred_at"])
    op.create_index("ix_usage_key_time", "usage_records",
                    ["api_key_id", "occurred_at"])

    # ---- audit_log --------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_label", sa.Text()),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", sa.Text()),
        sa.Column("ip_addr", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                name="fk_audit_log_tenant_id_tenants"),
        sa.CheckConstraint("actor_type IN ('api_key','operator','system')",
                           name="ck_audit_log_actor_type_enum"),
        sa.CheckConstraint("result IN ('success','denied','error')",
                           name="ck_audit_log_result_enum"),
    )
    op.create_index("ix_audit_tenant_time", "audit_log",
                    ["tenant_id", "occurred_at"])
    op.create_index("ix_audit_action_time", "audit_log", ["action", "occurred_at"])

    # Audit immutability — application code never UPDATEs/DELETEs anyway.
    # Public role lockdown is set up out-of-band in db init scripts.
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC")

    # ---- job_idempotency --------------------------------------------------
    op.create_table(
        "job_idempotency",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_uri", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                name="fk_job_idempotency_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"],
                                name="fk_job_idempotency_api_key_id_api_keys"),
        sa.CheckConstraint(
            "status IN ('processing','complete','failed')",
            name="ck_job_idempotency_status_enum"),
    )
    op.create_index("ix_idempotency_expires", "job_idempotency", ["expires_at"])


def downgrade() -> None:
    # Forward-only migration policy. See docs/architecture/data-model.md §3.
    raise NotImplementedError("forward-only migration policy")
