"""Long-form sentence alignment (Faz B.5 Dalga 3.2).

Adds one column to `job_idempotency`:

* `sentence_alignment` (JSONB, nullable)
    Per-sentence `[{seq, start_ms, end_ms, text}, ...]` populated by
    the worker when a long-form job completes. Lets async clients
    render subtitles + scrub-bar timestamps without WAV parsing.

Nullable so existing rows stay valid. No CHECK constraints — the
shape is enforced at write time by the worker; the gateway only
consumes already-validated JSON when returning the status response.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_idempotency",
        sa.Column(
            "sentence_alignment",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
