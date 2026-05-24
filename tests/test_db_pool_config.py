"""Engine factory behaviour around pgBouncer + pool sizing (Faz C v1 item 4).

These tests don't spin up a real Postgres — they exercise the pure
config-derivation logic (`_pool_kwargs_for`) so a refactor can't
silently regress the pgBouncer contract (statement cache off when
fronted by pgBouncer in transaction mode).
"""

from __future__ import annotations

import importlib
import sys


def _reload_session(monkeypatch=None, env: dict | None = None):
    """Reload db.session with a clean env so module-level state +
    cached engines from earlier tests don't bleed in. Returns the
    fresh module."""
    if env is not None:
        for k, v in env.items():
            if v is None:
                # Pop if present so the default branch runs.
                # monkeypatch.delenv would raise on missing; use os direct.
                import os
                os.environ.pop(k, None)
            elif monkeypatch is not None:
                monkeypatch.setenv(k, v)
    for mod in list(sys.modules):
        if mod.startswith("db."):
            del sys.modules[mod]
    return importlib.import_module("db.session")


def test_pool_kwargs_direct_postgres_default(monkeypatch) -> None:
    """Without NQAI_DB_PGBOUNCER, pool is 10+10 and asyncpg statement
    cache is left at the default (no connect_args injection)."""
    for var in (
        "NQAI_DB_PGBOUNCER",
        "NQAI_DB_POOL_SIZE",
        "NQAI_DB_MAX_OVERFLOW",
    ):
        monkeypatch.delenv(var, raising=False)
    session = _reload_session(monkeypatch)
    kwargs = session._pool_kwargs_for(
        "postgresql+asyncpg://nqai:nqai@localhost:5432/nqai_voice"
    )
    assert kwargs["pool_size"] == 10
    assert kwargs["max_overflow"] == 10
    assert "connect_args" not in kwargs  # asyncpg default cache stays on


def test_pool_kwargs_pgbouncer_mode_disables_statement_cache(monkeypatch) -> None:
    """With NQAI_DB_PGBOUNCER=true the pool shrinks and asyncpg's
    prepared-statement cache is forced to zero. Both are required when
    fronting Postgres with pgBouncer in transaction mode (per
    docs/runbooks/database-pool.md)."""
    monkeypatch.setenv("NQAI_DB_PGBOUNCER", "true")
    monkeypatch.delenv("NQAI_DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("NQAI_DB_MAX_OVERFLOW", raising=False)
    session = _reload_session(monkeypatch)
    kwargs = session._pool_kwargs_for(
        "postgresql+asyncpg://nqai:nqai@pgbouncer:6432/nqai_voice"
    )
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 5
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    assert kwargs["connect_args"]["prepared_statement_cache_size"] == 0


def test_pool_kwargs_env_override_wins(monkeypatch) -> None:
    """Operators can override the defaults regardless of mode. Useful
    in load tests / heavy-write deployments."""
    monkeypatch.setenv("NQAI_DB_PGBOUNCER", "true")
    monkeypatch.setenv("NQAI_DB_POOL_SIZE", "15")
    monkeypatch.setenv("NQAI_DB_MAX_OVERFLOW", "30")
    monkeypatch.setenv("NQAI_DB_POOL_TIMEOUT_S", "60")
    monkeypatch.setenv("NQAI_DB_POOL_RECYCLE_S", "900")
    session = _reload_session(monkeypatch)
    kwargs = session._pool_kwargs_for(
        "postgresql+asyncpg://nqai:nqai@pgbouncer:6432/nqai_voice"
    )
    assert kwargs["pool_size"] == 15
    assert kwargs["max_overflow"] == 30
    assert kwargs["pool_timeout"] == 60
    assert kwargs["pool_recycle"] == 900


def test_pool_kwargs_pgbouncer_only_injects_connect_args_for_asyncpg(monkeypatch) -> None:
    """psycopg / psycopg2 drivers don't have an asyncpg-style statement
    cache. The pgBouncer-mode connect_args MUST NOT leak into a non-
    asyncpg URL — psycopg would reject the kwargs."""
    monkeypatch.setenv("NQAI_DB_PGBOUNCER", "true")
    session = _reload_session(monkeypatch)
    kwargs = session._pool_kwargs_for(
        "postgresql+psycopg://nqai:nqai@pgbouncer:6432/nqai_voice"
    )
    assert "connect_args" not in kwargs


def test_env_bool_parses_common_truthy_values(monkeypatch) -> None:
    session = _reload_session(monkeypatch)
    for truthy in ("true", "True", "1", "yes", "ON"):
        monkeypatch.setenv("NQAI_TEST_BOOL", truthy)
        assert session._env_bool("NQAI_TEST_BOOL") is True
    for falsy in ("false", "0", "no", "off", "anything-else"):
        monkeypatch.setenv("NQAI_TEST_BOOL", falsy)
        assert session._env_bool("NQAI_TEST_BOOL") is False
    monkeypatch.delenv("NQAI_TEST_BOOL", raising=False)
    assert session._env_bool("NQAI_TEST_BOOL", default=True) is True
    assert session._env_bool("NQAI_TEST_BOOL", default=False) is False
