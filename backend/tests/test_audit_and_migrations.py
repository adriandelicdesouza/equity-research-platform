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


def test_rates_and_reference_migrations_downgrade_only_without_data(settings: Settings) -> None:
    """0014/0015 (D58): clean downgrade and re-upgrade while empty; refused once data exists."""
    name = "erp_test_migrations_d58"
    recreate_database(name)
    try:
        engine = create_engine(database_url(settings, owner=True, database=name))
        migrate(name)
        migrate(name, "0013", down=True)
        assert "rate_series" not in inspect(engine).get_table_names(schema="market")
        assert "reference_datasets" not in inspect(engine).get_table_names(schema="ref")
        migrate(name)
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM market.rate_series")).scalar() == 19
            conn.execute(
                text(
                    "INSERT INTO source.provider_fetches (id, provider_code, url) VALUES "
                    "('00000000-0000-0000-0000-000000000001', 'us_treasury', 'https://x.invalid')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO market.rate_observations (series_code, provider_code, obs_date,"
                    " value, fetch_id) VALUES ('UST_PAR_10Y', 'us_treasury', '2024-03-05', 4.15,"
                    " '00000000-0000-0000-0000-000000000001')"
                )
            )
        migrate(name, "0014", down=True)  # 0015 has no data: fine
        with pytest.raises(DBAPIError, match="downgrade would lose provenance"):
            migrate(name, "0013", down=True)
        engine.dispose()
    finally:
        drop_database(name)
