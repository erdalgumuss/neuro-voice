"""Add gateway_first_byte_ms to usage_records (Faz C v1 item 1).

The Faz C v0 waterfall (migration 0004) captures worker-side timing:
`first_pcm_ms` is "engine yielded first SynthChunk" and `first_audio_ms`
is "first publish_chunk XADD on the result stream". Both are
worker-clock measurements.

`gateway_first_byte_ms` is the *client-facing* TTFB: time from the
gateway receiving the HTTP request to the gateway writing the first
audio byte to the client's StreamingResponse. It includes:

  * gateway auth + validate + idempotency reserve + queue.submit
  * Redis XADD (job) + queue wait
  * worker pickup + reference resolve + first PCM + bridge to result stream
  * gateway result-stream subscribe (`consume_result_stream`)
  * yield path through StreamingResponse → ASGI → HTTP transport

This is the only column in the waterfall written by the gateway,
not the worker. The worker writes the usage row when the job
completes; the gateway then UPDATEs this column when its streaming
generator emits its first chunk. UPDATE-not-INSERT, keyed on
(tenant_id, request_id) which is the unique index on usage_records.

Only `/v1/tts/stream` writes this column. Async jobs return 202 with
no audio body (gateway-first-byte = 202 response, not meaningful for
TTFB). Sync `/v1/tts` (deprecated) drains all chunks before returning
one WAV — first byte = full audio, also not meaningful. Both paths
leave this column NULL.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column("gateway_first_byte_ms", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "gateway_first_byte_ms_nonneg",
        "usage_records",
        "gateway_first_byte_ms IS NULL OR gateway_first_byte_ms >= 0",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
