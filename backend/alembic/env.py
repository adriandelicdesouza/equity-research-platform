"""Alembic environment. Migrations always run as the database OWNER role.

The target database can be overridden with ``-x database=<name>`` (used by tests).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from erp_backend.config import get_settings
from erp_backend.db import database_url
from erp_backend.models import Base
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_online() -> None:
    settings = get_settings()
    database = context.get_x_argument(as_dictionary=True).get("database")
    engine = create_engine(
        database_url(settings, owner=True, database=database), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            include_schemas=True,
            version_table_schema="public",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported.")
run_migrations_online()
