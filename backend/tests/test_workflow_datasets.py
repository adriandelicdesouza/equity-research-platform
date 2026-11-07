"""Dataset lifecycle actions (D60): build, rebuild, verify, lock (pending), discard.

Synthetic SEC-style facts on ``erp_test``; each test rolled back. No network.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from erp_backend.auth.rbac import Permission, Role
from erp_backend.datasets import resolve_conflict
from erp_backend.models import (
    ApiAction,
    AuditEvent,
    Company,
    DataConflict,
    FinancialDataset,
    FinancialValue,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.test_conflicts_db import END, F1, F2, FY
from tests.test_datasets import add_facts
from tests.workflow_helpers import Actor, as_role, grant_pending

REASON = "Original vintage is consistent with the liabilities reported."


@pytest.fixture
def company(db: Session) -> Company:
    c = Company(legal_name="Workflow Co", reporting_currency="USD", accounting_standard="US_GAAP")
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


@pytest.fixture
def analyst(wf_client: TestClient, db: Session) -> Actor:
    return as_role(wf_client, db, "ana", ["analyst"])


def build(actor: Actor, company: Company, **body: Any) -> Any:
    return actor.post(f"/api/companies/{company.id}/datasets/build", json=body)


def versions(db: Session, company: Company) -> list[tuple[int, str]]:
    return [
        (d.version_no, d.status)
        for d in db.scalars(
            select(FinancialDataset)
            .where(FinancialDataset.company_id == company.id)
            .order_by(FinancialDataset.version_no)
        )
    ]


def user_id(db: Session, name: str) -> uuid.UUID:
    return db.scalars(select(User.id).where(User.username == name)).one()


# --------------------------------------------------------------------------- auth + CSRF


def test_unauthenticated_and_csrf_and_origin_are_enforced(
    wf_client: TestClient, db: Session, company: Company
) -> None:
    url = f"/api/companies/{company.id}/datasets/build"
    r = wf_client.post(url, json={}, headers={"Origin": "https://testserver"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "not_authenticated")
    actor = as_role(wf_client, db, "ana", ["analyst"])
    for csrf in (None, "wrong-token"):
        r = actor.post(url, json={}, csrf=csrf)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_failed")
    r = actor.post(url, json={}, origin=None)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "origin_rejected")
    r = actor.post(url, json={}, origin="https://evil.example")
    assert r.status_code == 403
    assert versions(db, company) == []
    assert db.scalar(select(func.count()).select_from(ApiAction)) == 0


def test_reviewer_cannot_change_datasets(
    wf_client: TestClient, db: Session, company: Company
) -> None:
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    r = build(reviewer, company)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")
    assert versions(db, company) == []


# --------------------------------------------------------------------------- build / rebuild


def test_build_creates_a_draft_with_audit_and_action_record(
    analyst: Actor, db: Session, company: Company
) -> None:
    r = build(analyst, company)
    assert r.status_code == 201, r.text
    body = r.json()
    result = body["result"]
    assert (result["created"], result["dataset"]["version_no"], result["dataset"]["status"]) == (
        True,
        1,
        "draft",
    )
    assert result["dataset"]["basis"] == "standardized"
    assert result["dataset"]["mapping_version"] == "v2"
    assert result["conflicts"]["open_material"] == 1
    assert result["validation"] is not None
    record = db.get(ApiAction, uuid.UUID(body["action_id"]))
    assert record is not None and (record.action, record.outcome) == ("dataset.build", "succeeded")
    assert record.user_id == user_id(db, "ana")
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == result["dataset"]["id"])
    ).first()
    assert event is not None and event.user_id == user_id(db, "ana")
    again = build(analyst, company)
    assert again.status_code == 200 and again.json()["result"]["created"] is False
    assert versions(db, company) == [(1, "draft")]


def test_build_retry_with_idempotency_key_acts_once(
    analyst: Actor, db: Session, company: Company
) -> None:
    first = analyst.post(f"/api/companies/{company.id}/datasets/build", json={}, key="build-0001")
    retry = analyst.post(f"/api/companies/{company.id}/datasets/build", json={}, key="build-0001")
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert retry.json() == first.json() and retry.status_code == 201
    other = analyst.post(
        f"/api/companies/{company.id}/datasets/build",
        json={"policy": "first_reported"},
        key="build-0001",
    )
    assert (other.status_code, other.json()["error"]["code"]) == (422, "idempotency_key_reused")
    assert db.scalar(select(func.count()).select_from(ApiAction)) == 1


@pytest.mark.parametrize(
    "body",
    [{"basis": "adjusted"}, {"policy": "newest"}, {"unknown": 1}, {"facts_as_of": "2024-13-01"}],
)
def test_build_validates_the_body(analyst: Actor, company: Company, body: dict[str, Any]) -> None:
    r = build(analyst, company, **body)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")


def test_unknown_company_is_a_recorded_404(analyst: Actor, db: Session) -> None:
    r = analyst.post(f"/api/companies/{uuid.uuid4()}/datasets/build", json={})
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    record = db.get(ApiAction, uuid.UUID(r.json()["error"]["action_id"]))
    assert record is not None and record.outcome == "refused"


def test_rebuild_needs_a_reason_and_keeps_history(
    analyst: Actor, db: Session, company: Company
) -> None:
    build(analyst, company)
    url = f"/api/companies/{company.id}/datasets/rebuild"
    r = analyst.post(url, json={"reason": "short"})
    assert r.status_code == 422
    r = analyst.post(url, json={"reason": "1234567890"})  # long enough, but no words
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    assert versions(db, company) == [(1, "draft")]  # refused: nothing changed
    r = analyst.post(url, json={"reason": "Rebuild after reviewing the inputs."})
    assert r.status_code in (200, 201), r.text
    assert versions(db, company) == [(1, "discarded"), (2, "draft")]


# --------------------------------------------------------------------------- verify


def test_reviewer_can_verify_without_changing_anything(
    analyst: Actor, wf_client: TestClient, db: Session, company: Company
) -> None:
    dataset_id = build(analyst, company).json()["result"]["dataset"]["id"]
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    r = reviewer.post(f"/api/datasets/{dataset_id}/verify")
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["ok"] is True and result["rebuild_matches"] is True
    assert versions(db, company) == [(1, "draft")]


# --------------------------------------------------------------------------- lock (pending)


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_lock_is_pending_approval_for_every_role(
    wf_client: TestClient, db: Session, company: Company, role: str
) -> None:
    builder = as_role(wf_client, db, "builder", ["analyst"])
    dataset_id = build(builder, company).json()["result"]["dataset"]["id"]
    actor = as_role(wf_client, db, f"locker-{role}", [role])
    r = actor.post(f"/api/datasets/{dataset_id}/lock", json={"reason": REASON})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pending_approval")
    assert "P15" in r.json()["error"]["message"]
    assert versions(db, company) == [(1, "draft")]


def test_lock_rules_once_approved(
    analyst: Actor, db: Session, company: Company, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_pending(monkeypatch, Role.ANALYST, Permission.DATASET_LOCK)  # simulated approval
    d1 = build(analyst, company).json()["result"]["dataset"]["id"]
    r = analyst.post(f"/api/datasets/{d1}/lock", json={"reason": REASON})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "action_refused")
    assert "open material conflict" in r.json()["error"]["message"]
    assert versions(db, company) == [(1, "draft")]

    conflict = db.scalars(
        select(DataConflict).where(DataConflict.dataset_id == uuid.UUID(d1))
    ).one()
    first = next(c for c in conflict.candidates if c["role"] == "first_reported")
    resolve_conflict(
        db,
        uuid.UUID(d1),
        conflict.conflict_key,
        "select_candidate",
        reason=REASON,
        candidate_fact_id=first["fact_id"],
    )
    rebuilt = analyst.post(
        f"/api/companies/{company.id}/datasets/rebuild", json={"reason": "Apply the resolution."}
    ).json()["result"]["dataset"]["id"]
    r = analyst.post(f"/api/datasets/{rebuilt}/lock", json={"reason": REASON})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["dataset"]["status"] == "locked"
    for action in ("lock", "discard"):
        again = analyst.post(f"/api/datasets/{rebuilt}/{action}", json={"reason": REASON})
        assert (again.status_code, again.json()["error"]["code"]) == (409, "action_refused")
    assert versions(db, company) == [(1, "discarded"), (2, "locked")]


# --------------------------------------------------------------------------- discard


def test_discard_draft(analyst: Actor, db: Session, company: Company) -> None:
    dataset_id = build(analyst, company).json()["result"]["dataset"]["id"]
    r = analyst.post(f"/api/datasets/{dataset_id}/discard", json={"reason": "Built by mistake."})
    assert r.status_code == 200 and r.json()["result"]["dataset"]["status"] == "discarded"
    count = db.scalar(
        select(func.count()).where(FinancialValue.dataset_id == uuid.UUID(dataset_id))
    )
    assert count == 0
    event = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "dataset_discarded", AuditEvent.entity_id == dataset_id
        )
    ).one()
    assert event.reason == "Built by mistake." and event.user_id == user_id(db, "ana")
    missing = analyst.post(f"/api/datasets/{uuid.uuid4()}/discard", json={"reason": REASON})
    assert missing.status_code == 404
