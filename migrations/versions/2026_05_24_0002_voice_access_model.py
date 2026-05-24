"""voice access model — owner_tenant_id rename + visibility + voice_access + app_label

Refactor R (decision log 2026-05-24, "Tenant = account/workspace + voice access policy"):

  * voices.tenant_id        → owner_tenant_id (semantic rename — same column type/FK)
  * voices.visibility       new column: 'private'|'shared'|'public', default 'private'
  * voice_access            new table: per-tenant explicit grants for shared voices
  * usage_records.app_label new column: product attribution from X-NQAI-App header

The (owner_tenant_id, voice_id) UNIQUE constraint stays — slug uniqueness is
per-owner. Cross-tenant visibility is mediated by voice_access rows, not by
making voice_id globally unique.

Forward-only (data-model.md migration policy).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- voices: rename tenant_id → owner_tenant_id ----------------------
    # Postgres rename is metadata-only; the (tenant_id, voice_id) UNIQUE
    # constraint name auto-updates because of our naming_convention, but we
    # rename explicitly so the new name `uq_voices_owner_tenant_id` is stable
    # for future migrations.
    op.alter_column("voices", "tenant_id", new_column_name="owner_tenant_id")

    # Drop + recreate the FK so its name reflects the new column.
    op.drop_constraint("fk_voices_tenant_id_tenants", "voices", type_="foreignkey")
    op.create_foreign_key(
        "fk_voices_owner_tenant_id_tenants",
        "voices", "tenants",
        ["owner_tenant_id"], ["id"],
        ondelete="CASCADE",
    )

    # Drop + recreate the per-owner UNIQUE so its name is stable too.
    op.drop_constraint("uq_voices_tenant_id", "voices", type_="unique")
    op.create_unique_constraint(
        "uq_voices_owner_tenant_id_voice_id",
        "voices",
        ["owner_tenant_id", "voice_id"],
    )

    # Replace the catalog index (tenant_id → owner_tenant_id).
    op.drop_index("ix_voices_tenant_id", table_name="voices")
    op.create_index(
        "ix_voices_owner_tenant_id_active",
        "voices",
        ["owner_tenant_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ---- voices.visibility -----------------------------------------------
    op.add_column(
        "voices",
        sa.Column(
            "visibility", sa.Text(),
            nullable=False, server_default="private",
        ),
    )
    op.create_check_constraint(
        "ck_voices_visibility_enum",
        "voices",
        "visibility IN ('private','shared','public')",
    )
    # Hot path for the public catalog listing (Faz B+ endpoint).
    op.create_index(
        "ix_voices_public",
        "voices",
        ["visibility"],
        postgresql_where=sa.text("visibility = 'public' AND deleted_at IS NULL"),
    )

    # ---- voice_access ----------------------------------------------------
    # Explicit grant table: when owner_tenant_id wants tenant X to use a
    # voice with visibility='shared', they insert a row here. Visibility
    # 'public' bypasses this table (every active tenant sees it).
    op.create_table(
        "voice_access",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "permission", sa.Text(),
            nullable=False, server_default="use",
        ),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            ondelete="CASCADE",
            name="fk_voice_access_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["voice_id"], ["voices.id"],
            ondelete="CASCADE",
            name="fk_voice_access_voice_id_voices",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["operators.id"],
            ondelete="SET NULL",
            name="fk_voice_access_granted_by_operators",
        ),
        sa.UniqueConstraint(
            "tenant_id", "voice_id",
            name="uq_voice_access_tenant_id_voice_id",
        ),
        sa.CheckConstraint(
            "permission IN ('use','read')",
            name="ck_voice_access_permission_enum",
        ),
    )
    op.create_index("ix_voice_access_tenant_id", "voice_access", ["tenant_id"])
    op.create_index("ix_voice_access_voice_id", "voice_access", ["voice_id"])

    # ---- usage_records.app_label -----------------------------------------
    # Product attribution — X-NQAI-App header → recorded here for billing
    # rollup and per-app analytics. NULL is valid (header is optional).
    op.add_column(
        "usage_records",
        sa.Column("app_label", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_usage_records_tenant_id_app_label_created_at",
        "usage_records",
        ["tenant_id", "app_label", "created_at"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    # Forward-only migration policy (data-model.md §3, decision log).
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
