"""Voice catalog enrichment (Faz B.5 Dalga 2.4).

Adds four columns to `voices` that bring the schema in line with
ElevenLabs / MiniMax voice metadata:

* `description` (TEXT, nullable)
    Free-form voice description shown in the dashboard / SDK list
    endpoint. Vendor parity — ElevenLabs voices have descriptions.

* `labels` (TEXT[], nullable)
    Searchable tags (mood, accent, age, use_case). ElevenLabs has
    `labels: {accent, age, gender, use_case}` map; we store the
    flattened list and let clients structure them.

* `preview_url` (TEXT, nullable)
    Short demo URL (typically a 5-10s WAV/MP3 hosted on R2 with a
    presigned GET). Lets a UI play a sample without consuming TTS
    credits. Populated by the enroll path when it stores the
    reference audio, or by an operator via PATCH.

* `voice_settings_defaults` (JSONB, nullable)
    Per-voice vendor-shape defaults (stability, similarity_boost,
    speed, style, ...). Layered with request-level `voice_settings`
    at synthesis time: defaults first, request overrides per-field.

All columns nullable so existing rows stay valid. No CHECK
constraints needed — content is operator/client-supplied free text /
structured JSON.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voices",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "voices",
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "voices",
        sa.Column("preview_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "voices",
        sa.Column(
            "voice_settings_defaults",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
