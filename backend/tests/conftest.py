"""Backend test harness.

Uses the dev PostgreSQL (make up-dev), on a disposable database ``erp_test`` that the
harness creates, migrates and drops. Only databases whose names start with ``erp_test``
are ever created or dropped. Each test runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

REPO = Path(__file__).resolve().parents[2]
SECRETS = REPO / "secrets" / "dev"
TEST_DB = "erp_test"

os.environ.update(
    {
        "ERP_ENV": "test",
        "ERP_DB_HOST": os.environ.get("ERP_TEST_DB_HOST", "127.0.0.1"),
        "ERP_DB_PORT": os.environ.get("ERP_TEST_DB_PORT", "5433"),
        "ERP_DB_NAME": TEST_DB,
        "ERP_DB_APP_PASSWORD_FILE": str(SECRETS / "db_app_password"),
        "ERP_DB_OWNER_PASSWORD_FILE": str(SECRETS / "db_owner_password"),
        "ERP_ALLOWED_ORIGINS": "https://testserver",
        "ERP_LOGIN_MAX_ATTEMPTS": "3",
        "ERP_MAPPINGS_ROOT": str(REPO / "mappings"),
    }
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from erp_backend.api.admin import create_user_record  # noqa: E402
from erp_backend.config import Settings, get_settings  # noqa: E402
from erp_backend.db import database_url, get_db  # noqa: E402
from erp_backend.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

ORIGIN = "https://testserver"
PASSWORD = "correct-horse-battery-staple"


def _superuser_conn() -> psycopg.Connection[tuple[object, ...]]:
    s = get_settings()
    return psycopg.connect(
        host=s.db_host,
        port=s.db_port,
        dbname="postgres",
        user="postgres",
        password=(SECRETS / "pg_superuser_password").read_text().strip(),
        autocommit=True,
    )


def recreate_database(name: str) -> None:
    if not name.startswith("erp_test"):
        raise RuntimeError(f"Refusing to recreate non-test database {name!r}")
    s = get_settings()
    with _superuser_conn() as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(s.db_owner_user)
            )
        )
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        conn.execute(
            sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
                sql.Identifier(name), sql.Identifier(s.db_app_user)
            )
        )
    with psycopg.connect(
        host=s.db_host,
        port=s.db_port,
        dbname=name,
        user="postgres",
        password=(SECRETS / "pg_superuser_password").read_text().strip(),
        autocommit=True,
    ) as conn:
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def drop_database(name: str) -> None:
    if not name.startswith("erp_test"):
        raise RuntimeError(f"Refusing to drop non-test database {name!r}")
    with _superuser_conn() as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def alembic_config() -> Config:
    return Config(str(REPO / "backend" / "alembic.ini"))


def migrate(database: str, revision: str = "head", *, down: bool = False) -> None:
    cfg = alembic_config()
    cfg.cmd_opts = type("Opts", (), {"x": [f"database={database}"]})()  # type: ignore[assignment]
    (command.downgrade if down else command.upgrade)(cfg, revision)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def migrated_db(settings: Settings) -> Iterator[str]:
    recreate_database(TEST_DB)
    migrate(TEST_DB)
    yield TEST_DB
    drop_database(TEST_DB)


@pytest.fixture(scope="session")
def app_engine(migrated_db: str, settings: Settings) -> Iterator[Engine]:
    engine = create_engine(database_url(settings, database=migrated_db))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def owner_engine(migrated_db: str, settings: Settings) -> Iterator[Engine]:
    engine = create_engine(database_url(settings, owner=True, database=migrated_db))
    yield engine
    engine.dispose()


@pytest.fixture
def db(app_engine: Engine) -> Iterator[Session]:
    conn = app_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture
def client(db: Session, settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def make_user(db: Session, username: str, roles: list[str]) -> None:
    create_user_record(
        db, username=username, display_name=username.title(), password=PASSWORD, roles=roles
    )
    db.commit()


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, object]:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 200, r.text
    body: dict[str, object] = r.json()
    return body


@pytest.fixture
def wf_client(client: TestClient, db: Session) -> TestClient:
    """``client`` whose read-only data sessions use the test session too (workflow tests)."""
    from erp_backend.readonly import read_only_db

    def same_session() -> Iterator[Session]:
        yield db

    client.app.dependency_overrides[read_only_db] = same_session  # type: ignore[attr-defined]
    return client
