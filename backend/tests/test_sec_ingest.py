"""SEC ingestion service against the test database, with mocked HTTP only."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from erp_backend.config import Settings
from erp_backend.ingestion import archive
from erp_backend.ingestion.http import RateLimiter
from erp_backend.ingestion.sec import sec_client
from erp_backend.ingestion.sec_ingest import (
    SecIngestError,
    companyfacts_url,
    ingest_company,
    refresh_ticker_map,
    resolve_cik,
    submissions_url,
    ticker_map_is_stale,
)
from erp_backend.models import (
    AuditEvent,
    Company,
    CompanyIdentifier,
    Filing,
    ProviderFetch,
    ReportedFact,
    Security,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from . import sec_payloads as p

UA = "Validator Fixture fixture-only@validator-fixture.dev"  # test-only; MockTransport

ACME_FACTS = {
    "us-gaap": {
        "Revenues": {
            "USD": [
                p.obs(
                    1000,
                    "2022-12-31",
                    start="2022-01-01",
                    accn="0009990001-23-000001",
                    form="10-K",
                    filed="2023-02-01",
                    fy=2022,
                    fp="FY",
                ),
                p.obs(
                    1010,
                    "2022-12-31",
                    start="2022-01-01",
                    accn="0009990001-24-000002",
                    form="10-K",
                    filed="2024-02-01",
                    fy=2023,
                    fp="FY",
                ),  # restated
                p.obs(
                    1100,
                    "2023-12-31",
                    start="2023-01-01",
                    accn="0009990001-24-000002",
                    form="10-K",
                    filed="2024-02-01",
                    fy=2023,
                    fp="FY",
                ),
            ]
        },
        "Assets": {
            "USD": [
                p.obs(
                    5000, "2023-12-31", accn="0009990001-24-000002", form="10-K", filed="2024-02-01"
                )
            ]
        },
    },
    "dei": {
        "EntityCommonStockSharesOutstanding": {
            "shares": [
                p.obs(
                    100, "2024-01-20", accn="0009990001-24-000002", form="10-K", filed="2024-02-01"
                )
            ]
        }
    },
}


class FakeSec:
    """Routes SEC URLs to canned responses; records requests (never touches the network)."""

    def __init__(self, routes: dict[str, list[httpx.Response] | httpx.Response]) -> None:
        self.routes = {k: v if isinstance(v, list) else [v] for k, v in routes.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(str(request.url).split("?")[0])
        if not queue:
            return httpx.Response(404)
        return queue.pop(0) if len(queue) > 1 else queue[0]


def ok(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "application/json"})


def default_routes() -> dict[str, list[httpx.Response] | httpx.Response]:
    return {
        "https://www.sec.gov/files/company_tickers_exchange.json": ok(
            p.ticker_map(
                [(p.ACME_CIK, "Acme Corp", "ACME", "Nasdaq"), (p.MAPLE_CIK, "Maple", "MPL", "NYSE")]
            )
        ),
        submissions_url(p.ACME_CIK): ok(
            p.submissions(p.ACME_CIK, "Acme Corp", ["ACME"], ["Nasdaq"])
        ),
        companyfacts_url(p.ACME_CIK): ok(p.companyfacts(p.ACME_CIK, ACME_FACTS)),
    }


@pytest.fixture
def make_client(settings: Settings) -> Callable[[FakeSec], object]:
    configured = settings.model_copy(update={"sec_user_agent": UA})

    def build(fake: FakeSec, limiter: RateLimiter | None = None):  # type: ignore[no-untyped-def]
        client = sec_client(
            configured, transport=httpx.MockTransport(fake), limiter=limiter or RateLimiter(1e6)
        )
        client.sleep = lambda s: None
        return client

    return build


def test_ingest_company_end_to_end(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    fake = FakeSec(default_routes())
    summary = ingest_company(db, make_client(fake), tmp_path, ticker="acme")

    assert [str(r.url) for r in fake.requests] == [
        "https://www.sec.gov/files/company_tickers_exchange.json",
        submissions_url(p.ACME_CIK),
        companyfacts_url(p.ACME_CIK),
    ]
    assert all(r.headers["user-agent"] == UA for r in fake.requests)

    company = db.get(Company, summary.company_id)
    assert company is not None and company.legal_name == "Acme Corp"
    assert (company.country, company.fiscal_year_end_mmdd, company.sic_code) == (
        "US",
        "1231",
        "3571",
    )
    assert (company.accounting_standard, company.reporting_currency) == ("US_GAAP", "USD")
    assert company.accounting_standard_inferred and company.interim_coverage == "none"
    assert db.get(CompanyIdentifier, ("SEC_CIK", f"{p.ACME_CIK:010d}")) is not None
    sec = db.scalars(select(Security).where(Security.company_id == company.id)).one()
    assert (sec.ticker, sec.exchange_mic, sec.is_primary) == ("ACME", "XNAS", True)

    assert summary.facts.observations == 5 and summary.facts.inserted == 5
    assert db.scalar(select(func.count()).select_from(Filing)) == 2  # 2 distinct accessions

    fetches = db.scalars(select(ProviderFetch).order_by(ProviderFetch.retrieved_at)).all()
    assert len(fetches) == 3 and all(f.http_status == 200 for f in fetches)
    for f in fetches:  # archive-first: every payload is stored and verifiable
        assert f.payload_path and f.content_sha256
        assert archive.load(tmp_path, f.payload_path, f.content_sha256)
        assert "fixture-only" not in (f.url + str(f.params))  # UA never stored
    actions = db.scalars(select(AuditEvent.action)).all()
    assert "company_created" in actions and "company_inferred_fields_updated" in actions


def test_reingest_is_idempotent_and_audits_profile_changes(
    db: Session, tmp_path: Path, make_client
) -> None:  # type: ignore[no-untyped-def]
    ingest_company(db, make_client(FakeSec(default_routes())), tmp_path, ticker="ACME")
    routes = default_routes()
    routes[submissions_url(p.ACME_CIK)] = ok(
        p.submissions(p.ACME_CIK, "Acme Corporation", ["ACME"], ["Nasdaq"])
    )
    again = ingest_company(db, make_client(FakeSec(routes)), tmp_path, cik=p.ACME_CIK)
    assert again.facts.inserted == 0
    assert db.scalar(select(func.count()).select_from(ReportedFact)) == 5
    assert db.scalar(select(func.count()).select_from(Company)) == 1
    update = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "company_profile_updated")
    ).one()
    assert update.before == {"legal_name": "Acme Corp"}
    assert update.after == {"legal_name": "Acme Corporation"}


def test_fresh_ticker_map_is_reused(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    fake = FakeSec(default_routes())
    client = make_client(fake)
    refresh_ticker_map(db, client, tmp_path)
    assert not ticker_map_is_stale(db)
    assert ticker_map_is_stale(db, now=datetime.now(UTC) + timedelta(days=2))
    ingest_company(db, client, tmp_path, ticker="ACME")
    assert sum("company_tickers" in str(r.url) for r in fake.requests) == 1


def test_unknown_and_ambiguous_tickers(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    routes = default_routes()
    routes["https://www.sec.gov/files/company_tickers_exchange.json"] = ok(
        p.ticker_map([(1, "A", "DUP", None), (2, "B", "DUP", None)])
    )
    refresh_ticker_map(db, make_client(FakeSec(routes)), tmp_path)
    with pytest.raises(SecIngestError, match="not found"):
        resolve_cik(db, "NOPE")
    with pytest.raises(SecIngestError, match="several CIKs"):
        resolve_cik(db, "DUP")


def test_http_404_is_logged_and_nothing_is_created(
    db: Session, tmp_path: Path, make_client
) -> None:  # type: ignore[no-untyped-def]
    routes = default_routes()
    del routes[submissions_url(p.ACME_CIK)]
    with pytest.raises(SecIngestError, match="HTTP 404"):
        ingest_company(db, make_client(FakeSec(routes)), tmp_path, cik=p.ACME_CIK)
    fetch = db.scalars(select(ProviderFetch)).one()
    assert fetch.http_status == 404  # logged (and its payload archived) despite the failure
    assert db.scalar(select(func.count()).select_from(Company)) == 0


def test_transient_errors_are_retried(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    routes = default_routes()
    routes[submissions_url(p.ACME_CIK)] = [
        httpx.Response(503),
        httpx.Response(429),
        ok(p.submissions(p.ACME_CIK, "Acme Corp", ["ACME"], ["Nasdaq"])),
    ]
    fake = FakeSec(routes)
    ingest_company(db, make_client(fake), tmp_path, cik=p.ACME_CIK)
    assert sum(submissions_url(p.ACME_CIK) == str(r.url) for r in fake.requests) == 3


def test_persistent_failure_records_error_without_payload(
    db: Session, tmp_path: Path, make_client
) -> None:  # type: ignore[no-untyped-def]
    routes = default_routes()
    routes[submissions_url(p.ACME_CIK)] = httpx.Response(503)
    with pytest.raises(SecIngestError, match="Fetch failed"):
        ingest_company(db, make_client(FakeSec(routes)), tmp_path, cik=p.ACME_CIK)
    fetch = db.scalars(select(ProviderFetch)).one()
    assert fetch.error and fetch.payload_path is None and fetch.http_status is None


def test_malformed_payload_is_archived_then_rejected(
    db: Session, tmp_path: Path, make_client
) -> None:  # type: ignore[no-untyped-def]
    routes = default_routes()
    routes[companyfacts_url(p.ACME_CIK)] = ok(
        b'{"cik": 9990001, "facts": {"us-gaap": {"X": {"units": {"USD": [{"val": 1}]}}}}}'
    )
    with pytest.raises(SecIngestError, match="unparseable"):
        ingest_company(db, make_client(FakeSec(routes)), tmp_path, cik=p.ACME_CIK)
    bad = db.scalars(
        select(ProviderFetch).where(ProviderFetch.url == companyfacts_url(p.ACME_CIK))
    ).one()
    assert bad.payload_path and (tmp_path / bad.payload_path).exists()
    assert db.scalar(select(func.count()).select_from(ReportedFact)) == 0


def test_cik_mismatch_rejected(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    routes = default_routes()
    routes[submissions_url(p.ACME_CIK)] = ok(p.submissions(p.MAPLE_CIK, "Maple", ["MPL"], ["NYSE"]))
    with pytest.raises(SecIngestError, match="does not match"):
        ingest_company(db, make_client(FakeSec(routes)), tmp_path, cik=p.ACME_CIK)


def test_requests_respect_five_per_second(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    now = [0.0]
    starts: list[float] = []

    def sleep(seconds: float) -> None:
        now[0] += seconds

    limiter = RateLimiter(5.0, clock=lambda: now[0], sleep=sleep)
    fake = FakeSec(default_routes())

    def timed(request: httpx.Request) -> httpx.Response:
        starts.append(now[0])
        return fake(request)

    client = make_client(fake, limiter)
    client._client = httpx.Client(
        transport=httpx.MockTransport(timed),
        headers={"User-Agent": UA},
    )
    ingest_company(db, client, tmp_path, ticker="ACME")
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert len(starts) == 3 and all(g >= 0.2 - 1e-9 for g in gaps)


def test_sec_client_default_limiter_is_five_per_second(settings: Settings) -> None:
    client = sec_client(
        settings.model_copy(update={"sec_user_agent": UA}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert client.limiter.interval == pytest.approx(0.2)


SOURCE_TABLES = ("source.reported_facts", "source.filings", "source.sec_tickers")

SEED = """
WITH f AS (INSERT INTO source.provider_fetches (id, provider_code, url)
           VALUES (gen_random_uuid(), 'sec_edgar', 'https://x') RETURNING id),
     c AS (INSERT INTO market.companies (id, legal_name) VALUES (gen_random_uuid(), 'T')
           RETURNING id),
     fi AS (INSERT INTO source.filings (id, company_id, accession_no, form, filed_date, fetch_id)
            SELECT gen_random_uuid(), c.id, 'acc-1', '10-K', '2024-01-01', f.id FROM c, f),
     st AS (INSERT INTO source.sec_tickers (fetch_id, cik, ticker, name)
            SELECT f.id, 1, 'T', 'T' FROM f)
