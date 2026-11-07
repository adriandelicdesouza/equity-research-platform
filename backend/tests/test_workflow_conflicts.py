"""Conflict-resolution and classification actions (D60). Synthetic data on ``erp_test``."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from erp_backend.classification import CONFIRMABLE
from erp_backend.models import (
    AuditEvent,
    Company,
    CompanyClassification,
    ConflictResolution,
    DataConflict,
    FinancialValue,
    Security,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.test_datasets import add_facts
from tests.test_statement_import import FY24, SEC_META, do_ingest, statement
from tests.test_workflow_datasets import build
from tests.workflow_helpers import Actor, as_role

REASON = "Original vintage is consistent with the liabilities reported."


@pytest.fixture
def analyst(wf_client: TestClient, db: Session) -> Actor:
    return as_role(wf_client, db, "ana", ["analyst"])


@pytest.fixture
def sec_company(db: Session) -> Company:
    from tests.test_conflicts_db import END, F1, F2, FY

    c = Company(legal_name="Resolve Co", reporting_currency="USD", accounting_standard="US_GAAP")
    db.add(c)
    db.flush()
    add_facts(
        db,
        c,
        [
            ("Revenues", "1000", *FY, *F1),
            ("Assets", "100123", None, END, *F1),
            ("Liabilities", "60111", None, END, *F1),
            ("StockholdersEquity", "40012", None, END, *F1),
            ("StockholdersEquity", "50012", None, END, *F2),
        ],
    )
    return c


def conflict_of(db: Session, dataset_id: str) -> DataConflict:
    return db.scalars(
        select(DataConflict).where(DataConflict.dataset_id == uuid.UUID(dataset_id))
    ).one()


def resolve(actor: Actor, dataset_id: str, key: str, **body: Any) -> Any:
    return actor.post(f"/api/datasets/{dataset_id}/conflicts/{key}/resolutions", json=body)


def rebuild(actor: Actor, company: Company, basis: str = "standardized") -> str:
    r = actor.post(
        f"/api/companies/{company.id}/datasets/rebuild",
        json={"basis": basis, "reason": "Apply the recorded conflict decision."},
    )
    assert r.status_code in (200, 201), r.text
    return str(r.json()["result"]["dataset"]["id"])


def resolutions(db: Session, company: Company) -> int:
    return db.scalar(select(func.count()).where(ConflictResolution.company_id == company.id)) or 0


# --------------------------------------------------------------------------- conflicts


def test_select_candidate_then_rebuild_resolves(
    analyst: Actor, wf_client: TestClient, db: Session, sec_company: Company
) -> None:
    d1 = build(analyst, sec_company).json()["result"]["dataset"]["id"]
    conflict = conflict_of(db, d1)
    first = next(c for c in conflict.candidates if c["role"] == "first_reported")
    r = resolve(
        analyst,
        d1,
        conflict.conflict_key,
        action="select_candidate",
        reason=REASON,
        candidate_id=first["fact_id"],
    )
    assert r.status_code == 201, r.text
    res = r.json()["result"]["resolution"]
    assert (res["action"], res["candidate_id"], res["candidate_source"]) == (
        "select_candidate",
        first["fact_id"],
        "reported_fact",
    )
    assert conflict_of(db, d1).status == "open"  # the detected conflict is never edited
    d2 = rebuild(analyst, sec_company)
    assert conflict_of(db, d2).status == "resolved"
    equity = db.scalars(
        select(FinancialValue).where(
            FinancialValue.dataset_id == uuid.UUID(d2),
            FinancialValue.line_item_code == "BS.TOTAL_EQUITY",
        )
    ).one()
    assert str(equity.value) == "40012"
    history = wf_client.get(f"/api/companies/{sec_company.id}/conflict-resolutions").json()
    assert history["total"] == 1
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "conflict_resolved")).one()
    assert event.reason == REASON and event.user_id is not None


def test_enter_value_override_and_revoke(analyst: Actor, db: Session, sec_company: Company) -> None:
    d1 = build(analyst, sec_company).json()["result"]["dataset"]["id"]
    key = conflict_of(db, d1).conflict_key
    r = resolve(
        analyst,
        d1,
        key,
        action="enter_value",
        reason=REASON,
        value="45000.50",
        source_reference="Annual report 2024, p. 61",
    )
    assert r.status_code == 201 and r.json()["result"]["resolution"]["value"] == "45000.50"
    d2 = rebuild(analyst, sec_company)
    equity = db.scalars(
        select(FinancialValue).where(
            FinancialValue.dataset_id == uuid.UUID(d2),
            FinancialValue.line_item_code == "BS.TOTAL_EQUITY",
        )
    ).one()
    assert (equity.provenance_kind, str(equity.value)) == ("analyst", "45000.50")
    r = resolve(analyst, d2, key, action="override", reason="Restatement accepted as published.")
    assert r.status_code == 201
    r = resolve(analyst, d2, key, action="revoke", reason="Override withdrawn after review.")
    assert r.status_code == 201
    again = resolve(analyst, d2, key, action="revoke", reason="Withdrawing nothing at all.")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "action_refused")
    assert "No active resolution" in again.json()["error"]["message"]
    assert resolutions(db, sec_company) == 3  # every decision kept, none edited


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"action": "select_candidate", "reason": REASON}, 422),
        ({"action": "enter_value", "reason": REASON, "value": "1"}, 422),
        ({"action": "override", "reason": REASON, "candidate_id": 1}, 422),
        ({"action": "override", "reason": REASON, "value": "1", "source_reference": "x"}, 422),
        ({"action": "enter_value", "reason": REASON, "value": "NaN", "source_reference": "x"}, 422),
        ({"action": "delete", "reason": REASON}, 422),
        ({"action": "override", "reason": "too short"}, 422),
        ({"action": "select_candidate", "reason": REASON, "candidate_id": 999999}, 409),
    ],
)
def test_resolution_requests_are_validated(
    analyst: Actor, db: Session, sec_company: Company, body: dict[str, Any], status: int
) -> None:
    d1 = build(analyst, sec_company).json()["result"]["dataset"]["id"]
    r = resolve(analyst, d1, conflict_of(db, d1).conflict_key, **body)
    assert r.status_code == status, r.text
    assert resolutions(db, sec_company) == 0


def test_unknown_conflict_and_permissions(
    analyst: Actor, wf_client: TestClient, db: Session, sec_company: Company
) -> None:
    d1 = build(analyst, sec_company).json()["result"]["dataset"]["id"]
    key = conflict_of(db, d1).conflict_key
    r = resolve(analyst, d1, "0" * 16, action="override", reason=REASON)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    r = resolve(analyst, d1, "NOT-HEX", action="override", reason=REASON)
    assert r.status_code == 422
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    r = resolve(reviewer, d1, key, action="override", reason=REASON)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")
    r = resolve(analyst, d1, key, action="override", reason=REASON, csrf=None)
    assert r.status_code == 403
    assert resolutions(db, sec_company) == 0


def test_resolution_retry_is_not_recorded_twice(
    analyst: Actor, db: Session, sec_company: Company
) -> None:
    d1 = build(analyst, sec_company).json()["result"]["dataset"]["id"]
    key = conflict_of(db, d1).conflict_key
    url = f"/api/datasets/{d1}/conflicts/{key}/resolutions"
    body = {"action": "override", "reason": REASON}
    first = analyst.post(url, json=body, key="resolve-override-1")
    retry = analyst.post(url, json=body, key="resolve-override-1")
    assert retry.headers.get("Idempotent-Replayed") == "true" and retry.json() == first.json()
    assert resolutions(db, sec_company) == 1


def test_select_imported_or_sec_candidate_by_source(
    analyst: Actor, db: Session, tmp_path: Path
) -> None:
    company = Company(
        legal_name="Dual Source Co",
        reporting_currency="USD",
        accounting_standard="US_GAAP",
        accounting_standard_inferred=False,
    )
    db.add(company)
    db.flush()
    db.add(
        Security(
            company_id=company.id,
            ticker="DSC",
            exchange_name="Nasdaq",
            exchange_mic="XNAS",
            trading_currency="USD",
        )
    )
    add_facts(
        db,
        company,
        [
            (
                "Revenues",
                "1200",
                date(2024, 1, 1),
                date(2024, 12, 31),
                "10-K",
                date(2025, 2, 1),
                "0000000009-25-000001",
            )
        ],
    )
    build(analyst, company)
    data = statement(columns=(FY24,), meta=SEC_META, items=(("IS.REVENUE", "1100"),))
    result = do_ingest(db, data, tmp_path, allow_conflicts=True)
    assert result.dataset is not None
    d = str(result.dataset.dataset.id)
    conflict = conflict_of(db, d)
    imported = next(c for c in conflict.candidates if c.get("source") == "import_value")
    r = resolve(
        analyst,
        d,
        conflict.conflict_key,
        action="select_candidate",
        reason=REASON,
        candidate_id=imported["fact_id"],
        candidate_source="import_value",
    )
    assert r.status_code == 201, r.text
    assert r.json()["result"]["resolution"]["candidate_source"] == "import_value"
    d2 = rebuild(analyst, company, basis="reported")
    revenue = db.scalars(
        select(FinancialValue).where(
            FinancialValue.dataset_id == uuid.UUID(d2),
            FinancialValue.line_item_code == "IS.REVENUE",
        )
    ).one()
    assert (revenue.provenance_kind, str(revenue.value)) == ("manual_import", "1100")


# --------------------------------------------------------------------------- classification


def test_api_classification_choices_match_the_domain() -> None:
    from erp_backend.api.actions import ConfirmClassificationRequest

    field = ConfirmClassificationRequest.model_fields["classification"]
    assert set(field.annotation.__args__) == set(CONFIRMABLE)  # type: ignore[union-attr]


def test_confirm_classification(
    analyst: Actor, wf_client: TestClient, db: Session, sec_company: Company
) -> None:
    url = f"/api/companies/{sec_company.id}/classification/confirm"
    r = analyst.post(
        url,
        json={
            "classification": "non_financial",
            "reason": "Industrial company; no lending or insurance.",
        },
    )
    assert r.status_code == 201, r.text
    result = r.json()["result"]
    assert (result["effective"], result["confirmation"]["kind"]) == ("non_financial", "confirmed")
    r = analyst.post(url, json={"classification": "bank", "reason": "Reclassified: bank charter."})
    assert r.status_code == 201
    rows = db.scalars(
        select(CompanyClassification.classification)
        .where(CompanyClassification.company_id == sec_company.id)
        .order_by(CompanyClassification.id)
    ).all()
    assert list(rows) == ["non_financial", "bank"]  # history kept
    db.refresh(sec_company)
    assert sec_company.financial_classification == "bank"
    assert (
        db.scalar(select(func.count()).where(AuditEvent.action == "classification_confirmed")) == 2
    )
    for body in (
        {"classification": "needs_review", "reason": REASON},
        {"classification": "bank", "reason": "short"},
        {"classification": "bank"},
    ):
        assert analyst.post(url, json=body).status_code == 422
    missing = analyst.post(
        f"/api/companies/{uuid.uuid4()}/classification/confirm",
        json={"classification": "bank", "reason": REASON},
    )
    assert missing.status_code == 404
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    assert reviewer.post(url, json={"classification": "bank", "reason": REASON}).status_code == 403
