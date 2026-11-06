import asyncio

import procrastinate
import pytest
from erp_backend import tasks
from erp_backend.config import Settings
from erp_backend.jobs import app, conninfo, defer, sync_app
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError


def test_ping_job_runs_end_to_end(migrated_db: str, settings: Settings, app_engine: Engine) -> None:
    with sync_app(settings, database=migrated_db).open() as queue:
        job_id = defer(queue, tasks.ping.name, message="hello")

    async def work() -> None:
        worker_app = app.with_connector(
            procrastinate.PsycopgConnector(conninfo=conninfo(settings, database=migrated_db))
        )
        async with worker_app.open_async():
            await worker_app.run_worker_async(wait=False, concurrency=1)

    asyncio.run(work())
    with app_engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM jobs.procrastinate_jobs WHERE id = :i"), {"i": job_id}
        ).scalar_one()
    assert status == "succeeded"


def test_providers_seeded_disabled(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT code, enabled FROM source.providers")).all())
    assert {
        "sec_edgar",
        "yahoo_chart",
        "us_treasury",
        "bank_of_canada",
        "fred",
        "damodaran",
        "manual_import",
    } == set(rows)
    assert not any(rows.values())


@pytest.mark.parametrize("engine_name", ["app_engine", "owner_engine"])
@pytest.mark.parametrize(
    "statement",
    ["UPDATE source.provider_fetches SET url = 'x'", "DELETE FROM source.provider_fetches"],
)
def test_provider_fetches_are_append_only(
    request: pytest.FixtureRequest, engine_name: str, statement: str
) -> None:
    engine: Engine = request.getfixturevalue(engine_name)
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text(
                "INSERT INTO source.provider_fetches (id, provider_code, url) "
                "VALUES (gen_random_uuid(), 'sec_edgar', 'https://x')"
            )
        )
        with pytest.raises(DBAPIError, match=r"append-only|permission denied"):
            conn.execute(text(statement))
        trans.rollback()


def test_app_role_cannot_modify_provider_registry(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(DBAPIError):
            conn.execute(text("UPDATE source.providers SET enabled = true"))
        trans.rollback()
