"""Async engine + sessionmaker + FastAPI dependency.

In production we use asyncpg against PostgreSQL 16. For tests we boot an
in-memory aiosqlite engine — fast (~10 ms init) and good enough for the
ORM round-trip / repository contract tests. Integration tests that need
real Postgres semantics (RLS, advisory locks, JSONB ops) use
testcontainers and call `get_engine()` directly with a temp DSN.

The connection pool is sized to `(workers × threads × 2) + 5` slack —
see scale-roadmap.md D-07.
"""

from __future__ import annotations

import os
import threading
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .base import Base

_engine: AsyncEngine | None = None
_engine_lock = threading.Lock()
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _default_database_url() -> str:
    return os.environ.get(
        "NQAI_DATABASE_URL",
        "postgresql+asyncpg://nqai:nqai@localhost:5432/nqai_voice",
    )


def get_engine(database_url: str | None = None) -> AsyncEngine:
    """Return the process-wide async engine, lazy-built."""
    global _engine, _sessionmaker
    if _engine is not None and database_url is None:
        return _engine
    with _engine_lock:
        if _engine is None or database_url is not None:
            url = database_url or _default_database_url()
            kwargs: dict = {
                "echo": os.environ.get("NQAI_DB_ECHO", "false").lower() == "true",
                "future": True,
            }
            # Sensible pool defaults — see D-07 in scale-roadmap.md
            if url.startswith("postgresql"):
                kwargs.update(
                    pool_size=int(os.environ.get("NQAI_DB_POOL_SIZE", "10")),
                    max_overflow=int(os.environ.get("NQAI_DB_MAX_OVERFLOW", "10")),
                    pool_timeout=30,
                    pool_recycle=1800,
                    pool_pre_ping=True,
                )
            _engine = create_async_engine(url, **kwargs)
            _sessionmaker = async_sessionmaker(
                _engine, class_=AsyncSession, expire_on_commit=False
            )
    return _engine


AsyncSessionLocal: async_sessionmaker[AsyncSession]
"""Module-level alias for the configured sessionmaker. Lazy."""


def _ensure_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        get_engine()  # initializes both
    assert _sessionmaker is not None
    return _sessionmaker


# Module-level callable that returns a session — usage: `async with AsyncSessionLocal() as s: ...`
def AsyncSessionLocal(**kw):  # noqa: N802 — matches conventional name
    return _ensure_sessionmaker()(**kw)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a session bound to the request's scope.

    Commit/rollback semantics: the caller (route or repo) is responsible
    for `await session.commit()` after successful writes. On any exception
    we roll back; the session is always closed.
    """
    session = _ensure_sessionmaker()()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_models_for_tests(database_url: str = "sqlite+aiosqlite:///:memory:") -> AsyncEngine:
    """Boot a fresh engine with all tables created via `Base.metadata.create_all`.

    For unit tests only — production uses Alembic migrations. On non-Postgres
    backends (SQLite for fast unit tests) we strip Postgres-specific CHECK
    constraints because they use the `~` regex operator which SQLite lacks.
    The constraints are enforced again in real deployments through Alembic.
    """
    from sqlalchemy import CheckConstraint

    global _engine, _sessionmaker
    with _engine_lock:
        _engine = create_async_engine(database_url, echo=False, future=True)
        _sessionmaker = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )

    is_pg = _engine.dialect.name == "postgresql"
    if not is_pg:
        for table in Base.metadata.tables.values():
            for c in list(table.constraints):
                if isinstance(c, CheckConstraint):
                    table.constraints.discard(c)

        # Enable FK enforcement on every aiosqlite connection — required
        # for CASCADE deletes to actually cascade in tests.
        from sqlalchemy import event as _event

        @_event.listens_for(_engine.sync_engine, "connect")
        def _enforce_sqlite_fks(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return _engine
