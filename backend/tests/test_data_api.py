"""Read-only data API (D59): every resource group, query validation, errors, auth, and the
read-only boundary.

Runs against its own disposable database ``erp_test_api``, seeded once with **synthetic**
data through the platform's real service functions (SEC-style facts → dataset build →
conflict resolution; statement, market and Damodaran file imports; rate/FX payloads), then
committed, so the API reads it through genuine ``READ ONLY`` transactions. No network.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from erp_backend import audit
from erp_backend.api import data as data_api
from erp_backend.api.admin import create_user_record
from erp_backend.config import Settings
from erp_backend.datasets import build_dataset, rebuild_draft, resolve_conflict
from erp_backend.db import database_url, get_db
from erp_backend.ingestion import archive
from erp_backend.main import create_app
from erp_backend.market import file_import as market_import
from erp_backend.models import (
    Company,
    CompanyClassification,
    DataConflict,
    PriceObservation,
    ProviderFetch,
    Security,
)
from erp_backend.rates import bank_of_canada, treasury
from erp_backend.rates.store import store_parsed
from erp_backend.readonly import ReadOnlyViolationError, make_read_only, read_only_db
from erp_backend.reference import damodaran
from erp_backend.statements import file_import as statement_import
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import ORIGIN, PASSWORD, drop_database, migrate, recreate_database
from tests.rates_payloads import TREASURY_GOOD, VALET_GOOD, treasury_csv
from tests.test_datasets import add_facts
from tests.test_statement_import import FY23, FY24, statement

API_DB = "erp_test_api"
ROOT = Path(__file__).resolve().parents[2] / "mappings"
TODAY = date(2025, 6, 30)
END = date(2024, 12, 31)
FY = (date(2024, 1, 1), END)
F1 = ("10-K", date(2025, 2, 1), "0000000001-25-000001")
F2 = ("10-K", date(2026, 2, 1), "0000000001-26-000001")
DATA_SCHEMAS = ("market", "fin", "source", "ref")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(db: Session, code: str, payload: bytes, root: Path) -> ProviderFetch:
    stored = archive.store(root, code, payload)
    row = ProviderFetch(
        provider_code=code,
        url="https://example.invalid/synthetic",
        http_status=200,
        content_sha256=stored.sha256,
        payload_path=stored.relative_path,
        bytes=stored.size,
        stored_bytes=stored.stored_size,
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def api_engine(settings: Settings) -> Iterator[Engine]:
    recreate_database(API_DB)
    migrate(API_DB)
    engine = create_engine(database_url(settings, database=API_DB))
    yield engine
    engine.dispose()
    drop_database(API_DB)


@pytest.fixture(scope="module")
def seeded(api_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    raw = tmp_path_factory.mktemp("raw")
    ids: dict[str, Any] = {}
    with Session(api_engine, expire_on_commit=False) as db:
        for username, roles in (("reviewer", ["reviewer"]), ("noroles", [])):
            create_user_record(
                db, username=username, display_name=username, password=PASSWORD, roles=roles
            )

        # SEC-style company: dataset v1 (conflict) → resolution → v2 (resolved).
        sec = Company(
            legal_name="Conflict Co",
            common_name="Conflict",
            country="US",
            reporting_currency="USD",
            accounting_standard="US_GAAP",
        )
        db.add(sec)
        db.flush()
        db.add(
            Security(
                company_id=sec.id,
                ticker="CFC",
                exchange_name="Nasdaq",
                exchange_mic="XNAS",
                trading_currency="USD",
                provider_symbols={"yahoo_chart": "CFC"},
            )
        )
        add_facts(
            db,
            sec,
            [
                ("Revenues", "1000", *FY, *F1),
                ("Assets", "100123", None, END, *F1),
                ("Liabilities", "60111", None, END, *F1),
                ("StockholdersEquity", "40012", None, END, *F1),
                ("StockholdersEquity", "50012", None, END, *F2),
            ],
        )
        d1 = build_dataset(db, sec, mappings_root=ROOT, mapping_version="v2").dataset
        conflict = db.scalars(select(DataConflict).where(DataConflict.dataset_id == d1.id)).one()
        first = next(c for c in conflict.candidates if c["role"] == "first_reported")
        resolution = resolve_conflict(
            db,
            d1.id,
            conflict.conflict_key,
            "select_candidate",
            reason="Original vintage is consistent with the liabilities reported.",
            candidate_fact_id=first["fact_id"],
        )
        d2 = rebuild_draft(
            db, sec, mappings_root=ROOT, mapping_version="v2", reason="apply conflict resolution"
        ).dataset
        db.add_all(
            [
                CompanyClassification(
                    company_id=sec.id,
                    kind="suggested",
                    classification="non_financial",
                    evidence={"sic": None},
                    classifier_version="1",
                ),
                CompanyClassification(
                    company_id=sec.id,
                    kind="confirmed",
                    classification="non_financial",
                    evidence={},
                    reason="Reviewed: an industrial company.",
                ),
            ]
        )
        sec.financial_classification = "non_financial"
        ids.update(
            sec=sec.id,
            d1=d1.id,
            d2=d2.id,
            conflict_key=conflict.conflict_key,
            resolution=resolution.id,
        )

        # Statement import: a 'reported' dataset with manual_import provenance.
        stc = Company(
            legal_name="Statement Test Co",
            country="CA",
            reporting_currency="CAD",
            accounting_standard="IFRS",
            accounting_standard_inferred=False,
        )
        db.add(stc)
        db.flush()
        db.add(
            Security(
                company_id=stc.id,
                ticker="STC",
                exchange_name="Toronto Stock Exchange",
                exchange_mic="XTSE",
                trading_currency="CAD",
            )
        )
        db.flush()
        data = statement(columns=(FY23, FY24))
        result = statement_import.ingest(
            db,
            filename="stc_statements.csv",
            data=data,
            source_name="Analyst transcription of the annual report",
            expected_sha256=sha(data),
            raw_root=raw,
            mappings_root=ROOT,
            default_mapping_version="v2",
            today=TODAY,
        )
        ids.update(
            stc=stc.id, statement_import=result.import_id, reported=result.dataset.dataset.id
        )

        # Market data: manual raw + split-adjusted series, a dividend, a Yahoo revision.
        tst_company = Company(legal_name="Import Test Co", country="US")
        db.add(tst_company)
        db.flush()
        tst = Security(
            company_id=tst_company.id,
            ticker="TST",
            exchange_name="Nasdaq",
            exchange_mic="XNAS",
            trading_currency="USD",
        )
        db.add(tst)
        db.flush()
        header = (
            "ticker,exchange_mic,date,open,high,low,close,close_basis,adj_close,"
            "adj_close_basis,volume,currency"
        )
        for name, basis, rows in (
            (
                "raw.csv",
                "raw",
                (
                    "2024-03-04,10.00,10.50,9.90,10.20",
                    "2024-03-05,10.20,10.60,10.00,10.40",
                    "2024-03-06,10.40,10.70,10.30,10.65",
                ),
            ),
            ("adj.csv", "split_adjusted", ("2024-03-04,5.00,5.25,4.95,5.10",)),
        ):
            lines = [f"TST,XNAS,{r},{basis},,,1000,USD" for r in rows]
            body = ("\n".join([header, *lines]) + "\n").encode()
            market_import.ingest(
                db,
                filename=name,
                data=body,
                template="market_prices.v1",
                source_name="Synthetic exchange export",
                expected_sha256=sha(body),
                raw_root=raw,
                today=TODAY,
            )
        actions = (
            b"ticker,exchange_mic,ex_date,kind,amount,currency\n"
            b"TST,XNAS,2024-03-05,cash_dividend,0.25,USD\n"
        )
        market_import.ingest(
            db,
            filename="div.csv",
            data=actions,
            template="corporate_actions.v1",
            source_name="Synthetic dividend notice",
            expected_sha256=sha(actions),
            raw_root=raw,
            today=TODAY,
        )
        f1 = fetch(db, "yahoo_chart", b"synthetic-1", raw)
        f2 = fetch(db, "yahoo_chart", b"synthetic-2", raw)
        for f, adj in ((f1, "10.10"), (f2, "10.05")):
            db.add(
                PriceObservation(
                    security_id=tst.id,
                    provider_code="yahoo_chart",
                    fetch_id=f.id,
                    trade_date=date(2024, 3, 4),
                    close=Decimal("10.20"),
                    adj_close=Decimal(adj),
                    currency="USD",
                    close_basis="unverified",
                    adj_close_basis="unverified",
                )
            )
        ids.update(tst=tst.id)

        # Rates and FX (synthetic payloads), with one Treasury revision.
        store_parsed(
            db,
            treasury.parse_par_yield_csv(TREASURY_GOOD, TODAY),
            fetch(db, "us_treasury", TREASURY_GOOD, raw),
        )
        revised = treasury_csv(
            "03/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.16,4.42,4.29"
        )
        store_parsed(
            db, treasury.parse_par_yield_csv(revised, TODAY), fetch(db, "us_treasury", revised, raw)
        )
        store_parsed(
            db,
            bank_of_canada.parse_valet(VALET_GOOD, TODAY),
            fetch(db, "bank_of_canada", VALET_GOOD, raw),
        )

        # Damodaran: CRP v1 → v2 (superseded), and a rating-spread table.
        header_crp = "country_name,country_iso2,moodys_rating,country_risk_premium,total_erp"
        v1 = f"{header_crp}\nCanada,CA,Aaa,0.00%,4.60%\nRuritania,DE,Aa1,0.70%,5.30%\n".encode()
        v2 = f"{header_crp}\nCanada,CA,Aaa,0.00%,4.60%\nRuritania,DE,Aa1,0.80%,5.40%\n".encode()
        common = {
            "template": "damodaran_country_risk.v1",
            "edition_date": date(2024, 1, 5),
            "source_name": "Synthetic Damodaran-format file",
            "raw_root": raw,
            "today": TODAY,
        }
        r1 = damodaran.ingest(db, filename="crp.csv", data=v1, expected_sha256=sha(v1), **common)
        r2 = damodaran.ingest(
            db,
            filename="crp_fix.csv",
            data=v2,
            expected_sha256=sha(v2),
            supersede=True,
            reason="Corrected DE transcription",
            **common,
        )
        spreads = (
            b"coverage_min,coverage_max,rating,spread\n"
            b"-100000,1.999999,B,4.50%\n2,100000,AAA,0.60%\n"
        )
        damodaran.ingest(
            db,
            filename="spreads.csv",
            data=spreads,
            template=damodaran.TEMPLATES["damodaran_rating_spreads.v1"].code,
            edition_date=date(2024, 1, 5),
            variant="large_cap",
            source_name="Synthetic",
            expected_sha256=sha(spreads),
            raw_root=raw,
            today=TODAY,
        )
        ids.update(crp_v1=r1.dataset_id, crp_v2=r2.dataset_id, crp_import=r1.import_id)
        audit.record(db, action="test_seed", entity_type="test")
        db.commit()
    return ids


@pytest.fixture(scope="module")
def factory(api_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(api_engine, expire_on_commit=False)


def _app(factory: sessionmaker[Session], settings: Settings):  # type: ignore[no-untyped-def]
    app = create_app(settings)

    def db_override() -> Iterator[Session]:
        with factory() as s:
            yield s

    def read_override() -> Iterator[Session]:  # the production logic, on the test database
        s = factory()
        try:
            yield make_read_only(s)
        finally:
            s.rollback()
            s.close()

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[read_only_db] = read_override
    return app


def _login(c: TestClient, username: str) -> None:
    r = c.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def client(
    seeded: dict[str, Any], factory: sessionmaker[Session], settings: Settings
) -> Iterator[TestClient]:
    with TestClient(_app(factory, settings), base_url="https://testserver") as c:
        _login(c, "reviewer")
        yield c


def ok(c: TestClient, url: str, **params: Any) -> Any:
    r = c.get(url, params=params)
    assert r.status_code == 200, (url, r.status_code, r.text)
    return r.json()


def err(c: TestClient, url: str, status: int, code: str, **params: Any) -> dict[str, Any]:
    r = c.get(url, params=params)
    assert r.status_code == status, (url, r.status_code, r.text)
    body = r.json()
    assert set(body) == {"error"} and body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str)
    return body


# --------------------------------------------------------------------------- boundary


def _data_routes() -> list[APIRoute]:
    return [r for r in data_api.router.routes if isinstance(r, APIRoute)]


def test_every_data_route_is_get_only() -> None:
    routes = _data_routes()
    assert len(routes) == 24
    assert all(r.methods == {"GET"} for r in routes), [(r.path, r.methods) for r in routes]


def test_openapi_documents_data_paths_as_get_only(client: TestClient) -> None:
    spec = ok(client, "/api/openapi.json")
    data_paths = {
        p: ops
        for p, ops in spec["paths"].items()
        if not p.startswith(("/api/auth", "/api/admin", "/api/health"))
    }
    assert "/api/datasets/{dataset_id}/values" in data_paths
    assert all(set(ops) == {"get"} for ops in data_paths.values())


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutating_methods_are_not_routed(
    client: TestClient, seeded: dict[str, Any], method: str
) -> None:
    for url in (
        "/api/companies",
        f"/api/datasets/{seeded['d2']}",
        f"/api/datasets/{seeded['d1']}/conflicts",
        "/api/imports",
        "/api/reference/datasets",
    ):
        r = client.request(method, url, headers={"Origin": ORIGIN}, json={})
        assert r.status_code == 405, (method, url, r.status_code)


def test_read_session_refuses_writes_at_orm_and_database(factory: sessionmaker[Session]) -> None:
    s = make_read_only(factory())
    try:
        s.add(Company(legal_name="Should never be stored"))
        with pytest.raises(ReadOnlyViolationError):
            s.flush()
        s.expunge_all()
        with pytest.raises(ReadOnlyViolationError):
            s.execute(update(Company).values(status="x"))
        with pytest.raises(DBAPIError, match="read-only transaction"):
            s.execute(
                text(
                    "INSERT INTO market.companies (id, legal_name, status) "
                    "VALUES (gen_random_uuid(), 'x', 'active')"
                )
            )
    finally:
        s.rollback()
        s.close()


def test_read_only_violation_maps_to_a_clean_error(
    factory: sessionmaker[Session],
    settings: Settings,
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_backend.read import companies

    def bad(db: Session, company_id: uuid.UUID) -> Any:
        db.add(Company(legal_name="sneaky"))
        db.flush()

    monkeypatch.setattr(companies, "get_company", bad)
    with TestClient(
        _app(factory, settings), base_url="https://testserver", raise_server_exceptions=False
    ) as c:
        _login(c, "reviewer")
        err(c, f"/api/companies/{seeded['sec']}", 405, "read_only")


def test_unexpected_errors_do_not_leak_internals(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from erp_backend.read import imports

    def boom(*_: object, **__: object) -> Any:
        raise RuntimeError("SELECT password FROM /home/secret/path")

    monkeypatch.setattr(imports, "list_imports", boom)
    with TestClient(
        _app(factory, settings), base_url="https://testserver", raise_server_exceptions=False
    ) as c:
        _login(c, "reviewer")
        r = c.get("/api/imports")
        assert r.status_code == 500
        assert r.json() == {
            "error": {"code": "internal_error", "message": "An internal error occurred."}
        }
        assert "SELECT" not in r.text and "/home" not in r.text


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT schemaname, tablename FROM pg_tables WHERE schemaname = ANY(:s)"),
            {"s": list(DATA_SCHEMAS)},
        ).all()
        return {
            f"{s}.{t}": conn.execute(text(f'SELECT count(*) FROM "{s}"."{t}"')).scalar_one()
            for s, t in tables
        }


def test_api_calls_create_no_persistent_records(
    client: TestClient, seeded: dict[str, Any], api_engine: Engine
) -> None:
    before = _counts(api_engine)
    for url in (
        "/api/companies",
        f"/api/companies/{seeded['sec']}",
        "/api/listings",
        f"/api/companies/{seeded['sec']}/datasets",
        f"/api/datasets/{seeded['d2']}",
        f"/api/datasets/{seeded['d2']}/values",
        f"/api/datasets/{seeded['d2']}/statements/BS",
        f"/api/datasets/{seeded['d2']}/checks",
        f"/api/datasets/{seeded['d2']}/integrity",
        f"/api/datasets/{seeded['d1']}/conflicts",
        f"/api/companies/{seeded['sec']}/conflict-resolutions",
        "/api/imports",
        f"/api/imports/{seeded['statement_import']}",
        f"/api/listings/{seeded['tst']}/prices?close_basis=raw&provider=manual_import",
        f"/api/listings/{seeded['tst']}/corporate-actions",
        "/api/rates/series",
        "/api/rates/series/UST_PAR_10Y/observations",
        "/api/fx/rates?base=USD&quote=CAD",
        "/api/reference/datasets",
        f"/api/reference/datasets/{seeded['crp_v1']}/records",
    ):
        assert client.get(url).status_code == 200, url
    assert _counts(api_engine) == before


# --------------------------------------------------------------------------- auth


def test_unauthenticated_requests_are_refused(
    seeded: dict[str, Any], factory: sessionmaker[Session], settings: Settings
) -> None:
    with TestClient(_app(factory, settings), base_url="https://testserver") as c:
        for url in (
            "/api/companies",
            f"/api/datasets/{seeded['d2']}/values",
            "/api/fx/rates?base=USD&quote=CAD",
            "/api/reference/datasets",
        ):
            err(c, url, 401, "not_authenticated")


def test_user_without_read_permission_is_forbidden(
    seeded: dict[str, Any], factory: sessionmaker[Session], settings: Settings
) -> None:
    with TestClient(_app(factory, settings), base_url="https://testserver") as c:
        _login(c, "noroles")
        body = err(c, "/api/companies", 403, "forbidden")
        assert "company:read" in body["error"]["message"]


# --------------------------------------------------------------------------- companies


def test_company_list_search_filter_sort_and_paginate(client: TestClient) -> None:
    page = ok(client, "/api/companies", limit=2)
    assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (3, 2, 0, 2)
    assert [c["legal_name"] for c in page["items"]] == ["Conflict Co", "Import Test Co"]
    assert ok(client, "/api/companies", limit=2, offset=2)["items"][0]["legal_name"] == (
        "Statement Test Co"
    )
    assert ok(client, "/api/companies", sort="-name")["items"][0]["legal_name"] == (
        "Statement Test Co"
    )
    assert [c["legal_name"] for c in ok(client, "/api/companies", q="stc")["items"]] == [
        "Statement Test Co"
    ]  # exact ticker match
    assert ok(client, "/api/companies", country="ca")["total"] == 1
    assert ok(client, "/api/companies", q="conflict")["items"][0]["tickers"] == ["CFC:XNAS"]


@pytest.mark.parametrize(
    ("params", "loc"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 501}, "limit"),
        ({"offset": -1}, "offset"),
        ({"sort": "revenue"}, "sort"),
        ({"country": "USA"}, "country"),
    ],
)
def test_invalid_query_parameters(client: TestClient, params: dict[str, Any], loc: str) -> None:
    body = err(client, "/api/companies", 422, "validation_error", **params)
    assert any(loc in d["loc"] for d in body["error"]["details"])


def test_company_detail(client: TestClient, seeded: dict[str, Any]) -> None:
    c = ok(client, f"/api/companies/{seeded['sec']}")
    assert (c["country"], c["reporting_currency"], c["financial_classification"]) == (
        "US",
        "USD",
        "non_financial",
    )
    cls = c["classification"]
    assert cls["status"] == "confirmed"
    assert cls["latest_confirmation"]["reason"] == "Reviewed: an industrial company."
    assert cls["latest_suggestion"]["kind"] == "suggested"
    listing = c["listings"][0]
    assert (listing["ticker"], listing["exchange_mic"], listing["trading_currency"]) == (
        "CFC",
        "XNAS",
        "USD",
    )
    assert listing["provider_symbols"] == {"yahoo_chart": "CFC"}
    assert listing["origin"] == "sec"
    history = ok(client, f"/api/companies/{seeded['sec']}/classifications")
    assert [h["kind"] for h in history["items"]] == ["confirmed", "suggested"]


def test_missing_and_malformed_identifiers(client: TestClient) -> None:
    missing = uuid.uuid4()
    for url in (
        f"/api/companies/{missing}",
        f"/api/datasets/{missing}",
        f"/api/imports/{missing}",
        f"/api/listings/{missing}/prices",
        f"/api/reference/datasets/{missing}",
        "/api/rates/series/UST_PAR_99Y",
    ):
        err(client, url, 404, "not_found")
    err(client, "/api/companies/not-a-uuid", 422, "validation_error")
    err(client, "/api/rates/series/ust_par_10y", 422, "validation_error")


def test_listing_lookup_by_ticker_and_mic(client: TestClient, seeded: dict[str, Any]) -> None:
    page = ok(client, "/api/listings", ticker="stc", mic="xtse")
    assert page["total"] == 1
    item = page["items"][0]
    assert (item["company_id"], item["trading_currency"]) == (str(seeded["stc"]), "CAD")
    assert ok(client, "/api/listings", ticker="STC", mic="XNAS")["total"] == 0
    assert ok(client, f"/api/listings/{item['id']}")["ticker"] == "STC"


# --------------------------------------------------------------------------- datasets


def test_dataset_versions_basis_and_status(client: TestClient, seeded: dict[str, Any]) -> None:
    page = ok(client, f"/api/companies/{seeded['sec']}/datasets")
    assert [(d["basis"], d["version_no"], d["status"]) for d in page["items"]] == [
        ("standardized", 2, "draft"),
        ("standardized", 1, "discarded"),
    ]
    assert ok(client, f"/api/companies/{seeded['sec']}/datasets", status="draft")["total"] == 1
    reported = ok(client, f"/api/companies/{seeded['stc']}/datasets", basis="reported")
    assert reported["items"][0]["id"] == str(seeded["reported"])
    assert (
        ok(client, f"/api/companies/{seeded['stc']}/datasets", basis="standardized")["total"] == 0
    )
    err(
        client,
        f"/api/companies/{seeded['sec']}/datasets",
        422,
        "validation_error",
        basis="adjusted",
    )
    detail = ok(client, f"/api/datasets/{seeded['d1']}")
    assert detail["open_material_conflicts"] == 1 and detail["conflict_counts"] == {"open": 1}
    assert detail["currency"] == "USD" and detail["source_summary"]["values"] > 0


def test_values_carry_sec_provenance(client: TestClient, seeded: dict[str, Any]) -> None:
    page = ok(client, f"/api/datasets/{seeded['d2']}/values", line_item="BS.TOTAL_EQUITY")
    assert page["total"] == 1
    v = page["items"][0]
    assert (v["statement"], v["unit"], v["provenance_kind"]) == ("BS", "USD", "reported")
    assert v["value"] == "40012"  # the resolved (first-reported) candidate, exact string
    assert v["period"]["end_date"] == "2024-12-31"
    source = v["fact_sources"][0]
    assert (source["concept"], source["accession_no"], source["form"]) == (
        "StockholdersEquity",
        "0000000001-25-000001",
        "10-K",
    )
    assert v["import_source"] is None and v["label"]


def test_values_filters_and_import_provenance(client: TestClient, seeded: dict[str, Any]) -> None:
    url = f"/api/datasets/{seeded['reported']}/values"
    all_values = ok(client, url, limit=500)
    assert {v["provenance_kind"] for v in all_values["items"]} <= {"manual_import", "derived"}
    revenue = ok(client, url, line_item="IS.REVENUE", fiscal_year_from=2024)["items"]
    assert len(revenue) == 1
    r = revenue[0]
    assert (r["value"], r["unit"], r["period"]["fiscal_year"]) == ("1100000", "CAD", 2024)
    src = r["import_source"]
    assert src["original_filename"] == "stc_statements.csv"
    assert src["file_sha256"] == sha(statement(columns=(FY23, FY24)))
    assert src["locator"] and src["source_document"] == "Synthetic test annual report 2024"
    eps = ok(client, url, line_item="IS.EPS_BASIC", fiscal_year_to=2023)["items"][0]
    assert eps["value"] == "1.25"  # per-share amounts are never scaled
    bs = ok(client, url, statement="BS", limit=500)
    assert bs["total"] >= 8 and all(v["statement"] == "BS" for v in bs["items"])
    assert ok(client, url, provenance="analyst")["total"] == 0
    err(client, url, 422, "validation_error", fiscal_year_from=2025, fiscal_year_to=2024)
    err(client, url, 422, "validation_error", period_type="Q5")


def test_statement_grid(client: TestClient, seeded: dict[str, Any]) -> None:
    s = ok(client, f"/api/datasets/{seeded['reported']}/statements/BS")
    assert (s["basis"], s["version_no"], s["currency"], s["statement"]) == (
        "reported",
        1,
        "CAD",
        "BS",
    )
    assert [p["fiscal_year"] for p in s["periods"]] == [2023, 2024]
    rows = {r["line_item_code"]: r for r in s["rows"]}
    assert [c["value"] for c in rows["BS.CASH"]["cells"]] == ["500000", "600000"]
    assert rows["BS.TOTAL_ASSETS"]["label"]
    err(client, f"/api/datasets/{seeded['reported']}/statements/XX", 422, "validation_error")
    empty = ok(client, f"/api/datasets/{seeded['reported']}/statements/IS", period_type="Q1")
    assert empty["periods"] == [] and empty["rows"] == []


def test_checks_and_integrity(client: TestClient, seeded: dict[str, Any]) -> None:
    checks = ok(client, f"/api/datasets/{seeded['reported']}/checks", limit=500)
    assert checks["total"] > 0
    assert {"check_code", "severity", "status", "expected", "actual"} <= set(checks["items"][0])
    integrity = ok(client, f"/api/datasets/{seeded['reported']}/integrity")
    assert integrity["stored_values_match"] is True and integrity["stored_checks_match"] is True
    assert ok(client, f"/api/datasets/{seeded['d2']}/integrity")["stored_values_match"] is True
    discarded = ok(client, f"/api/datasets/{seeded['d1']}/integrity")
    assert discarded["status"] == "discarded" and discarded["stored_values_match"] is None


# --------------------------------------------------------------------------- conflicts


def test_conflicts_and_resolutions(client: TestClient, seeded: dict[str, Any]) -> None:
    open_ = ok(client, f"/api/datasets/{seeded['d1']}/conflicts")["items"]
    assert len(open_) == 1
    c = open_[0]
    assert (c["kind"], c["status"], c["is_material"], c["line_item_code"]) == (
        "mixed_vintage",
        "open",
        True,
        "BS.TOTAL_EQUITY",
    )
    assert {x["role"] for x in c["candidates"]} == {"first_reported", "latest_reported"}
    assert {x["value"] for x in c["candidates"]} == {"40012", "50012"}
    assert all(x["source"] == "reported_fact" and x["accession"] for x in c["candidates"])
    assert c["resolution"] is None
    resolved = ok(client, f"/api/datasets/{seeded['d2']}/conflicts", material=True)["items"][0]
    assert resolved["status"] == "resolved"
    assert resolved["resolution"]["id"] == seeded["resolution"]
    assert resolved["resolution"]["action"] == "select_candidate"
    assert resolved["resolution"]["candidate_source"] == "reported_fact"
    assert ok(client, f"/api/datasets/{seeded['d2']}/conflicts", status="open")["total"] == 0
    history = ok(client, f"/api/companies/{seeded['sec']}/conflict-resolutions")
    assert history["total"] == 1
    assert history["items"][0]["detected_in_dataset_id"] == str(seeded["d1"])
    assert (
        ok(
            client,
            f"/api/companies/{seeded['sec']}/conflict-resolutions",
            conflict_key=seeded["conflict_key"],
        )["total"]
        == 1
    )


# --------------------------------------------------------------------------- imports


def test_import_history_without_paths(client: TestClient, seeded: dict[str, Any]) -> None:
    page = ok(client, "/api/imports", limit=100)
    assert page["total"] == 7
    categories = sorted(i["category"] for i in page["items"])
    assert categories == [
        "market",
        "market",
        "market",
        "reference",
        "reference",
        "reference",
        "statement",
    ]
    assert ok(client, "/api/imports", category="reference")["total"] == 3
    assert ok(client, "/api/imports", template="corporate_actions.v1")["total"] == 1
    statement_import = ok(client, f"/api/imports/{seeded['statement_import']}")
    assert statement_import["record_counts"]["accepted"] > 0
    assert statement_import["stored_objects"]["statement_values"] > 0
    crp = ok(client, f"/api/imports/{seeded['crp_import']}")
    assert crp["stored_objects"]["reference_datasets"][0]["version_no"] == 1
    assert crp["record_counts"]["storable"] == 2
    found = ok(client, "/api/imports", file_sha256=crp["file_sha256"].upper())
    assert found["total"] == 1
    text_all = json.dumps(page) + json.dumps(statement_import) + json.dumps(crp)
    assert "stored_path" not in text_all and ".zst" not in text_all
    err(client, "/api/imports", 422, "validation_error", file_sha256="abc")
    err(
        client,
        "/api/imports",
        422,
        "validation_error",
        imported_from="2025-02-01",
        imported_to="2025-01-01",
    )


# --------------------------------------------------------------------------- market data


def test_prices_never_mix_series(client: TestClient, seeded: dict[str, Any]) -> None:
    url = f"/api/listings/{seeded['tst']}/prices"
    body = err(client, url, 409, "ambiguous_series")
    assert "manual_import" in body["error"]["message"]
    body = err(client, url, 409, "ambiguous_series", provider="manual_import")
    assert "raw" in body["error"]["message"] and "split_adjusted" in body["error"]["message"]
    raw = ok(client, url, provider="manual_import", close_basis="raw")
    assert raw["total"] == 3
    first = raw["items"][0]
    assert (first["trade_date"], first["close"], first["close_basis"], first["currency"]) == (
        "2024-03-04",
        "10.20",
        "raw",
        "USD",
    )
    assert first["origin"]["kind"] == "file_import" and first["origin"]["import_row"] == 1
    adjusted = ok(client, url, provider="manual_import", close_basis="split_adjusted")
    assert [p["close"] for p in adjusted["items"]] == ["5.10"]
    window = ok(
        client,
        url,
        provider="manual_import",
        close_basis="raw",
        start="2024-03-05",
        end="2024-03-05",
    )
    assert [p["trade_date"] for p in window["items"]] == ["2024-03-05"]
    desc = ok(client, url, provider="manual_import", close_basis="raw", sort="-date")
    assert desc["items"][0]["trade_date"] == "2024-03-06"
    err(
        client,
        url,
        422,
        "validation_error",
        provider="manual_import",
        close_basis="raw",
        start="2024-03-06",
        end="2024-03-01",
    )
    err(client, url, 422, "validation_error", close_basis="adjusted")


def test_price_revisions_are_explicit(client: TestClient, seeded: dict[str, Any]) -> None:
    url = f"/api/listings/{seeded['tst']}/prices"
    latest = ok(client, url, provider="yahoo_chart")["items"]
    assert len(latest) == 1
    assert (latest[0]["adj_close"], latest[0]["revisions"]) == ("10.05", 2)
    assert latest[0]["close_basis"] == latest[0]["adj_close_basis"] == "unverified"
    every = ok(client, url, provider="yahoo_chart", revisions="all")["items"]
    assert [p["adj_close"] for p in every] == ["10.10", "10.05"]
    assert all(p["origin"]["kind"] == "provider_fetch" for p in every)


def test_corporate_actions(client: TestClient, seeded: dict[str, Any]) -> None:
    page = ok(client, f"/api/listings/{seeded['tst']}/corporate-actions")
    a = page["items"][0]
    assert (a["kind"], a["ex_date"], a["amount"], a["currency"], a["currency_source"]) == (
        "cash_dividend",
        "2024-03-05",
        "0.25",
        "USD",
        "import_file",
    )
    assert (
        ok(client, f"/api/listings/{seeded['tst']}/corporate-actions", kind="split")["total"] == 0
    )


def test_rate_series_identity_and_observations(client: TestClient) -> None:
    series = ok(client, "/api/rates/series", limit=100)
    assert series["total"] == 19
    cad = ok(client, "/api/rates/series", currency="CAD")["items"]
    assert {s["rate_type"] for s in cad} == {"benchmark_bond_yield"} and len(cad) == 6
    ten = ok(client, "/api/rates/series", tenor="10y")["items"]
    assert {(s["code"], s["currency"], s["rate_type"]) for s in ten} == {
        ("UST_PAR_10Y", "USD", "par_yield"),
        ("GOC_BMK_10Y", "CAD", "benchmark_bond_yield"),
    }
    detail = ok(client, "/api/rates/series/UST_PAR_10Y")
    assert (detail["tenor_months"], detail["unit"], detail["observation_count"]) == (
        120,
        "percent",
        4,
    )
    obs = ok(client, "/api/rates/series/UST_PAR_10Y/observations")["items"]
    assert [(o["obs_date"], o["value"], o["revisions"]) for o in obs] == [
        ("2024-03-01", "4.13", 1),
        ("2024-03-04", "4.14", 1),
        ("2024-03-05", "4.16", 2),
    ]
    every = ok(
        client, "/api/rates/series/UST_PAR_10Y/observations", revisions="all", start="2024-03-05"
    )["items"]
    assert [o["value"] for o in every] == ["4.15", "4.16"]
    assert ok(client, "/api/rates/series/GOC_BMK_LONG")["tenor_months"] is None


def test_fx_direction_is_explicit_and_never_inverted(client: TestClient) -> None:
    page = ok(client, "/api/fx/rates", base="usd", quote="cad", sort="-date")
    first = page["items"][0]
    assert (first["base"], first["quote"], first["direction"]) == ("USD", "CAD", "CAD per 1 USD")
    assert (first["obs_date"], first["rate"], first["provider_series_id"]) == (
        "2024-03-05",
        "1.3571",
        "FXUSDCAD",
    )
    assert ok(client, "/api/fx/rates", base="CAD", quote="USD")["total"] == 0
    err(client, "/api/fx/rates", 422, "validation_error", base="USD", quote="USD")
    err(client, "/api/fx/rates", 422, "validation_error", base="USD")
    err(client, "/api/fx/rates", 422, "validation_error", base="US", quote="CAD")


# --------------------------------------------------------------------------- reference


def test_reference_versions_and_reproducible_records(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    page = ok(client, "/api/reference/datasets", kind="country_risk")
    assert [(d["version_no"], d["is_current"]) for d in page["items"]] == [(2, True), (1, False)]
    v1 = next(d for d in page["items"] if d["version_no"] == 1)
    assert v1["superseded_by_id"] == str(seeded["crp_v2"])
    current = ok(client, "/api/reference/datasets", current_only=True)
    assert {d["kind"] for d in current["items"]} == {"country_risk", "rating_spreads"}
    assert current["total"] == 2
    detail = ok(client, f"/api/reference/datasets/{seeded['crp_v2']}")
    assert detail["supersedes_id"] == str(seeded["crp_v1"])
    assert detail["reason"] == "Corrected DE transcription"
    assert detail["original_filename"] == "crp_fix.csv" and "stored_path" not in detail

    old = ok(client, f"/api/reference/datasets/{seeded['crp_v1']}/records", record_key="DE")
    new = ok(client, f"/api/reference/datasets/{seeded['crp_v2']}/records", record_key="DE")
    crp_old = old["items"][0]["fields"]["country_risk_premium"]
    assert (crp_old["value"], crp_old["unit"]) == ("0.70", "percent")
    assert new["items"][0]["fields"]["country_risk_premium"]["value"] == "0.80"
    records = ok(
        client,
        f"/api/reference/datasets/{seeded['crp_v1']}/records",
        field=["total_erp", "moodys_rating"],
    )
    assert [r["record_key"] for r in records["items"]] == ["CA", "DE"]  # file order
    assert set(records["items"][0]["fields"]) == {"total_erp", "moodys_rating"}
    assert records["items"][0]["fields"]["moodys_rating"]["value_text"] == "Aaa"
    paged = ok(client, f"/api/reference/datasets/{seeded['crp_v1']}/records", limit=1, offset=1)
    assert (paged["total"], paged["items"][0]["record_key"]) == (2, "DE")
    spreads = ok(client, "/api/reference/datasets", kind="rating_spreads")["items"][0]
    assert spreads["variant"] == "large_cap" and spreads["region"] is None
    err(client, "/api/reference/datasets", 422, "validation_error", kind="betas")


# --------------------------------------------------------------------------- query behaviour


def _statements_for(client: TestClient, engine: Engine, url: str, **params: Any) -> int:
    count = 0

    def counter(*_: object) -> None:
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", counter)
    try:
        ok(client, url, **params)
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    return count


def test_query_count_does_not_grow_with_page_size(
    client: TestClient, seeded: dict[str, Any], api_engine: Engine
) -> None:
    for url, params in (
        (f"/api/datasets/{seeded['reported']}/values", {}),
        (
            f"/api/listings/{seeded['tst']}/prices",
            {"provider": "manual_import", "close_basis": "raw"},
        ),
        ("/api/rates/series", {}),
        ("/api/companies", {}),
        (f"/api/reference/datasets/{seeded['crp_v1']}/records", {}),
    ):
        small = _statements_for(client, api_engine, url, limit=1, **params)
        large = _statements_for(client, api_engine, url, limit=200, **params)
        assert small == large, (url, small, large)
        assert large <= 20, (url, large)
