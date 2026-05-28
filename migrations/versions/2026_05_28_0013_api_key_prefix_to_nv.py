"""api_key prefix regex alignment with ADR-4 (nv_* prefix)

The initial migration 0001 created `api_keys.prefix` with a CHECK
constraint enforcing `^nqai_(prod|staging|dev)_[a-zA-Z0-9]{14}$`.
ADR-4 (2026-05-28) renamed the auth surface to use the `nv_` prefix
to match the rest of the brand (`X-NV-*` headers, `NEUROVOICE_*`
env vars, `nv_admin_*` cookies). The API key generator at
`src/server/security/api_keys.py:57` produces `nv_<env>_*` keys, so
any attempt to create an API key against the production schema fails
the CHECK constraint immediately — the auth path is broken
end-to-end despite ADR-4 being marked "kabul edildi".

This migration:
  1. Drops the legacy `^nqai_…` CHECK constraint.
  2. Adds a new CHECK enforcing `^nv_(prod|staging|dev)_[a-zA-Z0-9]{14}$`.
  3. Rewrites any pre-existing `nqai_*` prefixes to `nv_*`. v0.x has
     no production rows, so the UPDATE is defensive — a fresh DB
     applies migrations in order and never sees the old prefix; a
     dev DB that was bootstrapped against migration 0001 will have
     its prefixes rewritten to match the new constraint.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_api_keys_prefix_format", "api_keys", type_="check",
    )

    # Defensive rewrite — v0.x has no prod rows; any seed/test that
    # somehow landed with nqai_* gets moved into the new shape so the
    # subsequent CHECK constraint can be added without scanning bad
    # rows.
    op.execute(
        r"UPDATE api_keys "
        r"SET prefix = regexp_replace(prefix, '^nqai_', 'nv_') "
        r"WHERE prefix ~ '^nqai_(prod|staging|dev)_'"
    )

    op.create_check_constraint(
        "ck_api_keys_prefix_format",
        "api_keys",
        r"prefix ~ '^nv_(prod|staging|dev)_[a-zA-Z0-9]{14}$'",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
