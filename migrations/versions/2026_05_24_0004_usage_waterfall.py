"""Add usage waterfall latency columns (Faz C step 1).

Extends the latency waterfall persisted on `usage_records` from the two
columns introduced in migration 0003 (`queue_wait_ms`, `inference_ms`)
with four additional millisecond-granularity capture points so the
Prometheus exporter can surface a full per-job latency breakdown:

  * worker_pickup_ms      — time from `payload.enqueued_at_ms` to the
                            worker `process_one_job` start (i.e. how
                            long the job sat in Redis Streams before
                            any worker began handling it)
  * reference_resolve_ms  — duration of `resolve_reference_uri` (R2 /
                            filesystem fetch + decode for the prompt
                            audio)
  * first_pcm_ms          — time from inference start to the FIRST
                            SynthChunk yielded by the engine (engine-
                            local TTFB; complements `inference_ms`)
  * first_audio_ms        — time from inference start to the FIRST
                            `publish_chunk` XADD on the result stream
                            (what the gateway can actually observe;
                            includes the bridge overhead)

All four columns are nullable + non-negative — older `usage_records`
rows stay valid, and the rows the pipeline emits during partial
failures keep NULL on the metrics that never materialised.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column("worker_pickup_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records",
        sa.Column("reference_resolve_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records",
        sa.Column("first_pcm_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records",
        sa.Column("first_audio_ms", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "worker_pickup_ms_nonneg",
        "usage_records",
        "worker_pickup_ms IS NULL OR worker_pickup_ms >= 0",
    )
    op.create_check_constraint(
        "reference_resolve_ms_nonneg",
        "usage_records",
        "reference_resolve_ms IS NULL OR reference_resolve_ms >= 0",
    )
    op.create_check_constraint(
        "first_pcm_ms_nonneg",
        "usage_records",
        "first_pcm_ms IS NULL OR first_pcm_ms >= 0",
    )
    op.create_check_constraint(
        "first_audio_ms_nonneg",
        "usage_records",
        "first_audio_ms IS NULL OR first_audio_ms >= 0",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
