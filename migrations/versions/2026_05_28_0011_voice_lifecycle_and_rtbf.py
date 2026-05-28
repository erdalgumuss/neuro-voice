"""voice lifecycle + right-to-be-forgotten (ADR-11)

Adds the runtime substrate for voice lifecycle state transitions and
KVKK/GDPR Article 17 (data subject erasure) requests:

* `voices.frozen_at`         — reversible synthesis stop; data preserved
* `voices.frozen_reason`     — free-text audit string
* `voices.purge_after_at`    — purge eligibility timestamp (typically
                              frozen_at + 30 days per GDPR practice)
* `voices.purged_at`         — set when reference audio + adapter
                              weights have been removed and PII fields
                              scrubbed. Tombstone state; row is kept
                              for usage_records / audit_log referential
                              integrity, but holds no recoverable data.

Lifecycle state is **derived** in app code from these four columns
plus `deleted_at`; no stored enum / GENERATED column (kept portable
across SQLite tests via TypeDecorator).

Partial indexes back operator inbox queries + the future cron worker
scan (cron itself is out of scope per ADR-11).

* `data_deletion_requests` — audit trail for tenant-initiated KVKK /
                              GDPR erasure requests. Independent of
                              `voices.deleted_at` (the soft-delete
                              flag): a deletion REQUEST drives freeze
                              + purge_after_at on each named voice;
                              the request row is the operator-visible
                              ticket.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


DELETION_REQUEST_STATUS_VALUES = (
    "pending", "in-progress", "completed", "rejected",
)


def upgrade() -> None:
    # ---- 1. voices: 4 lifecycle columns
    op.add_column("voices", sa.Column("frozen_at", sa.DateTime(timezone=True)))
    op.add_column("voices", sa.Column("frozen_reason", sa.Text()))
    op.add_column("voices", sa.Column("purge_after_at", sa.DateTime(timezone=True)))
    op.add_column("voices", sa.Column("purged_at", sa.DateTime(timezone=True)))

    # Partial indexes — frozen voices (operator inbox), purge-pending
    # voices (future cron worker scan). Both predicates exclude
    # purged_at IS NOT NULL so tombstones don't bloat the index.
    op.create_index(
        "ix_voices_frozen",
        "voices",
        ["frozen_at"],
        postgresql_where=sa.text("frozen_at IS NOT NULL AND purged_at IS NULL"),
    )
    op.create_index(
        "ix_voices_purge_pending",
        "voices",
        ["purge_after_at"],
        postgresql_where=sa.text(
            "purge_after_at IS NOT NULL AND purged_at IS NULL"
        ),
    )

    # Sanity CHECK constraints — purge_after_at must follow frozen_at;
    # purged_at must follow purge_after_at if purge_after_at was set.
    # Either timestamp can independently be NULL; the order only matters
    # when both ends are populated.
    op.create_check_constraint(
        "ck_voices_purge_after_frozen",
        "voices",
        "purge_after_at IS NULL OR frozen_at IS NULL "
        "OR purge_after_at >= frozen_at",
    )
    op.create_check_constraint(
        "ck_voices_purged_after_purge_after",
        "voices",
        "purged_at IS NULL OR purge_after_at IS NULL "
        "OR purged_at >= purge_after_at",
    )

    # ---- 2. data_deletion_requests
    op.create_table(
        "data_deletion_requests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # ON DELETE RESTRICT — a tenant with deletion requests on file
        # cannot be hard-deleted; status='deleted' soft on tenants
        # remains the canonical lifecycle.
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        # Empty array = "delete every voice this tenant owns". Slugs
        # carried as TEXT[] (not FK) so a request stays meaningful even
        # after voices are deleted/purged.
        sa.Column(
            "voice_slugs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("jurisdiction", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        # Polymorphic actor — api_keys.id (tenant-side) on creation;
        # operators.id on operator-driven processing. No FK; see ADR-10
        # voice_consent_records.recorded_by_actor_id for the same
        # pattern.
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reason", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completion_notes", sa.Text()),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT",
            name="fk_data_deletion_requests_tenant_id_tenants",
        ),
        sa.CheckConstraint(
            f"status IN ({','.join(repr(v) for v in DELETION_REQUEST_STATUS_VALUES)})",
            name="ck_data_deletion_requests_status_enum",
        ),
        # jurisdiction: NULL or ISO 3166-1 alpha-2 or "EU"
        sa.CheckConstraint(
            "jurisdiction IS NULL OR jurisdiction = 'EU' "
            r"OR jurisdiction ~ '^[A-Z]{2}$'",
            name="ck_data_deletion_requests_jurisdiction_format",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_data_deletion_requests_completed_after_requested",
        ),
        # status=completed must have completed_at filled; pending/in-
        # progress/rejected must have completed_at NULL (rejected uses
        # completion_notes for the rejection reason but stays NULL on
        # completed_at — the request was never completed).
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_data_deletion_requests_completed_at_consistency",
        ),
    )
    op.create_index(
        "ix_data_deletion_pending",
        "data_deletion_requests",
        ["status", "requested_at"],
        postgresql_where=sa.text("status IN ('pending','in-progress')"),
    )
    op.create_index(
        "ix_data_deletion_tenant",
        "data_deletion_requests",
        ["tenant_id", "requested_at"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
