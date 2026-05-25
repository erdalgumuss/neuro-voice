"""Reproducibility audit trail (MLOps PR #1).

Adds `engine_inputs` JSONB column to `usage_records` — per-request
snapshot of every input the engine actually saw:

    {
      "model_id":            "openbmb/VoxCPM2",
      "hf_revision":         "<commit_sha>",
      "preset_id":           "nqai-voxcpm2-tr-hd",
      "cfg_value":           2.0,
      "inference_timesteps": 16,
      "seed":                42 | None,
      "voice_settings":      {...resolved (defaults + per-req override)...},
      "pronunciation_dict_size": 0,
      "previous_text_len":   0,
      "next_text_len":       0,
      "reference_sha256":    "<hex>"
    }

Without this column we cannot trace "the output sounded different
today vs yesterday" to "the upstream model changed" or "the engine
knobs changed" or "the reference audio drifted." That investigation
is the foundation of every quality-regression postmortem.

Nullable so pre-PR-1 rows stay valid. No CHECK constraints — the
shape is enforced at write time by the worker; readers must tolerate
missing keys (forward-compat).

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column(
            "engine_inputs",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
