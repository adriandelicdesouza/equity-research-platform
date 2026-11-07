"""Import workflow actions (D60): non-persistent preview, pending-approval ingestion,
hash confirmation, idempotency, archive availability. Synthetic files on ``erp_test``;
the network is blocked for the whole module."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from erp_backend.auth.rbac import Permission, Role
from erp_backend.config import get_settings
from erp_backend.models import (
    ApiAction,
    AuditEvent,
    Company,
    PriceObservation,
    Provider,
    ReferenceDataset,
    Security,
    SourceImport,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.test_statement_import import FY23, FY24, statement
from tests.workflow_helpers import Actor, as_role, grant_pending

PRICES = (
    b"ticker,exchange_mic,date,open,high,low,close,close_basis,volume,currency\n"
    b"TST,XNAS,2024-03-04,10.00,10.50,9.90,10.20,raw,1000,USD\n"
    b"TST,XNAS,2024-03-05,10.20,10.60,10.00,10.40,raw,1200,USD\n"
    b"TST,XNAS,2024-03-06,10.40,10.70,10.30,10.65,raw,900,USD\n"
)
CRP_V1 = (
    b"country_name,country_iso2,country_risk_premium,total_erp\n"
    b"Canada,CA,0.00%,4.60%\nRuritania,DE,0.70%,5.30%\n"
)
CRP_V2 = CRP_V1.replace(b"0.70%,5.30%", b"0.80%,5.40%")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def archive_root(wf_client: TestClient, tmp_path: Path) -> Iterator[Path]:
    patched = get_settings().model_copy(update={"raw_root": tmp_path})
    wf_client.app.dependency_overrides[get_settings] = lambda: patched  # type: ignore[attr-defined]
    yield tmp_path


@pytest.fixture
def issuers(db: Session) -> None:
    stc = Company(
        legal_name="Statement Test Co",
        reporting_currency="CAD",
        accounting_standard="IFRS",
        accounting_standard_inferred=False,
    )
    tst = Company(legal_name="Import Test Co")
    db.add_all([stc, tst])
    db.flush()
    db.add_all(
        [
            Security(
                company_id=stc.id,
                ticker="STC",
                exchange_name="Toronto Stock Exchange",
                exchange_mic="XTSE",
                trading_currency="CAD",
            ),
            Security(
                company_id=tst.id,
                ticker="TST",
                exchange_name="Nasdaq",
                exchange_mic="XNAS",
                trading_currency="USD",
            ),
        ]
    )
    db.flush()


@pytest.fixture
def analyst(wf_client: TestClient, db: Session, archive_root: Path, issuers: None) -> Actor:
    return as_role(wf_client, db, "ana", ["analyst"])


def imports(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(SourceImport)) or 0


def upload(actor: Actor, url: str, data: bytes, key: str | None = None, **params: Any) -> Any:
    return actor.post(url, content=data, params=params, key=key)


STATEMENT = statement(columns=(FY23, FY24))


# --------------------------------------------------------------------------- preview


def test_previews_report_without_persisting(analyst: Actor, db: Session) -> None:
    r = upload(analyst, "/api/imports/statements/validate", STATEMENT, filename="stc.csv")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["file_sha256"] == sha(STATEMENT) and report["summary"]["accepted"] > 0
    assert "stored_path" not in r.text
    r = upload(
        analyst,
        "/api/imports/market/validate",
        PRICES,
        filename="p.csv",
        template="market_prices.v1",
    )
    assert r.json()["summary"]["accepted"] == 3
    r = upload(
        analyst,
        "/api/imports/reference/validate",
        CRP_V1,
        filename="crp.csv",
        template="damodaran_country_risk.v1",
        edition_date="2024-01-05",
    )
    assert r.json()["outcome"] == "new_dataset"
    assert imports(db) == 0
    assert db.scalar(select(func.count()).select_from(ApiAction)) == 0  # previews aren't actions


def test_preview_routes_use_the_read_only_session() -> None:
    from erp_backend.api import import_actions
    from erp_backend.readonly import read_only_db

    for route in import_actions.router.routes:
        if route.path.endswith("/validate"):  # type: ignore[attr-defined]
            calls = {d.call for d in route.dependant.dependencies}  # type: ignore[attr-defined]
            assert read_only_db in calls, route.path  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("url", "params", "status"),
    [
        ("/api/imports/market/validate", {"filename": "p.csv", "template": "nope.v1"}, 422),
        ("/api/imports/market/validate", {"template": "market_prices.v1"}, 422),
        (
            "/api/imports/market/validate",
            {"filename": "../x.csv", "template": "market_prices.v1"},
            422,
        ),
        (
            "/api/imports/reference/validate",
            {
                "filename": "c.csv",
                "edition_date": "2024-01-05",
                "template": "damodaran_country_risk.v1",
                "region": "us",
            },
            422,
        ),
        (
            "/api/imports/reference/validate",
            {
                "filename": "c.csv",
                "edition_date": "2099-01-05",
                "template": "damodaran_country_risk.v1",
            },
            422,
        ),
    ],
)
def test_preview_parameter_validation(
    analyst: Actor, url: str, params: dict[str, Any], status: int
) -> None:
    r = upload(analyst, url, PRICES, **params)
    assert (r.status_code, r.json()["error"]["code"]) == (status, "validation_error")


def test_preview_limits_and_permissions(analyst: Actor, wf_client: TestClient, db: Session) -> None:
    r = analyst.post(
        "/api/imports/market/validate",
        content=b"",
        params={"filename": "p.csv", "template": "market_prices.v1"},
    )
    assert r.status_code in (422,)
    big = b"x" * (20 * 1024 * 1024 + 1)
    r = upload(
        analyst,
        "/api/imports/market/validate",
        big,
        filename="big.csv",
        template="market_prices.v1",
    )
    assert (r.status_code, r.json()["error"]["code"]) == (413, "file_too_large")
    r = upload(analyst, "/api/imports/statements/validate", STATEMENT, filename="s.xlsx")
    assert r.status_code == 200 and "XLSX import is not available" in r.text
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    r = upload(reviewer, "/api/imports/statements/validate", STATEMENT, filename="s.csv")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")


# --------------------------------------------------------------------------- ingestion


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_ingestion_is_pending_approval_for_every_role(
    wf_client: TestClient, db: Session, archive_root: Path, issuers: None, role: str
) -> None:
    actor = as_role(wf_client, db, f"imp-{role}", [role])
    for url, data, params in (
        ("/api/imports/statements", STATEMENT, {}),
        ("/api/imports/market", PRICES, {"template": "market_prices.v1"}),
        (
            "/api/imports/reference",
            CRP_V1,
            {"template": "damodaran_country_risk.v1", "edition_date": "2024-01-05"},
        ),
    ):
        r = upload(
            actor,
            url,
            data,
            filename="f.csv",
            expect_sha256=sha(data),
            source="Synthetic test file",
            **params,
        )
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pending_approval"), url
    assert imports(db) == 0
    assert not any(tmp.is_file() for tmp in archive_root.rglob("*"))


def test_statement_ingestion_once_approved(
    analyst: Actor, db: Session, archive_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_pending(monkeypatch, Role.ANALYST, Permission.DATA_IMPORT)
    params = {"filename": "stc.csv", "source": "Analyst transcription of the annual report"}
    r = upload(analyst, "/api/imports/statements", STATEMENT, expect_sha256="0" * 64, **params)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    assert "not the reviewed" in r.json()["error"]["message"] and imports(db) == 0
    r = upload(
        analyst,
        "/api/imports/statements",
        STATEMENT,
        expect_sha256=sha(STATEMENT),
        key="stmt-import-1",
        **params,
    )
    assert r.status_code == 201, r.text
    result = r.json()["result"]
    assert result["status"] == "complete" and result["inserted"] > 0
    assert result["dataset"]["dataset"]["basis"] == "reported"
    assert "stored_path" not in r.text and str(archive_root) not in r.text
    replay = upload(
        analyst,
        "/api/imports/statements",
        STATEMENT,
        expect_sha256=sha(STATEMENT),
        key="stmt-import-1",
        **params,
    )
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json() == r.json()
    again = upload(
        analyst, "/api/imports/statements", STATEMENT, expect_sha256=sha(STATEMENT), **params
    )
    assert again.status_code == 200 and again.json()["result"]["status"] == "no_new_records"
    assert imports(db) == 1
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "financial_statements_imported")
    ).one()
    assert event.user_id is not None
    stored = db.scalars(select(SourceImport)).one()
    assert stored.imported_by == event.user_id
    assert db.scalars(select(Provider.code).where(Provider.enabled)).all() == []


def test_market_ingestion_once_approved(
    analyst: Actor, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_pending(monkeypatch, Role.ANALYST, Permission.DATA_IMPORT)
    r = upload(
        analyst,
        "/api/imports/market",
        PRICES,
        filename="p.csv",
        template="market_prices.v1",
        expect_sha256=sha(PRICES),
        source="Synthetic exchange export",
    )
    assert r.status_code == 201, r.text
    assert (r.json()["result"]["inserted"], r.json()["result"]["status"]) == (3, "complete")
    assert db.scalar(select(func.count()).select_from(PriceObservation)) == 3
    bad = PRICES + b"TST,XNAS,2024-03-07,1,1,1,-1,raw,1,USD\n"
    r = upload(
        analyst,
        "/api/imports/market",
        bad,
        filename="b.csv",
        template="market_prices.v1",
        expect_sha256=sha(bad),
        source="Synthetic exchange export",
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    assert "--allow-partial" in r.json()["error"]["message"]
    assert db.scalar(select(func.count()).select_from(PriceObservation)) == 3


def test_reference_ingestion_and_supersession_once_approved(
    analyst: Actor, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_pending(monkeypatch, Role.ANALYST, Permission.DATA_IMPORT)
    common = {
        "template": "damodaran_country_risk.v1",
        "edition_date": "2024-01-05",
        "source": "Synthetic Damodaran-format file",
    }
    r = upload(
        analyst,
        "/api/imports/reference",
        CRP_V1,
        filename="c1.csv",
        expect_sha256=sha(CRP_V1),
        **common,
    )
    assert r.status_code == 201 and r.json()["result"]["version_no"] == 1
    r = upload(
        analyst,
        "/api/imports/reference",
        CRP_V2,
        filename="c2.csv",
        expect_sha256=sha(CRP_V2),
        **common,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    assert "never overwrite" in r.json()["error"]["message"]
    r = upload(
        analyst,
        "/api/imports/reference",
        CRP_V2,
        filename="c2.csv",
        expect_sha256=sha(CRP_V2),
        supersede="true",
        reason="Corrected DE transcription",
        **common,
    )
    assert r.status_code == 201 and r.json()["result"]["version_no"] == 2
    versions = db.scalars(select(ReferenceDataset.version_no).order_by(ReferenceDataset.version_no))
    assert list(versions) == [1, 2]


def test_unwritable_archive_is_refused_cleanly(
    wf_client: TestClient,
    db: Session,
    issuers: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readonly_root = tmp_path / "ro"
    readonly_root.mkdir(mode=0o500)
    patched = get_settings().model_copy(update={"raw_root": readonly_root})
    wf_client.app.dependency_overrides[get_settings] = lambda: patched  # type: ignore[attr-defined]
    grant_pending(monkeypatch, Role.ANALYST, Permission.DATA_IMPORT)
    actor = as_role(wf_client, db, "ana", ["analyst"])
    r = upload(
        actor,
        "/api/imports/market",
        PRICES,
        filename="p.csv",
        template="market_prices.v1",
        expect_sha256=sha(PRICES),
        source="Synthetic exchange export",
    )
    assert (r.status_code, r.json()["error"]["code"]) == (503, "archive_unavailable")
    assert str(readonly_root) not in r.text
    assert imports(db) == 0
