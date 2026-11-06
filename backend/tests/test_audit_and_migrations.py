import pytest
from erp_backend.config import Settings
from erp_backend.db import database_url
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from .conftest import drop_database, migrate, recreate_database


def _insert_event(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            "INSERT INTO audit.audit_events (id, entity_type, action) "
            "VALUES (gen_random_uuid(), 'test', 'probe')"
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.audit_events SET action = 'tampered'",
        "DELETE FROM audit.audit_events",
    ],
)
@pytest.mark.parametrize("engine_name", ["app_engine", "owner_engine"])
def test_audit_log_rejects_update_and_delete_for_all_roles(
    request: pytest.FixtureRequest, engine_name: str, statement: str
) -> None:
    engine: Engine = request.getfixturevalue(engine_name)
    with engine.connect() as conn:
        trans = conn.begin()
        _insert_event(conn)
        with pytest.raises(DBAPIError):
            conn.execute(text(statement))
        trans.rollback()


def test_audit_log_rejects_truncate_for_owner(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(DBAPIError):
            conn.execute(text("TRUNCATE audit.audit_events"))
        trans.rollback()


def test_app_role_cannot_create_objects_or_alter_audit(app_engine: Engine) -> None:
    for stmt in (
        "CREATE TABLE public.sneaky (id int)",
        "CREATE TABLE auth.sneaky (id int)",
        "ALTER TABLE audit.audit_events DISABLE TRIGGER ALL",
    ):
        with app_engine.connect() as conn:
            trans = conn.begin()
            with pytest.raises(DBAPIError):
                conn.execute(text(stmt))
            trans.rollback()


def test_migrations_upgrade_downgrade_upgrade(settings: Settings) -> None:
    name = "erp_test_migrations"
    recreate_database(name)
    try:
        engine = create_engine(database_url(settings, owner=True, database=name))
        migrate(name)
        assert {"auth", "audit", "jobs", "source"} <= set(inspect(engine).get_schema_names())
        migrate(name, "base", down=True)
        assert not {"auth", "audit", "jobs", "source"} & set(inspect(engine).get_schema_names())
        migrate(name)
        assert "users" in inspect(engine).get_table_names(schema="auth")
        engine.dispose()
    finally:
        drop_database(name)
