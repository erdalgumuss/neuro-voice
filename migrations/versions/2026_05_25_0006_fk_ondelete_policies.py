"""Explicit ondelete policies on usage_records + audit_log FKs.

Audit L5 H2-H3 (2026-05-25): the FK definitions in 0001 inherited the
Postgres default (no explicit ondelete clause = RESTRICT). That works
for `usage_records` (which IS what we want — financial history must
not vanish when a tenant is removed) but the schema didn't say so;
operators only learned it by hitting a foreign-key violation.

For `audit_log.tenant_id` RESTRICT is the WRONG default: audit rows
are append-only forensic history; they MUST survive tenant deletion.
SET NULL is the right ondelete policy there — the row stays, the
tenant pointer goes null.

Forward-only fix: drop + recreate each FK with the explicit clause.
Names match the conventional auto-generated form Alembic produces so
a subsequent `alembic upgrade head` against a partially-rolled DB is
predictable.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # usage_records.tenant_id → tenants.id, RESTRICT
    op.drop_constraint(
        "fk_usage_records_tenant_id_tenants",
        "usage_records",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_usage_records_tenant_id_tenants",
        "usage_records", "tenants",
        ["tenant_id"], ["id"],
        ondelete="RESTRICT",
    )

    # usage_records.api_key_id → api_keys.id, RESTRICT
    op.drop_constraint(
        "fk_usage_records_api_key_id_api_keys",
        "usage_records",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_usage_records_api_key_id_api_keys",
        "usage_records", "api_keys",
        ["api_key_id"], ["id"],
        ondelete="RESTRICT",
    )

    # audit_log.tenant_id → tenants.id, SET NULL
    op.drop_constraint(
        "fk_audit_log_tenant_id_tenants",
        "audit_log",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_audit_log_tenant_id_tenants",
        "audit_log", "tenants",
        ["tenant_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
