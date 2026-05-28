"""watermark generation runtime substrate (ADR-13)

Wires the AudioSeal-based watermark into the persistence layer:

* `watermark_keys` — operator-managed table holding the 16-bit
  AudioSeal payload that gets embedded into synthesized audio. One
  row per allocation; rows are not deleted (forensics history) —
  retirement is a status flip (`retired_at`).

* `voices.watermark_key_id` — was TEXT (per ADR-7's voice manifest
  schema v2 forward-shape). Promoted to UUID FK on `watermark_keys`.
  v0.x has no production rows, so DROP+ADD is the cleanest type
  conversion (no USING-cast contortions, no SQLite portability
  worries).

* `voices.watermark_enabled` — boolean toggle, default TRUE. Voice
  with `watermark_enabled=true` AND `watermark_key_id IS NOT NULL`
  gets its synth stream watermarked per chunk. License-kind-bound
  invariants (talent-contract / public-figure / partner-licensed
  must stay watermark_enabled=true) are enforced in the app layer,
  not the DB — license_kind isn't a foreign key relation, and
  cross-column CHECK on a TEXT-enum column would brittlely fight
  ADR-10's CHECK list.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 1. watermark_keys table (depended on by the voices FK below)
    op.create_table(
        "watermark_keys",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # 16-bit AudioSeal payload (0..65535). Stored as INTEGER for
        # SQL ergonomics; the worker converts to/from the bit pattern
        # AudioSeal expects.
        sa.Column("message_bits", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "allocated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("retired_reason", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_by_operator_id", postgresql.UUID(as_uuid=True),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id"], ["operators.id"],
            ondelete="SET NULL",
            name="fk_watermark_keys_created_by_operator_id_operators",
        ),
        sa.CheckConstraint(
            "message_bits BETWEEN 0 AND 65535",
            name="ck_watermark_keys_message_bits_range",
        ),
        sa.CheckConstraint(
            "char_length(label) BETWEEN 1 AND 200",
            name="ck_watermark_keys_label_length",
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR retired_at >= allocated_at",
            name="ck_watermark_keys_retired_after_allocated",
        ),
    )
    # 16-bit slot uniqueness on ACTIVE keys only. Retired keys can
    # coexist with the same bit pattern as a new active key — old
    # forensics queries still need to find the historical row.
    op.create_index(
        "ix_watermark_keys_message_bits_active",
        "watermark_keys",
        ["message_bits"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_index(
        "ix_watermark_keys_active_allocated",
        "watermark_keys",
        ["allocated_at"],
        postgresql_where=sa.text("retired_at IS NULL"),
    )

    # ---- 2. voices.watermark_key_id: TEXT → UUID FK
    # ADR-7 left this column as Text (forward-shape, never populated
    # in v0.x). Drop + re-add as UUID with FK to watermark_keys.
    op.drop_column("voices", "watermark_key_id")
    op.add_column(
        "voices",
        sa.Column(
            "watermark_key_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_voices_watermark_key_id_watermark_keys",
        "voices", "watermark_keys",
        ["watermark_key_id"], ["id"],
        ondelete="SET NULL",
    )

    # ---- 3. voices.watermark_enabled
    # Default TRUE so existing rows (seed sample) come up watermark-
    # opted-in. License-kind-bound voices (talent-contract /
    # public-figure / partner-licensed) cannot toggle this off via
    # the admin endpoint — enforced app-layer; the DB column is a
    # simple boolean.
    op.add_column(
        "voices",
        sa.Column(
            "watermark_enabled", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
