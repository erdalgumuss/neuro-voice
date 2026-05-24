"""SQLAlchemy declarative base. All ORM models import this single Base."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# BigInteger PK that autoincrements correctly on SQLite (INTEGER alias) but
# stays BIGSERIAL on Postgres. SQLite needs the rowid INTEGER alias to
# trigger AUTOINCREMENT; Postgres BIGSERIAL is the right column type at scale.
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")

# SQLite FK enforcement (PRAGMA foreign_keys=ON) is bound on the test engine
# in db.session.init_models_for_tests() — we intentionally do NOT register a
# module-level Engine event so this package never mutates global SQLAlchemy
# state for unrelated Engines that might live in the same process.

# Deterministic naming convention — Alembic generates stable constraint names,
# `alembic --autogenerate` produces clean diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    """`created_at` always set, `updated_at` auto-bumped on mutation."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
