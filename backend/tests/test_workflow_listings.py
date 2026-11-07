"""Listing workflow actions (D60; D56, D55): validation, registration, currency declaration.
Synthetic issuers on ``erp_test``; each test rolled back."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from erp_backend.models import AuditEvent, Company, ListingRegistration, Security
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.workflow_helpers import Actor, as_role

REASON = "TSX listing confirmed from the exchange's listed-company directory."


def proposal(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ticker": "NWC",
        "mic": "XTSE",
        "currency": "CAD",
        "security_type": "common_equity",
        "issuer": {"new": {"legal_name": "New Workflow Corp", "country": "CA"}},
        "source": "TSX listed-company directory",
        "reason": REASON,
        "provider_symbols": {"yahoo_chart": "NWC.TO"},
    }
    body.update(changes)
    return body


@pytest.fixture
def analyst(wf_client: TestClient, db: Session) -> Actor:
    return as_role(wf_client, db, "ana", ["analyst"])


def listings(db: Session, ticker: str = "NWC") -> int:
    return db.scalar(select(func.count()).where(Security.ticker == ticker)) or 0


def test_validate_previews_without_writing(analyst: Actor, db: Session) -> None:
    r = analyst.post("/api/listings/validate", json=proposal())
    assert r.status_code == 200, r.text
    check = r.json()
    assert check["ok"] is True and check["issuer"]["mode"] == "new"
    assert check["listing"]["provider_symbols"] == {"yahoo_chart": "NWC.TO"}
    bad = analyst.post("/api/listings/validate", json=proposal(mic="XLON", currency="GBP"))
    assert bad.status_code == 200 and bad.json()["ok"] is False and bad.json()["errors"]
    assert listings(db) == 0
    assert db.scalar(select(func.count()).where(Company.legal_name == "New Workflow Corp")) == 0


def test_register_new_issuer_listing(analyst: Actor, db: Session) -> None:
    r = analyst.post("/api/listings", json=proposal(), key="register-nwc-1")
    assert r.status_code == 201, r.text
    result = r.json()["result"]
    assert result["company_created"] is True
    listing = result["listing"]
    assert (listing["ticker"], listing["exchange_mic"], listing["trading_currency"]) == (
        "NWC",
        "XTSE",
        "CAD",
    )
    assert listing["origin"] == "manual_registration"
    registration = db.get(ListingRegistration, uuid.UUID(result["registration_id"]))
    assert registration is not None and registration.registered_by is not None
    assert registration.reason == REASON
    assert db.scalar(select(func.count()).where(AuditEvent.action == "listing_registered")) == 1
    replay = analyst.post("/api/listings", json=proposal(), key="register-nwc-1")
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json() == r.json()
    duplicate = analyst.post("/api/listings", json=proposal())
    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "action_refused")
    assert listings(db) == 1


def test_register_second_listing_of_an_existing_issuer(analyst: Actor, db: Session) -> None:
    first = analyst.post("/api/listings", json=proposal()).json()["result"]
    r = analyst.post(
        "/api/listings",
        json=proposal(
            mic="XNYS",
            currency="USD",
            issuer={"same_issuer_as": {"ticker": "NWC", "mic": "XTSE"}},
            provider_symbols={},
        ),
    )
    assert r.status_code == 201, r.text
    assert r.json()["result"]["company_id"] == first["company_id"]
    assert r.json()["result"]["company_created"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer": {}},
        {"issuer": {"cik": 320193, "new": {"legal_name": "X", "country": "CA"}}},
        {"security_type": "warrant"},
        {"mic": "TSX"},
        {"currency": "CA"},
        {"reason": "short"},
        {"isin": "not-an-isin"},
        {"provider_symbols": {"Yahoo Chart": "X"}},
        {"extra": True},
    ],
)
def test_listing_request_validation(analyst: Actor, db: Session, changes: dict[str, Any]) -> None:
    r = analyst.post("/api/listings", json=proposal(**changes))
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
    assert listings(db) == 0


def test_refused_registration_writes_nothing(analyst: Actor, db: Session) -> None:
    r = analyst.post("/api/listings", json=proposal(mic="XLON", currency="GBP"))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    assert listings(db) == 0
    assert db.scalar(select(func.count()).where(Company.legal_name == "New Workflow Corp")) == 0
    assert db.scalar(select(func.count()).where(AuditEvent.action == "listing_registered")) == 0


def test_reviewer_and_csrf(wf_client: TestClient, db: Session) -> None:
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    for url in ("/api/listings/validate", "/api/listings"):
        r = reviewer.post(url, json=proposal())
        assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")
    analyst = as_role(wf_client, db, "ana", ["analyst"])
    r = analyst.post("/api/listings", json=proposal(), csrf=None)
    assert r.status_code == 403
    assert listings(db) == 0


def test_declare_currency_once(analyst: Actor, db: Session) -> None:
    company = Company(legal_name="SEC Listing Co")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id, ticker="SLC", exchange_name="Nasdaq", exchange_mic="XNAS"
    )
    db.add(security)
    db.flush()
    url = f"/api/listings/{security.id}/trading-currency"
    lower = analyst.post(url, json={"currency": "usd", "reason": "Nasdaq listing trades in USD."})
    assert lower.status_code == 422
    r = analyst.post(url, json={"currency": "USD", "reason": "Nasdaq listing trades in USD."})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["changed"] is True
    assert r.json()["result"]["listing"]["trading_currency"] == "USD"
    same = analyst.post(url, json={"currency": "USD", "reason": "Confirming the declaration."})
    assert same.status_code == 200 and same.json()["result"]["changed"] is False
    other = analyst.post(url, json={"currency": "CAD", "reason": "Trying to change it now."})
    assert (other.status_code, other.json()["error"]["code"]) == (409, "action_refused")
    minor = analyst.post(url, json={"currency": "GBX", "reason": "Pence are not a currency."})
    assert minor.status_code == 409
    db.refresh(security)
    assert security.trading_currency == "USD"
    events = db.scalar(select(func.count()).where(AuditEvent.action == "listing_currency_set"))
    assert events == 1
    missing = analyst.post(
        f"/api/listings/{uuid.uuid4()}/trading-currency",
        json={"currency": "USD", "reason": "Nasdaq listing trades in USD."},
    )
    assert missing.status_code == 404
