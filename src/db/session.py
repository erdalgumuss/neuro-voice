"""Async engine + sessionmaker + FastAPI dependency.

In production we use asyncpg against PostgreSQL 16, optionally fronted
by pgBouncer in transaction pooling mode (recommended at >50
concurrent clients per Postgres host). For tests we boot an in-memory
aiosqlite engine — fast (~10 ms init) and good enough for the ORM
round-trip / repository contract tests. Integration tests that need
real Postgres semantics (RLS, advisory locks, JSONB ops) use
testcontainers and call `get_engine()` directly with a temp DSN.

Pool sizing
=============================

Two distinct knobs:

1. **SQLAlchemy pool** (`NEUROVOICE_DB_POOL_SIZE` + `NEUROVOICE_DB_MAX_OVERFLOW`):
   how many DB connections each *process* opens.
2. **pgBouncer** (transaction pool mode): how many *server* connections
   pgBouncer multiplexes those onto.

When `NEUROVOICE_DB_PGBOUNCER=true`:
   * SQLAlchemy pool stays small (default 5+5) — pgBouncer is the real
     pool, the process-local pool is a thin handle.
   * asyncpg's prepared-statement cache is disabled (`statement_cache_
     size=0`). Prepared statements survive across asyncpg sessions but
     pgBouncer txn-mode bounces server connections between client
     transactions, so a cached statement may point at a server that
     no longer has it. Disabling the cache is the textbook fix.
   * Pre-ping stays ON so a server-side connection drop doesn't take
     out the next request.

Direct Postgres (`NEUROVOICE_DB_PGBOUNCER=false`, default):
   * Bigger SQLAlchemy pool (10+10).
   * Default asyncpg statement cache (better single-process latency).

See docs/runbooks/database-pool.md for the connection-count math.
"""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator

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
        "NEUROVOICE_DATABASE_URL",
        "postgresql+asyncpg://neurovoice:neurovoice@localhost:5432/neurovoice",
    )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"true", "1", "yes", "on"}


def _pool_kwargs_for(url: str) -> dict:
    """Build SQLAlchemy create_async_engine kwargs for a Postgres URL.

    Two paths:
    - direct Postgres: larger pool, default asyncpg statement cache.
    - pgBouncer transaction mode: small pool, asyncpg statement cache
      disabled (required because txn-mode bounces server connections
      between client transactions, breaking the prepared-statement
      cache contract).
    """
    pgbouncer = _env_bool("NEUROVOICE_DB_PGBOUNCER")
    # Defaults tuned per mode; env vars override if operator knows better.
    if pgbouncer:
        pool_size = int(os.environ.get("NEUROVOICE_DB_POOL_SIZE", "5"))
        max_overflow = int(os.environ.get("NEUROVOICE_DB_MAX_OVERFLOW", "5"))
    else:
        pool_size = int(os.environ.get("NEUROVOICE_DB_POOL_SIZE", "10"))
        max_overflow = int(os.environ.get("NEUROVOICE_DB_MAX_OVERFLOW", "10"))

    kwargs: dict = {
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_timeout": int(os.environ.get("NEUROVOICE_DB_POOL_TIMEOUT_S", "30")),
        "pool_recycle": int(os.environ.get("NEUROVOICE_DB_POOL_RECYCLE_S", "1800")),
        "pool_pre_ping": True,
    }

    if pgbouncer and "asyncpg" in url:
        kwargs["connect_args"] = {
            # asyncpg-level prepared-statement caching is incompatible
            # with pgBouncer transaction pool mode. SQLAlchemy's own
            # query compilation cache is independent and stays on.
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }
    return kwargs


def get_engine(database_url: str | None = None) -> AsyncEngine:
    """Return the process-wide async engine, lazy-built."""
    global _engine, _sessionmaker
    if _engine is not None and database_url is None:
        return _engine
    with _engine_lock:
        if _engine is None or database_url is not None:
            url = database_url or _default_database_url()
            kwargs: dict = {
                "echo": os.environ.get("NEUROVOICE_DB_ECHO", "false").lower() == "true",
                "future": True,
            }
            # Sensible pool defaults — see D-07 in scale-roadmap.md.
            if url.startswith("postgresql"):
                kwargs.update(_pool_kwargs_for(url))
            _engine = create_async_engine(url, **kwargs)
            _sessionmaker = async_sessionmaker(
                _engine, class_=AsyncSession, expire_on_commit=False
            )
    return _engine


def _ensure_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        get_engine()  # initializes both engine + sessionmaker
    assert _sessionmaker is not None
    return _sessionmaker


def AsyncSessionLocal(**kw) -> AsyncSession:  # noqa: N802 — keeps SQLAlchemy convention
    """Open a new async session from the process-wide sessionmaker.

    Usage:
        async with AsyncSessionLocal() as session:
            ...

    Prefer the FastAPI `get_session` dependency in request paths so the
    session is bound to the request's transactional scope.
    """
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
