"""Versioned datasets against the test database: lifecycle, immutability, verification."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.datasets import (
    BuildResult,
    DatasetError,
    build_dataset,
    discard_dataset,
    lock_dataset,
    stored_canonical,
    verify_dataset,
)
from erp_backend.ingestion.selection import Policy
from erp_backend.models import (
    AuditEvent,
    Company,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    ReportedFact,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2] / "mappings"


def build(db: Session, company: Company, **kw: object) -> BuildResult:
    return build_dataset(db, company, mappings_root=ROOT, mapping_version="v1", **kw)  # type: ignore[arg-type]


def add_facts(db: Session, company: Company, rows: list[tuple]) -> None:  # type: ignore[type-arg]
    fetch_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO source.provider_fetches (id, provider_code, url) "
            "VALUES (:i, 'sec_edgar', 'https://example.invalid/facts')"
        ),
        {"i": fetch_id},
    )
    for concept, value, start, end, form, filed, accn in rows:
        db.add(
            ReportedFact(
                company_id=company.id, taxonomy="us-gaap", concept=concept, unit="USD",
                value=Decimal(value), period_start=start, period_end=end, form=form,
                filed_date=filed, accession_no=accn, fetch_id=fetch_id,
            )
        )  # fmt: skip
    db.flush()


FY23 = (date(2023, 1, 1), date(2023, 12, 31))
FY24 = (date(2024, 1, 1), date(2024, 12, 31))
NINE24 = (date(2024, 1, 1), date(2024, 9, 30))
K24 = ("10-K", date(2025, 2, 1), "0000000001-25-000001")
Q24 = ("10-Q", date(2024, 11, 1), "0000000001-24-000003")
K25 = ("10-K", date(2026, 2, 1), "0000000001-26-000001")


@pytest.fixture
def company(db: Session) -> Company:
    c = Company(
        legal_name="Dataset Test Co", reporting_currency="USD", accounting_standard="US_GAAP"
    )
    db.add(c)
    db.flush()
    add_facts(
        db,
        c,
        [
            ("Revenues", "1000", *FY23, *K24),
            ("Revenues", "1200", *FY24, *K24),
            ("Revenues", "850", *NINE24, *Q24),
            ("Assets", "5000", None, FY24[1], *K24),
            ("PaymentsToAcquirePropertyPlantAndEquipment", "90", *FY24, *K24),
        ],
    )
    return c


def _audits(db: Session, action: str) -> int:
    return db.scalar(select(func.count()).where(AuditEvent.action == action)) or 0


def test_build_stores_periods_values_and_provenance(db: Session, company: Company) -> None:
    result = build(db, company)
    d = result.dataset
    assert result.created and (d.version_no, d.status, d.basis) == (1, "draft", "standardized")
    assert d.facts_as_of == date(2025, 2, 1) and d.selection_policy == "latest_reported"
    assert d.mapping_version == "v1" and d.parent_dataset_id is None
    periods = db.scalars(
        select(FiscalPeriodRow).where(FiscalPeriodRow.company_id == company.id)
    ).all()
    assert sorted((p.fiscal_year, p.period_type) for p in periods) == [
        (2023, "FY"), (2024, "9M"), (2024, "FY"), (2024, "Q4"),
    ]  # fmt: skip
    values = {
        (v.line_item_code, p.fiscal_year, p.period_type): v
        for v, p in db.execute(
            select(FinancialValue, FiscalPeriodRow)
            .join(FiscalPeriodRow, FiscalPeriodRow.id == FinancialValue.period_id)
            .where(FinancialValue.dataset_id == d.id)
        ).all()
    }
    q4 = values[("IS.REVENUE", 2024, "Q4")]
    assert (q4.value, q4.provenance_kind, q4.detail["rule"]) == (
        Decimal("350"),
        "derived",
        "Q4=FY-9M",
    )
    assert len(q4.source_fact_ids) == 2
    capex = values[("CF.CAPEX", 2024, "FY")]
    assert capex.value == Decimal("-90") and capex.sign_multiplier == -1
    assert capex.concept == "PaymentsToAcquirePropertyPlantAndEquipment"
    assert capex.detail["accession"] == K24[2] and capex.detail["form"] == "10-K"
    assert d.value_count == len(values)
    assert _audits(db, "dataset_created") == 1


def test_rebuild_is_byte_identical_and_unchanged_inputs_create_nothing(
    db: Session, company: Company
) -> None:
    d = build(db, company).dataset  # type: ignore[arg-type]
    check = verify_dataset(db, d, mappings_root=ROOT)
    assert check.ok and check.inputs_match and check.mapping_matches
    again = build(db, company)
    assert not again.created and again.dataset.id == d.id
    assert db.scalar(select(func.count()).select_from(FinancialDataset)) == 1


def test_lifecycle_versions_parent_diff_and_discard(db: Session, company: Company) -> None:
    v1 = build(db, company).dataset  # type: ignore[arg-type]
    add_facts(db, company, [("Revenues", "1250", *FY24, *K25)])  # restatement
    with pytest.raises(DatasetError, match="open draft"):
        build(db, company)
    lock_dataset(db, v1.id, mappings_root=ROOT, reason="reviewed")
    assert v1.status == "locked" and v1.locked_at is not None

    v2 = build(db, company).dataset  # type: ignore[arg-type]
    assert (v2.version_no, v2.parent_dataset_id) == (2, v1.id)
    summary = v2.source_summary
    assert summary["parent_changes"]["inputs"] is True
    assert summary["parent_changes"]["mapping"] is False
    # FY revenue restated, and the derived Q4 follows it.
    assert summary["diff_vs_parent"]["changed"] == 2
    assert verify_dataset(db, v1, mappings_root=ROOT).ok  # the locked version still rebuilds

    discard_dataset(db, v2.id, reason="superseded")
    assert v2.status == "discarded" and v2.discarded_at is not None
    assert db.scalar(select(func.count()).where(FinancialValue.dataset_id == v2.id)) == 0
    v3 = build(db, company).dataset  # type: ignore[arg-type]
    assert v3.version_no == 3  # version numbers are never reused
    with pytest.raises(DatasetError, match="discarded"):
        verify_dataset(db, v2, mappings_root=ROOT)
    with pytest.raises(DatasetError, match="only drafts"):
        lock_dataset(db, v1.id, mappings_root=ROOT, reason="again")
    assert _audits(db, "dataset_locked") == 1 and _audits(db, "dataset_discarded") == 1


def test_first_reported_and_cutoff_build_point_in_time_versions(
    db: Session, company: Company
) -> None:
    add_facts(db, company, [("Revenues", "1250", *FY24, *K25)])
    d = build(db, company, policy=Policy.FIRST_REPORTED).dataset  # type: ignore[arg-type]
    fy = db.execute(
        select(FinancialValue.value)
        .join(FiscalPeriodRow, FiscalPeriodRow.id == FinancialValue.period_id)
        .where(
            FinancialValue.dataset_id == d.id,
            FinancialValue.line_item_code == "IS.REVENUE",
            FiscalPeriodRow.period_type == "FY",
            FiscalPeriodRow.fiscal_year == 2024,
        )
    ).scalar_one()
    assert fy == Decimal("1200") and d.selection_policy == "first_reported"
    discard_dataset(db, d.id, reason="test")
    cut = build(db, company, facts_as_of=date(2025, 12, 31)).dataset  # type: ignore[arg-type]
    assert cut.facts_as_of == date(2025, 12, 31)
    assert verify_dataset(db, cut, mappings_root=ROOT).ok


def test_ambiguous_values_block_locking(db: Session, company: Company) -> None:
    add_facts(db, company, [("Revenues", "1201", *FY24, *K24)])  # same filing, other value
    d = build(db, company).dataset  # type: ignore[arg-type]
    with pytest.raises(DatasetError, match="ambiguous"):
        lock_dataset(db, d.id, mappings_root=ROOT, reason="x")
    # The trigger enforces the same rule even without the service.
    with pytest.raises(DBAPIError, match="ambiguous values"), db.begin_nested():
        db.execute(
            text(
                "UPDATE fin.financial_datasets SET status = 'locked', locked_at = now() "
                "WHERE id = :i"
            ),
            {"i": d.id},
        )


def test_tampering_is_detected_and_blocks_locking(db: Session, company: Company) -> None:
    d = build(db, company).dataset  # type: ignore[arg-type]
    db.execute(
        text(
            "DELETE FROM fin.financial_values WHERE dataset_id = :i "
            "AND line_item_code = 'BS.TOTAL_ASSETS'"
        ),
        {"i": d.id},
    )
    check = verify_dataset(db, d, mappings_root=ROOT)
    assert not check.stored_matches and check.rebuild_matches
    with pytest.raises(DatasetError, match="failed verification"):
        lock_dataset(db, d.id, mappings_root=ROOT, reason="x")


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        (
            "UPDATE fin.financial_values SET value = 0 WHERE dataset_id = :i",
            r"never updated|permission denied",
        ),
        ("DELETE FROM fin.financial_values WHERE dataset_id = :i", "immutable"),
        ("UPDATE fin.financial_datasets SET facts_as_of = '2020-01-01' WHERE id = :i", "immutable"),
        ("UPDATE fin.financial_datasets SET status = 'draft' WHERE id = :i", "immutable"),
        ("DELETE FROM fin.financial_datasets WHERE id = :i", r"never deleted|permission denied"),
        (
            "INSERT INTO fin.financial_values (dataset_id, period_id, line_item_code, "
            "mapping_version, status, value, unit, provenance_kind, concept, concept_priority, "
            "sign_multiplier, source_fact_ids, detail) SELECT :i, period_id, 'IS.COGS', 'v1', "
            "'ok', 1, 'USD', 'reported', 'X', 1, 1, '{}', '{}' FROM fin.financial_values "
            "WHERE dataset_id = :i LIMIT 1",
            "immutable",
        ),
    ],
)
def test_locked_datasets_cannot_change(
    db: Session, company: Company, statement: str, error: str
) -> None:
    d = build(db, company).dataset  # type: ignore[arg-type]
    lock_dataset(db, d.id, mappings_root=ROOT, reason="reviewed")
    before = stored_canonical(db, d)
    with pytest.raises(DBAPIError, match=error), db.begin_nested():
        db.execute(text(statement), {"i": d.id})
    assert stored_canonical(db, d) == before


def test_draft_rules_enforced_by_the_database(db: Session, company: Company) -> None:
    d = build(db, company).dataset  # type: ignore[arg-type]
    with pytest.raises(DBAPIError, match="only lifecycle fields"), db.begin_nested():
        db.execute(
            text(
                "UPDATE fin.financial_datasets SET status = 'locked', locked_at = now(), "
                "value_count = 1 WHERE id = :i"
            ),
            {"i": d.id},
        )
    with pytest.raises(DBAPIError, match="remove the values"), db.begin_nested():
        db.execute(
            text(
                "UPDATE fin.financial_datasets SET status = 'discarded', "
                "discarded_at = now() WHERE id = :i"
            ),
            {"i": d.id},
        )
    with pytest.raises(IntegrityError, match="uq_datasets_one_draft"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO fin.financial_datasets SELECT (r).* FROM ("
                "SELECT jsonb_populate_record(d, to_jsonb(d) || jsonb_build_object("
                "'id', gen_random_uuid(), 'version_no', 2)) AS r "
                "FROM fin.financial_datasets d WHERE id = :i) s"
            ),
            {"i": d.id},
        )
    with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
        db.execute(text("UPDATE fin.fiscal_periods SET fiscal_year = 1999"))


def test_company_without_standard_is_rejected(db: Session) -> None:
    c = Company(legal_name="No Standard Co")
    db.add(c)
    db.flush()
    with pytest.raises(DatasetError, match="accounting standard"):
        build(db, c)


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        ("UPDATE fin.financial_values SET value = 0 WHERE dataset_id = :i", "never updated"),
        ("DELETE FROM fin.financial_values WHERE dataset_id = :i", "immutable"),
        ("DELETE FROM fin.financial_datasets WHERE id = :i", "never deleted"),
        ("UPDATE fin.financial_datasets SET value_count = 0 WHERE id = :i", "immutable"),
        ("UPDATE fin.fiscal_periods SET fiscal_year = 1999", "append-only"),
        ("TRUNCATE fin.financial_values", "append-only"),
    ],
)
def test_triggers_bind_the_owner_role_too(owner_engine: Engine, statement: str, error: str) -> None:
    """Even the schema owner cannot alter a locked dataset; only migrations change schema."""
    conn = owner_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        c = Company(
            legal_name="Owner Test Co", reporting_currency="USD", accounting_standard="US_GAAP"
        )
        session.add(c)
        session.flush()
        add_facts(session, c, [("Revenues", "1000", *FY24, *K24)])
        d = build(session, c).dataset
        lock_dataset(session, d.id, mappings_root=ROOT, reason="reviewed")
        with pytest.raises(DBAPIError, match=error), session.begin_nested():
            session.execute(text(statement), {"i": d.id})
    finally:
        session.close()
        trans.rollback()
        conn.close()
