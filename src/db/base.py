"""SQLAlchemy declarative base. All ORM models import this single Base."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# BigInteger PK that autoincrements correctly on SQLite (INTEGER alias) but
# stays BIGSERIAL on Postgres.
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite defaults to FK constraints OFF — we want CASCADE deletes to
    actually cascade in test fixtures. No-op on Postgres."""
    is_sqlite = dbapi_connection.__class__.__module__.startswith(
        ("sqlite3", "aiosqlite")
    )
    if is_sqlite:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
