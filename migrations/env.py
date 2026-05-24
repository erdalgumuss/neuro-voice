"""Alembic environment — uses our SQLAlchemy Base + async engine."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from db import (
    Base,
    models,  # noqa: F401 — register models on Base.metadata
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject env-driven DSN; default kept distinct from prod to fail-loud.
db_url = os.environ.get(
    "NQAI_DATABASE_URL",
    "postgresql+asyncpg://nqai:nqai@localhost:5432/nqai_voice",
)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    # Skip Postgres-only system tables, none of ours match anyway.
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout (used by `alembic upgrade head --sql` for reviews)."""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
