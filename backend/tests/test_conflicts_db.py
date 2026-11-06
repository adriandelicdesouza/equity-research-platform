"""Conflict workflow against the test database: lock blocking, resolutions, audit, history."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.datasets import (
    DatasetError,
    build_dataset,
    discard_dataset,
    lock_dataset,
    rebuild_draft,
    resolve_conflict,
    verify_dataset,
)
from erp_backend.models import (
    AuditEvent,
    Company,
    ConflictResolution,
    DataConflict,
    FinancialDataset,
    FinancialValue,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .test_datasets import add_facts

ROOT = Path(__file__).resolve().parents[2] / "mappings"
END = date(2024, 12, 31)
FY = (date(2024, 1, 1), END)
F1 = ("10-K", date(2025, 2, 1), "0000000001-25-000001")
F2 = ("10-K", date(2026, 2, 1), "0000000001-26-000001")
REASON = "Restated equity is inconsistent with unrestated liabilities; use original vintage."


def opts() -> dict[str, object]:
    return {"mappings_root": ROOT, "mapping_version": "v2"}


@pytest.fixture
def company(db: Session) -> Company:
    c = Company(legal_name="Conflict Co", reporting_currency="USD", accounting_standard="US_GAAP")
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
            ("StockholdersEquity", "50012", None, END, *F2),  # later partial restatement
        ],
    )
    return c


def _conflict(db: Session, dataset_id: object, code: str = "BS.TOTAL_EQUITY") -> DataConflict:
    return db.scalars(
        select(DataConflict).where(
            DataConflict.dataset_id == dataset_id, DataConflict.line_item_code == code
        )
    ).one()


def _value(db: Session, dataset_id: object, code: str) -> FinancialValue:
    return db.scalars(
        select(FinancialValue).where(
            FinancialValue.dataset_id == dataset_id, FinancialValue.line_item_code == code
        )
    ).one()


def test_detected_conflict_is_stored_audited_and_blocks_locking(
    db: Session, company: Company
) -> None:
    d = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    c = _conflict(db, d.id)
    assert (c.kind, c.status, c.is_material, c.resolution_id) == (
        "mixed_vintage",
        "open",
        True,
        None,
    )
    assert [x["accession"] for x in c.candidates] == [F1[2], F2[2]]
    assert c.relative_difference > Decimal("0.19") and c.threshold > 0
    assert d.source_summary["conflicts"]["open_material"] == 1
    detected = db.scalars(select(AuditEvent).where(AuditEvent.action == "conflicts_detected")).one()
    assert detected.after["open_material"] == [c.conflict_key]  # type: ignore[index]
    with pytest.raises(DatasetError, match="open material conflict"):
        lock_dataset(db, d.id, mappings_root=ROOT, reason="try to lock")
    with pytest.raises(DBAPIError, match="open material conflicts"), db.begin_nested():
        db.execute(
            text("UPDATE fin.financial_datasets SET status='locked', locked_at=now() WHERE id=:i"),
            {"i": d.id},
        )
    assert verify_dataset(db, d, mappings_root=ROOT).ok  # conflicts are in the verified bytes


def test_resolution_rebuilds_a_resolved_lockable_draft_with_full_history(
    db: Session, company: Company
) -> None:
    d1 = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    c1 = _conflict(db, d1.id)
    first = next(x for x in c1.candidates if x["role"] == "first_reported")
    res = resolve_conflict(
        db, d1.id, c1.conflict_key, "select_candidate", reason=REASON,
        candidate_fact_id=first["fact_id"],
    )  # fmt: skip
    d2 = rebuild_draft(db, company, reason="apply conflict resolution", **opts()).dataset  # type: ignore[arg-type]
    assert (d1.status, d2.version_no, d2.resolution_cutoff_id) == ("discarded", 2, res.id)
    c2 = _conflict(db, d2.id)
    assert (c2.status, c2.resolution_id, c2.conflict_key) == ("resolved", res.id, c1.conflict_key)
    equity = _value(db, d2.id, "BS.TOTAL_EQUITY")
    assert equity.value == Decimal("40012") and equity.source_fact_ids == [first["fact_id"]]
    assert equity.detail["resolution"]["id"] == res.id
    # The discarded draft keeps its conflict record (history), its values are gone.
    assert _conflict(db, d1.id).status == "open"
    assert db.scalar(select(func.count()).where(FinancialValue.dataset_id == d1.id)) == 0
    # Audit: the original discrepancy, the analyst's action and reason.
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "conflict_resolved")).one()
    assert event.reason == REASON and event.after["resolution_id"] == res.id  # type: ignore[index]
    assert [x["value"] for x in event.before["candidates"]] == ["40012", "50012"]  # type: ignore[index]
    lock_dataset(db, d2.id, mappings_root=ROOT, reason="reviewed after resolution")
    assert d2.status == "locked"


def test_later_resolutions_never_change_earlier_datasets(db: Session, company: Company) -> None:
    d1 = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    key = _conflict(db, d1.id).conflict_key
    resolve_conflict(db, d1.id, key, "override", reason="Restatement accepted as published.")
    d2 = rebuild_draft(db, company, reason="apply override", **opts()).dataset  # type: ignore[arg-type]
    assert _conflict(db, d2.id).status == "overridden"
    lock_dataset(db, d2.id, mappings_root=ROOT, reason="reviewed with documented override")
    # A newer decision is recorded; the locked version still verifies with its own cutoff.
    resolve_conflict(db, d2.id, key, "revoke", reason="Override withdrawn after review.")
    assert verify_dataset(db, d2, mappings_root=ROOT).ok
    d3 = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    assert (d3.version_no, d3.parent_dataset_id) == (3, d2.id)
    assert _conflict(db, d3.id).status == "open"  # revoked → open again


@pytest.mark.parametrize(
    ("action", "kwargs", "error"),
    [
        ("override", {"reason": "short"}, "meaningful reason"),
        ("override", {"reason": "1234567890123"}, "meaningful reason"),
        ("select_candidate", {"reason": REASON, "candidate_fact_id": 999999}, "not a candidate"),
        ("enter_value", {"reason": REASON, "value": Decimal("1")}, "source reference"),
        ("revoke", {"reason": REASON}, "No active resolution"),
        ("approve", {"reason": REASON}, "Unknown action"),
    ],
)
def test_invalid_resolutions_are_rejected(
    db: Session, company: Company, action: str, kwargs: dict[str, object], error: str
) -> None:
    d = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    key = _conflict(db, d.id).conflict_key
    with pytest.raises(DatasetError, match=error):
        resolve_conflict(db, d.id, key, action, **kwargs)  # type: ignore[arg-type]
    assert db.scalar(select(func.count()).select_from(ConflictResolution)) == 0


def test_within_filing_conflicts_cannot_be_overridden(db: Session, company: Company) -> None:
    add_facts(db, company, [("Revenues", "1001", *FY, *F1)])
    d = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    c = _conflict(db, d.id, "IS.REVENUE")
    assert c.kind == "within_filing" and c.is_material
    with pytest.raises(DatasetError, match="no usable value"):
        resolve_conflict(db, d.id, c.conflict_key, "override", reason=REASON)
    resolve_conflict(
        db, d.id, c.conflict_key, "enter_value", reason="Revenue per audited 10-K statement.",
        value=Decimal("1000"), source_reference="10-K FY2024, income statement",
    )  # fmt: skip
    d2 = rebuild_draft(db, company, reason="apply entered value", **opts()).dataset  # type: ignore[arg-type]
    revenue = _value(db, d2.id, "IS.REVENUE")
    assert (revenue.provenance_kind, revenue.value, revenue.status) == (
        "analyst",
        Decimal("1000"),
        "ok",
    )


def test_database_rejects_weak_resolutions_and_changes(
    db: Session, company: Company, owner_engine: Engine
) -> None:
    d = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    key = _conflict(db, d.id).conflict_key
    with pytest.raises(DBAPIError, match="reason_meaningful"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO fin.conflict_resolutions (company_id, conflict_key, action, reason, "
                "detected_in_dataset_id, line_item_code, period_type, period_start, period_end) "
                "VALUES (:c, :k, 'override', 'ok', :d, 'BS.TOTAL_EQUITY', 'FY', :s, :e)"
            ),
            {"c": company.id, "k": key, "d": d.id, "s": FY[0], "e": END},
        )
    resolve_conflict(db, d.id, key, "override", reason="Restatement accepted as published.")
    for statement, error in (
        ("UPDATE fin.conflict_resolutions SET reason = 'changed afterwards'", "append-only|denied"),
        ("DELETE FROM fin.conflict_resolutions", "append-only|denied"),
        ("UPDATE fin.data_conflicts SET status = 'resolved'", "never updated|denied"),
        ("DELETE FROM fin.data_conflicts", "never updated|denied"),
    ):
        with pytest.raises(DBAPIError, match=error), db.begin_nested():
            db.execute(text(statement))


def test_discard_keeps_conflicts_and_identical_rebuilds_are_deterministic(
    db: Session, company: Company
) -> None:
    d1 = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    discard_dataset(db, d1.id, reason="testing rebuild determinism")
    d2 = build_dataset(db, company, **opts()).dataset  # type: ignore[arg-type]
    assert d2.content_sha256 == d1.content_sha256 and d2.version_no == 2
    assert _conflict(db, d1.id).conflict_key == _conflict(db, d2.id).conflict_key
    assert db.scalar(select(func.count()).select_from(FinancialDataset)) == 2
