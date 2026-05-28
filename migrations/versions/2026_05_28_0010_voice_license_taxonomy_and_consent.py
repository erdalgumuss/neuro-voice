"""voice license taxonomy + consent records + talent contracts (ADR-10)

Brings voice ownership / license / consent into a DB-enforced shape:

* `voices.license` (freeform Text) → `voices.license_kind` (Text + CHECK)
  on a closed list: example, synthetic, user-owned, talent-contract,
  public-figure, partner-licensed. CHECK constraint (not Postgres ENUM)
  so future taxonomy growth is a single ALTER, not a TYPE migration.

* `voices.license_ref` (Text, nullable) — polymorphic reference. Holds
  a `talent_contracts.id` UUID for license_kind='talent-contract', or an
  external URL / partner agreement string for other kinds. NO FK
  enforcement; app-layer integrity. Esnek mimari (ADR-10 / yönetici
  kararı 2026-05-28).

* `voices.source` CHECK constraint refreshed. Drop legacy values
  (`elevenlabs`, `voice-talent`) carried over from the vendor-parity era;
  add `talent-recorded`, `synthetic-from-prompt`, `partner-import`.
  Rename `placeholder` → `bootstrap` and `user-enroll` → `tenant-enroll`.

* New table `talent_contracts` — operator-managed (NOT tenant-scoped);
  one row per signed talent agreement.

* New table `voice_consent_records` — 1:N voice → records. A voice
  accumulates consent records over time (initial tenant-asserted →
  later signed-contract upgrade → eventual revocation). Application
  logic reads the latest `revoked_at IS NULL` row to gate synthesis;
  this migration only lays the table.

v0.x has no production voice rows (seed sample only), so backfill is
defensive: any unrecognised legacy `license` string folds into
`user-owned`, any unrecognised legacy `source` folds into `bootstrap`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


# Closed-list vocabularies (CHECK constraint inputs). Kept here as
# module-level tuples so the model layer can import the canonical list
# in a follow-up without round-tripping through Alembic.
LICENSE_KIND_VALUES = (
    "example",
    "synthetic",
    "user-owned",
    "talent-contract",
    "public-figure",
    "partner-licensed",
)
SOURCE_VALUES = (
    "bootstrap",
    "tenant-enroll",
    "talent-recorded",
    "synthetic-from-prompt",
    "partner-import",
)
CONSENT_KIND_VALUES = (
    "tenant-asserted",
    "recorded-statement",
    "signed-contract",
    "estate-permission",
)
RECORDED_BY_KIND_VALUES = ("tenant", "operator")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ",".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # ---- 1. voices.source — drop legacy CHECK, normalise rows, rebuild
    op.drop_constraint("ck_voices_source_enum", "voices", type_="check")

    # Defensive backfill — v0.x has no prod rows but seed/test fixtures
    # may carry the old vocabulary.
    op.execute("UPDATE voices SET source = 'bootstrap' WHERE source = 'placeholder'")
    op.execute("UPDATE voices SET source = 'tenant-enroll' WHERE source = 'user-enroll'")
    op.execute("UPDATE voices SET source = 'talent-recorded' WHERE source = 'voice-talent'")
    op.execute("UPDATE voices SET source = 'bootstrap' WHERE source = 'elevenlabs'")
    # Anything else (shouldn't exist) folds into bootstrap so the new
    # CHECK constraint can be added without a row-by-row scan failing.
    op.execute(
        "UPDATE voices SET source = 'bootstrap' "
        f"WHERE source NOT IN ({','.join(repr(v) for v in SOURCE_VALUES)})"
    )

    op.create_check_constraint(
        "ck_voices_source_enum",
        "voices",
        _in_list("source", SOURCE_VALUES),
    )

    # ---- 2. voices.license → voices.license_kind + CHECK + license_ref
    op.alter_column("voices", "license", new_column_name="license_kind")

    # Map legacy freeform license strings onto the closed list. Known
    # seed/test values fold to `example`; anything else lands at
    # `user-owned` (most defensive — preserves the row, signals to an
    # operator that the row needs a manual license review).
    op.execute(
        "UPDATE voices SET license_kind = 'example' "
        "WHERE license_kind IN ('internal-bridge','internal-placeholder','example')"
    )
    op.execute("UPDATE voices SET license_kind = 'synthetic' WHERE license_kind = 'synthetic'")
    op.execute(
        "UPDATE voices SET license_kind = 'user-owned' "
        f"WHERE license_kind NOT IN ({','.join(repr(v) for v in LICENSE_KIND_VALUES)})"
    )

    op.create_check_constraint(
        "ck_voices_license_kind_enum",
        "voices",
        _in_list("license_kind", LICENSE_KIND_VALUES),
    )

    op.add_column(
        "voices",
        sa.Column("license_ref", sa.Text(), nullable=True),
    )

    # ---- 3. talent_contracts (operator-managed, NOT tenant-scoped)
    op.create_table(
        "talent_contracts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("talent_full_name", sa.Text(), nullable=False),
        sa.Column("contract_pdf_uri", sa.Text(), nullable=False),
        sa.Column("contract_pdf_sha256", sa.Text(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("jurisdiction", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_by_operator_id",
            postgresql.UUID(as_uuid=True),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id"], ["operators.id"],
            ondelete="SET NULL",
            name="fk_talent_contracts_created_by_operator_id_operators",
        ),
        sa.CheckConstraint(
            "char_length(talent_full_name) BETWEEN 1 AND 200",
            name="ck_talent_contracts_talent_full_name_length",
        ),
        sa.CheckConstraint(
            r"contract_pdf_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_talent_contracts_sha256_format",
        ),
        # jurisdiction: NULL or ISO 3166-1 alpha-2 or the literal 'EU'
        sa.CheckConstraint(
            "jurisdiction IS NULL OR jurisdiction = 'EU' "
            r"OR jurisdiction ~ '^[A-Z]{2}$'",
            name="ck_talent_contracts_jurisdiction_format",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > signed_at",
            name="ck_talent_contracts_expires_after_signed",
        ),
    )
    op.create_index(
        "ix_talent_contracts_active",
        "talent_contracts",
        ["signed_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ---- 4. voice_consent_records (1:N voice → records)
    op.create_table(
        "voice_consent_records",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("voice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_kind", sa.Text(), nullable=False),
        sa.Column("evidence_uri", sa.Text()),
        sa.Column("evidence_sha256", sa.Text()),
        sa.Column("evidence_notes", sa.Text()),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("recorded_by_kind", sa.Text(), nullable=False),
        # actor id is polymorphic: api_keys.id (tenant path) or
        # operators.id (operator path). No FK so a single column can
        # carry either without violating referential integrity.
        sa.Column("recorded_by_actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.Text()),
        sa.ForeignKeyConstraint(
            ["voice_id"], ["voices.id"], ondelete="CASCADE",
            name="fk_voice_consent_records_voice_id_voices",
        ),
        sa.CheckConstraint(
            _in_list("consent_kind", CONSENT_KIND_VALUES),
            name="ck_voice_consent_records_consent_kind_enum",
        ),
        sa.CheckConstraint(
            _in_list("recorded_by_kind", RECORDED_BY_KIND_VALUES),
            name="ck_voice_consent_records_recorded_by_kind_enum",
        ),
        # evidence presence is contingent on the consent kind:
        #   tenant-asserted MUST have NULL evidence (no artifact in our
        #   system; the tenant accepts liability via the API call)
        #   every other kind MUST have a non-NULL evidence_uri pointing
        #   to the artifact in R2.
        sa.CheckConstraint(
            "(consent_kind = 'tenant-asserted' AND evidence_uri IS NULL) "
            "OR (consent_kind <> 'tenant-asserted' AND evidence_uri IS NOT NULL)",
            name="ck_voice_consent_records_evidence_presence",
        ),
        sa.CheckConstraint(
            "evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_voice_consent_records_evidence_sha256_format",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= recorded_at",
            name="ck_voice_consent_records_revoked_after_recorded",
        ),
    )
    # Latest-consent lookup index — the most common read pattern is
    # "give me the active consent for voice X right now". Partial index
    # on `revoked_at IS NULL` keeps the active subset hot.
    op.create_index(
        "ix_voice_consent_records_active",
        "voice_consent_records",
        ["voice_id", sa.text("recorded_at DESC")],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "downgrade not supported; write a forward migration"
    )