INSERT INTO source.reported_facts (company_id, taxonomy, concept, unit, value, period_end,
                                   form, filed_date, accession_no, fetch_id)
SELECT c.id, 'us-gaap', 'Assets', 'USD', 1, '2023-12-31', '10-K', '2024-01-01', 'acc-1', f.id
FROM c, f
"""


@pytest.mark.parametrize("table", SOURCE_TABLES)
@pytest.mark.parametrize(
    "statement", ["UPDATE {t} SET form = form", "DELETE FROM {t}", "TRUNCATE {t} CASCADE"]
)
def test_source_records_append_only_even_for_owner(
    owner_engine: Engine, table: str, statement: str
) -> None:
    stmt = statement.format(t=table)
    if "sec_tickers" in table and "form" in stmt:
        stmt = f"UPDATE {table} SET name = name"
    with owner_engine.connect() as conn:
        trans = conn.begin()
        conn.execute(text(SEED))
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(stmt))
        trans.rollback()


@pytest.mark.parametrize("table", SOURCE_TABLES)
def test_app_role_cannot_update_or_delete_source_records(app_engine: Engine, table: str) -> None:
    for stmt in (f"DELETE FROM {table}", f"UPDATE {table} SET fetch_id = fetch_id"):
        with app_engine.connect() as conn:
            trans = conn.begin()
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(stmt))
            trans.rollback()


def test_sec_tasks_share_the_sec_lock(
    migrated_db: str, settings: Settings, app_engine: Engine
) -> None:
    from erp_backend import tasks
    from erp_backend.jobs import defer, sync_app

    with sync_app(settings, database=migrated_db).open() as queue:
        job = defer(queue, tasks.sec_ingest_company.name, ticker="ACME")
    with app_engine.connect() as conn:
        row = conn.execute(
            text("SELECT queue_name, lock FROM jobs.procrastinate_jobs WHERE id = :i"), {"i": job}
        ).one()
    assert tuple(row) == ("ingestion", "sec")


def test_rebuild_from_archive_needs_no_network_and_restores_derived_fields(
    db: Session, tmp_path: Path, make_client
) -> None:  # type: ignore[no-untyped-def]
    from erp_backend.ingestion.sec_ingest import rebuild_company_from_archive

    summary = ingest_company(db, make_client(FakeSec(default_routes())), tmp_path, ticker="ACME")
    company = db.get(Company, summary.company_id)
    assert company is not None
    fetches_before = db.scalar(select(func.count()).select_from(ProviderFetch))
    company.country, company.interim_coverage = None, "full"  # simulate stale derived fields
    db.commit()
    level = rebuild_company_from_archive(db, tmp_path, company)  # no client: cannot fetch
    assert (company.country, level, company.interim_coverage) == ("US", "none", "none")
    assert db.scalar(select(func.count()).select_from(ProviderFetch)) == fetches_before


def test_rebuild_detects_tampered_archive(db: Session, tmp_path: Path, make_client) -> None:  # type: ignore[no-untyped-def]
    from erp_backend.ingestion.sec_ingest import rebuild_company_from_archive

    summary = ingest_company(db, make_client(FakeSec(default_routes())), tmp_path, ticker="ACME")
    sub_fetch = db.scalars(
        select(ProviderFetch).where(ProviderFetch.url == submissions_url(p.ACME_CIK))
    ).one()
    assert sub_fetch.payload_path
    target = tmp_path / sub_fetch.payload_path
    target.chmod(0o644)
    target.write_bytes(archive.zstandard.ZstdCompressor().compress(b'{"cik": "1"}'))
    company = db.get(Company, summary.company_id)
    assert company is not None
    with pytest.raises(archive.ArchiveIntegrityError):
        rebuild_company_from_archive(db, tmp_path, company)
