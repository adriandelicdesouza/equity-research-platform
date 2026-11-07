"""Financial-statement file import (D57): template, validation, ingestion, dataset lifecycle.

All data is synthetic and exists only inside the disposable ``erp_test`` database, in a
transaction rolled back after each test. No network access.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend import cli
from erp_backend.datasets import (
    IMPORT_BASIS,
    DatasetError,
    build_dataset,
    build_import_dataset,
    lock_dataset,
    rebuild_draft,
    resolve_conflict,
    stored_canonical,
    verify_dataset,
)
from erp_backend.ingestion.selection import Policy
from erp_backend.market.tabular import read_csv
from erp_backend.models import (
    AuditEvent,
    Company,
    DataConflict,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    Security,
    SourceImport,
    StatementImportValue,
    ValidationResult,
)
from erp_backend.standardization import imported as imp
from erp_backend.standardization import mapping as mapping_mod
from erp_backend.standardization.mapping import LineItem, MappingSet
from erp_backend.standardization.periods import FiscalPeriod
from erp_backend.statements import template as tpl
from erp_backend.statements.file_import import (
    ImportRefusedError,
    IngestResult,
    ValidationReport,
    ingest,
    load_import_file,
    validate,
)
from erp_backend.statements.template import Status
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .test_datasets import add_facts

ROOT = Path(__file__).resolve().parents[2] / "mappings"
TODAY = date(2025, 6, 30)
REASON = "Imported annual report figure verified against the printed statement."
META = {
    "template": "financial_statements.v1",
    "issuer_ticker": "STC",
    "issuer_mic": "XTSE",
    "currency": "CAD",
    "scale": "thousands",
    "share_scale": "thousands",
    "accounting_standard": "IFRS",
    "source_document": "Synthetic test annual report 2024",
}
FY23 = ("FY2023", "FY", "2023", "2023-01-01", "2023-12-31")
FY24 = ("FY2024", "FY", "2024", "2024-01-01", "2024-12-31")
Q1_24 = ("Q1 2024", "Q1", "2024", "2024-01-01", "2024-03-31")
H1_24 = ("H1 2024", "H1", "2024", "2024-01-01", "2024-06-30")
ITEMS = (
    ("IS.REVENUE", "1000", "1100"),
    ("IS.COGS", "600", "650"),
    ("IS.GROSS_PROFIT", "400", "450"),
    ("BS.CASH", "500", "600"),
    ("BS.TOTAL_ASSETS", "5000", "5500"),
    ("BS.TOTAL_LIABILITIES", "3000", "3200"),
    ("BS.TOTAL_EQUITY", "2000", "2300"),
    ("IS.SHARES_BASIC_WA", "100", "100"),
    ("IS.EPS_BASIC", "1.25", "1.40"),
)


def statement(
    items: tuple[tuple[str, ...], ...] = ITEMS,
    *,
    meta: dict[str, str] | None = None,
    columns: tuple[tuple[str, ...], ...] = (FY23, FY24),
    page: bool = False,
    drop: tuple[str, ...] = (),
    extra_rows: tuple[tuple[str, ...], ...] = (),
) -> bytes:
    values = {**META, **(meta or {})}
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["item", *(c[0] for c in columns), *(["source_page"] if page else [])])
    for k, v in values.items():
        if k not in drop:
            w.writerow([k, v])
    for i, key in enumerate(tpl.PERIOD_KEYS):
        if key not in drop:
            w.writerow([key, *(c[i + 1] for c in columns)])
    for row in extra_rows:
        w.writerow(row)
    for item in items:
        w.writerow(item)
    return out.getvalue().encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def mapping() -> MappingSet:
    return mapping_mod.load(ROOT, "v2")


@pytest.fixture
def issuer(db: Session) -> Company:
    company = Company(
        legal_name="Statement Test Co",
        reporting_currency="CAD",
        accounting_standard="IFRS",
        accounting_standard_inferred=False,
    )
    db.add(company)
    db.flush()
    db.add(
        Security(
            company_id=company.id,
            ticker="STC",
            exchange_name="Toronto Stock Exchange",
            exchange_mic="XTSE",
            trading_currency="CAD",
        )
    )
    db.flush()
    return company


def check(db: Session, data: bytes, name: str = "statements.csv") -> ValidationReport:
    return validate(
        db, filename=name, data=data, mappings_root=ROOT, default_mapping_version="v2",
        today=TODAY,
    )  # fmt: skip


def do_ingest(db: Session, data: bytes, root: Path, **kw: object) -> IngestResult:
    args: dict[str, object] = {
        "filename": "statements.csv",
        "data": data,
        "source_name": "Analyst transcription of the annual report",
        "expected_sha256": sha(data),
        "raw_root": root,
        "mappings_root": ROOT,
        "default_mapping_version": "v2",
        "today": TODAY,
    }
    args.update(kw)
    return ingest(db, **args)  # type: ignore[arg-type]


def by_status(report: ValidationReport, status: Status) -> list[tpl.Record]:
    return [r for r in report.records if r.status is status]


def issue_codes(report: ValidationReport) -> set[str]:
    return {i.code for r in report.records for i in r.issues}


def counts(db: Session) -> tuple[int, ...]:
    return tuple(
        db.scalar(select(func.count()).select_from(m)) or 0
        for m in (SourceImport, StatementImportValue, FinancialDataset, AuditEvent)
    )


def value_of(db: Session, dataset_id: uuid.UUID, code: str, end: date) -> FinancialValue:
    return db.scalars(
        select(FinancialValue)
        .join(FiscalPeriodRow, FiscalPeriodRow.id == FinancialValue.period_id)
        .where(
            FinancialValue.dataset_id == dataset_id,
            FinancialValue.line_item_code == code,
            FiscalPeriodRow.end_date == end,
            FiscalPeriodRow.period_type.in_(("FY", "Q1", "Q2", "Q3")),
        )
    ).one()


# --------------------------------------------------------------------------- template (pure)


def parse(data: bytes) -> tpl.ParsedFile:
    return tpl.parse_grid(read_csv(data))


def test_well_formed_grid_parses_metadata_and_periods() -> None:
    parsed = parse(statement(page=True, items=(("IS.REVENUE", "1000", "1100", "45"),)))
    assert parsed.errors == []
    assert parsed.metadata is not None
    assert (parsed.metadata.scale, parsed.metadata.share_scale) == (3, 3)
    assert [c.label for c in parsed.columns] == ["FY2023", "FY2024"]
    assert parsed.columns[1].end == date(2024, 12, 31)
    assert parsed.page_index == 3


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"line_item,FY2024\nIS.REVENUE,1\n", "first header cell"),
        (statement(drop=("currency",)), "Missing metadata: currency"),
        (statement(meta={"scale": "lakhs"}), "scale 'lakhs'"),
        (statement(meta={"accounting_standard": "JGAAP"}), "accounting_standard"),
        (statement(meta={"currency": "cad"}), "ISO 4217"),
        (statement(meta={"template": "financial_statements.v0"}), "this importer reads"),
        (statement(drop=("period_end",)), "Missing period rows: period_end"),
        (statement(columns=(("X", "FY5", "2024", "2024-01-01", "2024-12-31"),)), "period_type"),
        (statement(columns=(("X", "FY", "24", "2024-01-01", "2024-12-31"),)), "4-digit year"),
        (statement(columns=(("X", "FY", "2024", "2024-01-01", "31/12/2024"),)), "YYYY-MM-DD"),
        (statement(columns=(("X", "FY", "2024", "2024-01-01", "2024-06-30"),)), "182 days"),
        (statement(columns=(FY24, ("Again", *FY24[1:]))), "more than one column"),
        (statement(columns=(FY24, FY24)), "Duplicate column labels"),
        (statement(extra_rows=(("currency", "USD"),)), "given twice"),
        (statement(extra_rows=(("", "5", "6"),)), "values but no item"),
        (statement(extra_rows=(("IS.REVENUE", "1", "2", "3"),)), "the header has 3"),
        (statement(items=()), "No line-item rows"),
    ],
)
def test_malformed_grids_are_file_errors(data: bytes, fragment: str) -> None:
    errors = parse(data).errors
    assert any(fragment in e for e in errors), errors


def records_of(data: bytes, mapping: MappingSet, today: date = TODAY) -> list[tpl.Record]:
    parsed = parse(data)
    assert parsed.errors == []
    return tpl.records(parsed, mapping, today)


def test_scale_units_and_per_share_handling(mapping: MappingSet) -> None:
    recs = records_of(
        statement(meta={"scale": "millions", "share_scale": "thousands"}, columns=(FY24,),
                  items=(("IS.REVENUE", "1.5"), ("IS.SHARES_BASIC_WA", "250"),
                         ("IS.EPS_BASIC", "0.42"))),
        mapping,
    )  # fmt: skip
    got = {r.item.code: (r.value, r.scale, r.unit) for r in recs if r.item}
    assert got["IS.REVENUE"] == (Decimal("1500000"), 6, "CAD")
    assert got["IS.SHARES_BASIC_WA"] == (Decimal("250000"), 3, "shares")
    assert got["IS.EPS_BASIC"] == (Decimal("0.42"), 0, "CAD/shares")
    assert all(r.status is Status.ACCEPTED for r in recs)


def test_mapping_by_code_label_unknown_and_derived_only(mapping: MappingSet) -> None:
    recs = records_of(
        statement(columns=(FY24,), items=(("Cost of revenue", "5"), ("IS.NOT_A_CODE", "1"),
                                          ("Turnover", "2"), ("CF.FCF", "3"))),
        mapping,
    )  # fmt: skip
    by_key = {r.key: r for r in recs}
    assert by_key["Cost of revenue"].item is not None
    assert by_key["Cost of revenue"].item.code == "IS.COGS"
    assert by_key["Cost of revenue"].issues[0].code == "mapped_by_label"
    assert by_key["IS.NOT_A_CODE"].status is Status.UNMAPPED
    assert by_key["Turnover"].status is Status.UNMAPPED
    assert by_key["CF.FCF"].status is Status.REJECTED
    assert by_key["CF.FCF"].issues[0].code == "derived_only"


def test_label_naming_several_items_is_ambiguous(mapping: MappingSet) -> None:
    twin = LineItem("IS.REVENUE_TWIN", "Revenue", "IS", "duration", "flow", "natural")
    items = dict(mapping.line_items) | {twin.code: twin}
    doubled = MappingSet(mapping.version, mapping.sha256, items, mapping.candidates)
    (rec,) = records_of(statement(columns=(FY24,), items=(("revenue", "7"),)), doubled)
    assert rec.status is Status.AMBIGUOUS
    assert rec.issues[0].code == "ambiguous_label"


@pytest.mark.parametrize(
    ("item", "cell", "code"),
    [
        ("IS.REVENUE", "1,000", "invalid_number"),
        ("IS.REVENUE", "(500)", "invalid_number"),
        ("IS.REVENUE", "1e3", "invalid_number"),
        ("IS.REVENUE", "$12", "invalid_number"),
        ("IS.SHARES_BASIC_WA", "-5", "negative_count"),
    ],
)
def test_invalid_values_are_rejected(mapping: MappingSet, item: str, cell: str, code: str) -> None:
    (rec,) = records_of(statement(columns=(FY24,), items=((item, cell),)), mapping)
    assert rec.status is Status.REJECTED
    assert rec.issues[-1].code == code


def test_negative_expense_is_a_warning_not_a_rejection(mapping: MappingSet) -> None:
    (rec,) = records_of(statement(columns=(FY24,), items=(("IS.COGS", "-5"),)), mapping)
    assert rec.status is Status.ACCEPTED
    assert rec.issues[0].code == "negative_expense"


def test_balance_in_ytd_column_and_future_period_are_rejected(mapping: MappingSet) -> None:
    recs = records_of(
        statement(columns=(FY24, H1_24), items=(("BS.CASH", "1", "2"), ("IS.REVENUE", "3", "4"))),
        mapping,
        today=date(2024, 12, 30),
    )
    got = {
        (r.key, r.column.label): (r.status, r.issues[-1].code if r.issues else None) for r in recs
    }
    assert got == {
        ("BS.CASH", "FY2024"): (Status.REJECTED, "future_period"),
        ("BS.CASH", "H1 2024"): (Status.REJECTED, "balance_in_ytd_column"),
        ("IS.REVENUE", "FY2024"): (Status.REJECTED, "future_period"),
        ("IS.REVENUE", "H1 2024"): (Status.ACCEPTED, None),
    }


def test_in_file_duplicates_and_in_file_conflicts(mapping: MappingSet) -> None:
    recs = records_of(
        statement(columns=(FY24,), items=(("IS.REVENUE", "10"), ("Revenue", "10"),
                                          ("IS.COGS", "4"), ("IS.COGS", "5"))),
        mapping,
    )  # fmt: skip
    statuses = [(r.key, r.status) for r in recs]
    assert statuses == [
        ("IS.REVENUE", Status.ACCEPTED),
        ("Revenue", Status.DUPLICATE),
        ("IS.COGS", Status.AMBIGUOUS),
        ("IS.COGS", Status.AMBIGUOUS),
    ]


def test_blank_cells_produce_no_records(mapping: MappingSet) -> None:
    recs = records_of(statement(items=(("IS.REVENUE", "", "5"), ("IS.COGS", "", ""))), mapping)
    assert [(r.key, r.column.label) for r in recs] == [("IS.REVENUE", "FY2024")]


# --------------------------------------------------------------------------- validation (DB)


def test_valid_file_validates_without_persisting(db: Session, issuer: Company) -> None:
    before = counts(db)
    report = check(db, statement())
    assert report.file_errors == ()
    assert report.company_id == issuer.id
    assert report.summary["accepted"] == len(ITEMS) * 2
    assert report.sec_compared is False  # no SEC facts for this issuer
    assert counts(db) == before


@pytest.mark.parametrize(
    ("meta", "fragment"),
    [
        ({"issuer_ticker": "NOPE"}, "No listing NOPE XTSE"),
        ({"currency": "USD"}, "never converted"),
        ({"accounting_standard": "US_GAAP"}, "differs from the company's declared IFRS"),
        ({"mapping_version": "v9"}, "mapping_version"),
    ],
)
def test_issuer_currency_standard_and_mapping_errors(
    db: Session, issuer: Company, meta: dict[str, str], fragment: str
) -> None:
    report = check(db, statement(meta=meta))
    assert any(fragment in e for e in report.file_errors), report.file_errors
    assert report.records == ()


def test_inferred_standard_mismatch_is_only_a_warning(db: Session, issuer: Company) -> None:
    issuer.accounting_standard_inferred = True
    db.flush()
    report = check(db, statement(meta={"accounting_standard": "US_GAAP"}))
    assert report.file_errors == ()
    assert any("inferred IFRS" in w for w in report.file_warnings)


def test_company_without_reporting_currency_is_refused(db: Session, issuer: Company) -> None:
    issuer.reporting_currency = None
    db.flush()
    report = check(db, statement())
    assert any("no declared reporting currency" in e for e in report.file_errors)


def test_xlsx_is_refused_until_approved(db: Session, issuer: Company) -> None:
    report = check(db, b"PK\x03\x04", name="statements.xlsx")
    assert "owner's approval" in report.file_errors[0]


def test_fiscal_year_label_must_match_period(db: Session, issuer: Company) -> None:
    wrong = ("FY", "FY", "2025", "2024-01-01", "2024-12-31")
    report = check(db, statement(columns=(wrong,), items=(("IS.REVENUE", "1"),)))
    assert any("not FY 2025" in e for e in report.file_errors)


def test_quarters_need_a_known_fiscal_year(db: Session, issuer: Company) -> None:
    report = check(db, statement(columns=(Q1_24,), items=(("IS.REVENUE", "1"),)))
    assert any("cannot be placed" in e for e in report.file_errors)
    report = check(
        db, statement(columns=(FY24, Q1_24, H1_24), items=(("IS.REVENUE", "9", "2", "4"),))
    )
    assert report.file_errors == ()
    assert report.summary["accepted"] == 3


def test_period_type_must_match_the_calendar(db: Session, issuer: Company) -> None:
    mislabeled = ("Q2?", "Q2", "2024", "2024-01-01", "2024-03-31")
    report = check(db, statement(columns=(FY24, mislabeled), items=(("IS.REVENUE", "9", "2"),)))
    assert any("is Q1 of fiscal year 2024, not Q2 2024" in e for e in report.file_errors)


def test_fiscal_year_overlapping_stored_calendar_is_refused(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    do_ingest(db, statement(), tmp_path)
    shifted = ("FY2024b", "FY", "2024", "2023-07-01", "2024-06-30")
    report = check(db, statement(columns=(shifted,), items=(("IS.REVENUE", "1"),)))
    assert any("overlaps the company's stored fiscal year" in e for e in report.file_errors)


# --------------------------------------------------------------------------- ingestion


def test_ingest_requires_reviewed_hash_and_source(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    data = statement()
    before = counts(db)
    with pytest.raises(ImportRefusedError, match="not the reviewed"):
        do_ingest(db, data, tmp_path, expected_sha256="0" * 64)
    with pytest.raises(ImportRefusedError, match="source attribution"):
        do_ingest(db, data, tmp_path, source_name="  ")
    with pytest.raises(ImportRefusedError, match="File-level errors"):
        bad = statement(meta={"currency": "USD"})
        do_ingest(db, bad, tmp_path)
    assert counts(db) == before


def test_ingest_stores_provenance_archive_audit_and_builds_dataset(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    data = statement(page=True, items=(("IS.REVENUE", "1000", "1100", "45"), *(
        (*i, "47") for i in ITEMS[1:])))  # fmt: skip
    result = do_ingest(db, data, tmp_path, source_reference="Annual report PDF, pages 45-47")
    assert result.status == "complete"
    assert result.inserted == len(ITEMS) * 2
    record = db.get(SourceImport, result.import_id)
    assert record is not None
    assert record.template_version == "financial_statements.v1"
    assert record.file_sha256 == sha(data)
    assert record.summary["issuer"]["company_id"] == str(issuer.id)
    assert record.summary["metadata"]["scale"] == 3
    assert load_import_file(db, record.id, tmp_path) == data

    rev = db.scalars(
        select(StatementImportValue).where(
            StatementImportValue.import_id == record.id,
            StatementImportValue.line_item_code == "IS.REVENUE",
            StatementImportValue.fiscal_year == 2024,
        )
    ).one()
    assert (rev.reported_value, rev.scale, rev.value) == (Decimal("1100"), 3, Decimal("1100000"))
    assert (rev.unit, rev.currency, rev.source_page) == ("CAD", "CAD", "45")
    assert rev.source_document == META["source_document"]
    assert "column 'FY2024'" in rev.locator

    events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == str(record.id))).all()
    assert [e.action for e in events] == ["financial_statements_imported"]

    assert result.dataset is not None and result.dataset.created
    d = result.dataset.dataset
    assert (d.basis, d.status, d.version_no, d.taxonomy) == ("reported", "draft", 1, "standard")
    assert d.import_cutoff_id == max(
        db.scalars(
            select(StatementImportValue.id).where(StatementImportValue.import_id == record.id)
        )
    )
    v = value_of(db, d.id, "IS.REVENUE", date(2024, 12, 31))
    assert (v.provenance_kind, v.value, v.import_value_id, v.import_id) == (
        "manual_import",
        Decimal("1100000"),
        rev.id,
        record.id,
    )
    assert v.detail["source_page"] == "45"
    checks = {
        (r.check_code, r.status)
        for r in db.scalars(select(ValidationResult).where(ValidationResult.dataset_id == d.id))
    }
    assert ("BALANCE", "passed") in checks
    assert ("SUBTOTAL", "passed") in checks
    assert ("EPS_BASIC", "not_evaluable") in checks  # net income not imported: never "passed"
    verification = verify_dataset(db, d, mappings_root=ROOT)
    assert verification.ok, verification
    assert stored_canonical(db, d) == stored_canonical(db, d)


def test_reingesting_the_same_file_is_idempotent(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    data = statement()
    first = do_ingest(db, data, tmp_path)
    before = counts(db)
    again = check(db, data)
    assert again.summary["duplicate"] == again.summary["records"]
    second = do_ingest(db, data, tmp_path)
    assert (second.import_id, second.status, second.inserted) == (None, "no_new_records", 0)
    assert counts(db) == before
    assert first.dataset is not None
    rebuilt = build_import_dataset(db, issuer, mappings_root=ROOT, mapping_version="v2")
    assert (rebuilt.created, rebuilt.dataset.id) == (False, first.dataset.dataset.id)


def test_partial_import_requires_explicit_flag(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    data = statement(items=(*ITEMS, ("Turnover", "1", "2"), ("IS.COGS", "1", "")))
    report = check(db, data)
    assert report.summary["unmapped"] == 2
    assert report.summary["ambiguous"] == 2  # COGS FY2023: 600 vs 1
    with pytest.raises(ImportRefusedError, match="pass --allow-partial"):
        do_ingest(db, data, tmp_path)
    result = do_ingest(db, data, tmp_path, allow_partial=True)
    record = db.get(SourceImport, result.import_id)
    assert record is not None
    assert record.status == "partial"
    assert {s["status"] for s in record.skipped} == {"unmapped", "ambiguous"}
    assert result.inserted == len(ITEMS) * 2 - 1
    stored = db.scalars(
        select(StatementImportValue.line_item_code).where(
            StatementImportValue.import_id == record.id, StatementImportValue.fiscal_year == 2023
        )
    ).all()
    assert "IS.COGS" not in stored  # neither ambiguous value was stored


def test_conflicting_import_needs_flag_and_opens_a_blocking_conflict(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    first = do_ingest(db, statement(), tmp_path)
    assert first.dataset is not None
    v1 = first.dataset.dataset
    lock_dataset(db, v1.id, mappings_root=ROOT, reason=REASON)
    v1_sha, v1_values = v1.content_sha256, v1.value_count

    restated = statement(
        columns=(FY24,), items=(("IS.REVENUE", "1180"),),
        meta={"source_document": "Synthetic test annual report 2025 (restated 2024)"},
    )  # fmt: skip
    report = check(db, restated)
    assert [r.status for r in report.records] == [Status.CONFLICTING]
    assert "differs_from_import" in issue_codes(report)
    with pytest.raises(ImportRefusedError, match="--allow-conflicts"):
        do_ingest(db, restated, tmp_path)
    result = do_ingest(db, restated, tmp_path, allow_conflicts=True)
    assert (result.inserted, result.conflicting) == (1, 1)
    assert result.dataset is not None
    v2 = result.dataset.dataset
    assert (v2.version_no, v2.parent_dataset_id, v2.status) == (2, v1.id, "draft")
    db.refresh(v1)
    assert (v1.status, v1.content_sha256, v1.value_count) == ("locked", v1_sha, v1_values)

    conflict = db.scalars(select(DataConflict).where(DataConflict.dataset_id == v2.id)).one()
    assert (conflict.kind, conflict.status, conflict.is_material) == ("cross_import", "open", True)
    assert {c["source"] for c in conflict.candidates} == {"import_value"}
    latest = value_of(db, v2.id, "IS.REVENUE", date(2024, 12, 31))
    assert latest.value == Decimal("1180000")  # latest_reported policy
    with pytest.raises(DatasetError, match="open material conflict"):
        lock_dataset(db, v2.id, mappings_root=ROOT, reason=REASON)
    with pytest.raises(DBAPIError, match="open material conflicts"), db.begin_nested():
        db.execute(
            text(
                "UPDATE fin.financial_datasets SET status = 'locked', locked_at = now() "
                "WHERE id = :i"
            ),
            {"i": v2.id},
        )

    original = next(c for c in conflict.candidates if Decimal(c["value"]) == Decimal("1100000"))
    resolve_conflict(
        db, v2.id, conflict.conflict_key, "select_candidate", reason=REASON,
        candidate_fact_id=original["fact_id"],
    )  # fmt: skip
    v3 = rebuild_draft(
        db, issuer, mappings_root=ROOT, mapping_version="v2", reason="apply the resolution",
        basis=IMPORT_BASIS,
    ).dataset  # fmt: skip
    resolved = db.scalars(select(DataConflict).where(DataConflict.dataset_id == v3.id)).one()
    assert resolved.status == "resolved"
    chosen = value_of(db, v3.id, "IS.REVENUE", date(2024, 12, 31))
    assert (chosen.value, chosen.import_value_id) == (Decimal("1100000"), original["fact_id"])
    assert chosen.detail["resolution"]["candidate_source"] == "import_value"
    assert verify_dataset(db, v3, mappings_root=ROOT).ok
    locked = lock_dataset(db, v3.id, mappings_root=ROOT, reason=REASON)
    assert locked.status == "locked"


def test_enter_value_resolution_gives_analyst_provenance(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    do_ingest(db, statement(), tmp_path)
    second = do_ingest(
        db, statement(columns=(FY24,), items=(("IS.REVENUE", "1180"),)), tmp_path,
        allow_conflicts=True,
    )  # fmt: skip
    assert second.dataset is not None
    d = second.dataset.dataset
    key = db.scalars(select(DataConflict.conflict_key).where(DataConflict.dataset_id == d.id)).one()
    resolve_conflict(
        db, d.id, key, "enter_value", reason=REASON, value=Decimal("1150000"),
        source_reference="Annual report 2025, note 3",
    )  # fmt: skip
    nxt = rebuild_draft(
        db, issuer, mappings_root=ROOT, mapping_version="v2", reason="apply the resolution",
        basis=IMPORT_BASIS,
    ).dataset  # fmt: skip
    v = value_of(db, nxt.id, "IS.REVENUE", date(2024, 12, 31))
    assert (v.provenance_kind, v.value, v.import_value_id) == ("analyst", Decimal("1150000"), None)


def test_locked_dataset_values_cannot_change(db: Session, issuer: Company, tmp_path: Path) -> None:
    result = do_ingest(db, statement(), tmp_path)
    assert result.dataset is not None
    d = lock_dataset(db, result.dataset.dataset.id, mappings_root=ROOT, reason=REASON)
    v = value_of(db, d.id, "IS.REVENUE", date(2024, 12, 31))
    with pytest.raises(DBAPIError, match="immutable"), db.begin_nested():
        db.execute(text("DELETE FROM fin.financial_values WHERE dataset_id = :d"), {"d": d.id})
    # The application role cannot UPDATE values at all; the trigger guards every other role.
    with pytest.raises(DBAPIError, match=r"permission denied|never updated"), db.begin_nested():
        db.execute(
            text("UPDATE fin.financial_values SET value = 1 WHERE dataset_id = :d"), {"d": d.id}
        )
    db.refresh(v)
    assert v.value == Decimal("1100000")


def test_no_build_option_and_missing_imports(db: Session, issuer: Company, tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="no imported financial statements"):
        build_import_dataset(db, issuer, mappings_root=ROOT, mapping_version="v2")
    result = do_ingest(db, statement(), tmp_path, build_dataset=False)
    assert result.dataset is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(FinancialDataset)
            .where(FinancialDataset.company_id == issuer.id)
        )
        == 0
    )
    built = build_import_dataset(db, issuer, mappings_root=ROOT, mapping_version="v2")
    assert built.created and built.dataset.basis == "reported"


# --------------------------------------------------------------------------- SEC overlap


@pytest.fixture
def sec_issuer(db: Session) -> Company:
    company = Company(
        legal_name="Dual Source Co", reporting_currency="USD", accounting_standard="US_GAAP",
        accounting_standard_inferred=False,
    )  # fmt: skip
    db.add(company)
    db.flush()
    db.add(
        Security(company_id=company.id, ticker="DSC", exchange_name="Nasdaq",
                 exchange_mic="XNAS", trading_currency="USD")
    )  # fmt: skip
    fy = (date(2024, 1, 1), date(2024, 12, 31))
    k = ("10-K", date(2025, 2, 1), "0000000009-25-000001")
    add_facts(
        db,
        company,
        [("Revenues", "1200", *fy, *k), ("CostOfRevenue", "700", *fy, *k)],
    )
    return company


SEC_META = {"issuer_ticker": "DSC", "issuer_mic": "XNAS", "currency": "USD",
            "accounting_standard": "US_GAAP", "scale": "units"}  # fmt: skip


def test_import_overlapping_sec_data_opens_cross_source_conflict(
    db: Session, sec_issuer: Company, tmp_path: Path
) -> None:
    sec_ds = build_dataset(db, sec_issuer, mappings_root=ROOT, mapping_version="v2").dataset
    sec_sha = sec_ds.content_sha256
    data = statement(columns=(FY24,), meta=SEC_META,
                     items=(("IS.REVENUE", "1100"), ("IS.COGS", "700")))  # fmt: skip
    report = check(db, data)
    assert report.sec_compared
    by_code = {r.item.code: r for r in report.records if r.item}
    assert by_code["IS.REVENUE"].status is Status.CONFLICTING
    assert "material" in by_code["IS.REVENUE"].issues[-1].message
    assert by_code["IS.COGS"].status is Status.ACCEPTED

    result = do_ingest(db, data, tmp_path, allow_conflicts=True)
    assert result.dataset is not None
    d = result.dataset.dataset
    assert d.source_summary["sec_reference"]["confirmed"] == 1
    assert d.facts_as_of == date(2025, 2, 1)
    conflict = db.scalars(select(DataConflict).where(DataConflict.dataset_id == d.id)).one()
    assert (conflict.kind, conflict.status, conflict.line_item_code) == (
        "cross_source",
        "open",
        "IS.REVENUE",
    )
    sec_cand = next(c for c in conflict.candidates if "source" not in c)
    assert sec_cand["accession"] == "0000000009-25-000001"
    cogs = value_of(db, d.id, "IS.COGS", date(2024, 12, 31))
    assert cogs.detail["sec_confirmed"][1] == "0000000009-25-000001"

    resolve_conflict(
        db, d.id, conflict.conflict_key, "select_candidate", reason=REASON,
        candidate_fact_id=sec_cand["fact_id"], candidate_source="reported_fact",
    )  # fmt: skip
    nxt = rebuild_draft(
        db, sec_issuer, mappings_root=ROOT, mapping_version="v2", reason="apply the resolution",
        basis=IMPORT_BASIS,
    ).dataset  # fmt: skip
    rev = value_of(db, nxt.id, "IS.REVENUE", date(2024, 12, 31))
    assert (rev.provenance_kind, rev.value, rev.import_value_id) == (
        "reported",
        Decimal("1200"),
        None,
    )
    assert rev.source_fact_ids == [sec_cand["fact_id"]]
    assert verify_dataset(db, nxt, mappings_root=ROOT).ok
    # The SEC (standardized) dataset is a separate lineage and was not touched.
    db.refresh(sec_ds)
    assert (sec_ds.status, sec_ds.content_sha256) == ("draft", sec_sha)


def test_ambiguous_candidate_id_needs_a_source(
    db: Session, sec_issuer: Company, tmp_path: Path
) -> None:
    result = do_ingest(
        db, statement(columns=(FY24,), meta=SEC_META, items=(("IS.REVENUE", "1100"),)),
        tmp_path, allow_conflicts=True,
    )  # fmt: skip
    assert result.dataset is not None
    d = result.dataset.dataset
    conflict = db.scalars(select(DataConflict).where(DataConflict.dataset_id == d.id)).one()
    with pytest.raises(DatasetError, match="not a candidate"):
        resolve_conflict(
            db, d.id, conflict.conflict_key, "select_candidate", reason=REASON,
            candidate_fact_id=-1,
        )  # fmt: skip
    with pytest.raises(DatasetError, match="Unknown candidate source"):
        resolve_conflict(
            db, d.id, conflict.conflict_key, "select_candidate", reason=REASON,
            candidate_fact_id=conflict.candidates[0]["fact_id"], candidate_source="other",
        )  # fmt: skip


# --------------------------------------------------------------------------- pure build


def rec(
    i: int, code: str, value: str, end: date = date(2024, 12, 31), **kw: object
) -> imp.ImportedRecord:
    start = date(end.year, 1, 1)
    base = imp.ImportedRecord(
        id=i, import_id=f"00000000-0000-0000-0000-00000000000{i % 10}",
        imported_on=date(2025, 3, i),
        filename="f.csv", locator=f"line {i}", line_item=code,
        period=FiscalPeriod(end.year, "FY", start, end), value=Decimal(value), unit="CAD",
        reported_value=Decimal(value), scale=0, source_document="doc", source_page=None,
    )  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def test_pure_build_is_deterministic_and_policy_driven(mapping: MappingSet) -> None:
    records = [rec(1, "IS.REVENUE", "1000"), rec(2, "IS.REVENUE", "1001"), rec(3, "IS.COGS", "5")]
    kw: dict[str, object] = {"currency": "CAD", "import_cutoff_id": 3,
                             "facts_as_of": date(2025, 3, 3), "sec": None}  # fmt: skip
    a = imp.build(records, mapping, **kw)  # type: ignore[arg-type]
    b = imp.build(list(reversed(records)), mapping, **kw)  # type: ignore[arg-type]
    assert a.canonical == b.canonical
    latest = {v.line_item: v for v in a.values}["IS.REVENUE"]
    assert (latest.value, latest.import_value_id) == (Decimal("1001"), 2)
    (conflict,) = a.conflicts
    assert (conflict.kind, conflict.status) == ("cross_import", "auto_resolved_immaterial")
    first = imp.build(records, mapping, policy=Policy.FIRST_REPORTED, **kw)  # type: ignore[arg-type]
    assert {v.line_item: v for v in first.values}["IS.REVENUE"].value == Decimal("1000")
    assert first.content_sha256 != a.content_sha256
    cut = imp.build(records, mapping, **{**kw, "import_cutoff_id": 1})  # type: ignore[arg-type]
    assert [v.line_item for v in cut.values if v.provenance == "manual_import"] == ["IS.REVENUE"]
    assert cut.header.input_sha256 != a.header.input_sha256


def test_pure_build_derives_and_refuses_unknown_codes(mapping: MappingSet) -> None:
    records = [rec(1, "IS.REVENUE", "1000"), rec(2, "IS.COGS", "600")]
    built = imp.build(records, mapping, currency="CAD", import_cutoff_id=2,
                      facts_as_of=date(2025, 3, 2), sec=None)  # fmt: skip
    gp = {v.line_item: v for v in built.values}["IS.GROSS_PROFIT"]
    assert (gp.provenance, gp.value) == ("derived", Decimal("400"))
    with pytest.raises(imp.ImportBuildError, match="not in mapping"):
        imp.build([rec(1, "IS.MADE_UP", "1")], mapping, currency="CAD", import_cutoff_id=1,
                  facts_as_of=date(2025, 3, 1), sec=None)  # fmt: skip
    with pytest.raises(imp.ImportBuildError, match="No imported"):
        imp.build([], mapping, currency="CAD", import_cutoff_id=1,
                  facts_as_of=date(2025, 3, 1), sec=None)  # fmt: skip


# --------------------------------------------------------------------------- DB constraints


def test_import_values_are_append_only_and_normalization_is_enforced(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    result = do_ingest(db, statement(), tmp_path, build_dataset=False)
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(
            text("UPDATE source.statement_import_values SET value = 0 WHERE import_id = :i"),
            {"i": result.import_id},
        )
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(
            text("DELETE FROM source.statement_import_values WHERE import_id = :i"),
            {"i": result.import_id},
        )
    with pytest.raises(DBAPIError, match="value_normalized"), db.begin_nested():
        db.add(
            StatementImportValue(
                import_id=result.import_id, import_row=99, locator="x", company_id=issuer.id,
                mapping_version="v2", line_item_code="IS.RND", period_type="FY",
                fiscal_year=2024, period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
                reported_value=Decimal("5"), scale=3, value=Decimal("5"), unit="CAD",
                currency="CAD", source_document="doc",
            )
        )  # fmt: skip
        db.flush()


def test_manual_import_provenance_requires_import_link(
    db: Session, issuer: Company, tmp_path: Path
) -> None:
    result = do_ingest(db, statement(), tmp_path)
    assert result.dataset is not None
    d = result.dataset.dataset
    v = value_of(db, d.id, "IS.REVENUE", date(2024, 12, 31))
    with pytest.raises(DBAPIError, match="import_iff_manual"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO fin.financial_values (dataset_id, period_id, line_item_code, "
                "mapping_version, status, value, unit, scale, provenance_kind, concept, "
                "concept_priority, sign_multiplier, source_fact_ids, detail) VALUES "
                "(:d, :p, 'IS.RND', 'v2', 'ok', 1, 'CAD', 0, 'manual_import', 'IS.RND', 1, 1, "
                "'{}', '{}')"
            ),
            {"d": d.id, "p": v.period_id},
        )
    with pytest.raises(DBAPIError, match="import_cutoff_iff_reported"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO fin.financial_datasets (id, company_id, basis, version_no, status, "
                "mapping_version, mapping_sha256, taxonomy, currency, selection_policy, "
                "facts_as_of, format_version, input_sha256, content_sha256, interim_coverage, "
                "value_count, source_summary, import_cutoff_id) VALUES (gen_random_uuid(), :c, "
                "'standardized', 99, 'draft', 'v2', 'x', 'us-gaap', 'CAD', 'latest_reported', "
                "'2025-01-01', 3, :h, :h, 'none', 0, '{}', 5)"
            ),
            {"c": issuer.id, "h": "0" * 64},
        )


# --------------------------------------------------------------------------- CLI (read-only)


def test_cli_validate_json_and_list(
    migrated_db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = tmp_path / "statements.csv"
    file.write_bytes(statement(meta={"issuer_ticker": "ZZZQ"}))
    code = cli.main(["statement-import-validate", "--file", str(file), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["file_sha256"] == sha(file.read_bytes())
    assert "No listing ZZZQ XTSE" in report["file_errors"][0]
    assert cli.main(["statement-import-list"]) == 0
    assert "no statement imports" in capsys.readouterr().out
    assert cli.main(["statement-import-show", "--id", "not-a-uuid"]) == 2


def test_cli_stdin_needs_a_filename(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["statement-import-validate", "--file", "-"]) == 1
    assert "--filename is required" in capsys.readouterr().err


def test_shipped_blank_template_matches_the_parser(mapping: MappingSet) -> None:
    data = (ROOT.parent / "templates" / "financial" / "financial_statements.v1.csv").read_bytes()
    table = read_csv(data)
    parsed = tpl.parse_grid(table)
    assert parsed.errors[0].startswith("Missing metadata: issuer_ticker")  # blank on purpose
    codes = {key for _, _, key, _ in parsed.item_rows}
    assert codes == {c for c, i in mapping.line_items.items() if not i.derived_only}
