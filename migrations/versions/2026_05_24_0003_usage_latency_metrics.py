"""Add usage latency waterfall columns.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_records", sa.Column("queue_wait_ms", sa.Integer(), nullable=True))
    op.add_column("usage_records", sa.Column("inference_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "queue_wait_ms_nonneg",
        "usage_records",
        "queue_wait_ms IS NULL OR queue_wait_ms >= 0",
    )
    op.create_check_constraint(
        "inference_ms_nonneg",
        "usage_records",
        "inference_ms IS NULL OR inference_ms >= 0",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
