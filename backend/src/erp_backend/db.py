"""Database engine and session management (SQLAlchemy 2, psycopg 3, sync)."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from erp_backend.config import Settings, get_settings


def database_url(settings: Settings, *, owner: bool = False, database: str | None = None) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.db_owner_user if owner else settings.db_app_user,
        password=settings.owner_password() if owner else settings.app_password(),
        host=settings.db_host,
        port=settings.db_port,
        database=database or settings.db_name,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        database_url(settings),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request; caller commits explicitly."""
    with _session_factory()() as session:
        yield session
