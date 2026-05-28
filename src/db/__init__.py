"""NeuroVoice — database layer (PostgreSQL 16 + SQLAlchemy 2 async).

Public surface:
    Base                 declarative base for all ORM models
    AsyncSessionLocal    sessionmaker (call as factory)
    get_engine()         configured engine, cached per-process
    get_session()        FastAPI dependency, yields one tx-scoped session
    init_models_for_tests()  fast in-memory SQLite bootstrap for unit tests
"""

from . import models  # noqa: F401 — registers ORM classes with Base.metadata
from .base import Base
from .session import (
    AsyncSessionLocal,
    get_engine,
    get_session,
    init_models_for_tests,
)

__all__ = [
    "Base",
    "AsyncSessionLocal",
    "get_engine",
    "get_session",
    "init_models_for_tests",
    "models",
]
