"""Financial-statement file import: validate (read-only), then explicit ingestion (D57).

1. :func:`validate` reads the file through the format boundary
   (:mod:`erp_backend.market.tabular`), parses the ``financial_statements.v1`` grid
   (:mod:`.template`), resolves the issuer (ticker + MIC → exactly one listing), checks the
   currency, the accounting standard and every period against the company's fiscal
   calendar, maps each row to a standard line item, and compares each record with the
   company's stored imports and its latest-reported SEC values. It only reads.
2. :func:`ingest` re-validates the same bytes under a lock, requires the reviewed SHA-256,
   refuses file-level errors, refuses skipped records (unmapped / ambiguous / rejected)
   unless ``allow_partial`` and conflicting records unless ``allow_conflicts``. It then
   archives the exact bytes, writes one ``source.imports`` row and the new records
   (``source.statement_import_values``, append-only), audits, and builds the company's
   next draft ``reported`` dataset — all in the caller's transaction.

Never done: creating issuers or listings, converting or inferring currencies, overwriting
stored values (a differing value is stored as a second source and becomes a §10.3
conflict), changing an existing dataset version.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.datasets import (
    IMPORT_BASIS,
    BuildResult,
    DatasetError,
    rebuild_draft,
    sec_reference,
)
from erp_backend.ingestion import archive
from erp_backend.market.listings import find_listings
from erp_backend.market.tabular import FileFormatError, read_table
from erp_backend.models import (
    Company,
    FiscalPeriodRow,
    SourceImport,
    StatementImportValue,
)
from erp_backend.standardization import mapping as mapping_mod
from erp_backend.standardization.conflicts import materiality
from erp_backend.standardization.dataset_types import decimal_str
from erp_backend.standardization.imported import match_key
from erp_backend.standardization.periods import (
    FiscalCalendar,
    FiscalPeriod,
    fiscal_year_label,
)
from erp_backend.statements import template as tpl
from erp_backend.statements.template import Metadata, PeriodColumn, Record, Status

PROVIDER_CODE = "manual_import"
MAX_SKIPPED_STORED = 5000
LOCK_KEY = "statement-import"


class ImportRefusedError(Exception):
    """Ingestion refused; nothing was written."""


@dataclass(frozen=True)
class ValidationReport:
    filename: str
    file_sha256: str
    bytes: int
    file_kind: str | None
    file_errors: tuple[str, ...]
    file_warnings: tuple[str, ...]
    records: tuple[Record, ...]
    metadata: Metadata | None = None
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    mapping_version: str | None = None
    columns: tuple[PeriodColumn, ...] = ()
    sec_compared: bool = False

    template: str = tpl.TEMPLATE

    def count(self, status: Status) -> int:
        return sum(1 for r in self.records if r.status is status)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "records": len(self.records),
            **{s.value: self.count(s) for s in Status},
            "warnings": sum(1 for r in self.records for i in r.issues if i.level == "warning"),
        }

    @property
    def skipped(self) -> tuple[Record, ...]:
        return tuple(r for r in self.records if r.status in tpl.SKIPPED)

    @property
    def to_store(self) -> tuple[Record, ...]:
        return tuple(r for r in self.records if r.status in tpl.STORED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "filename": self.filename,
            "file_sha256": self.file_sha256,
            "bytes": self.bytes,
            "file_errors": list(self.file_errors),
            "file_warnings": list(self.file_warnings),
            "issuer": None
            if self.company_id is None
            else {"company_id": str(self.company_id), "name": self.company_name},
            "metadata": None if self.metadata is None else self.metadata.as_dict(),
            "mapping_version": self.mapping_version,
            "periods": [c.as_dict() for c in self.columns],
            "sec_compared": self.sec_compared,
            "summary": self.summary,
            "records": [r.as_dict() for r in self.records],
        }


# --------------------------------------------------------------------------- validation


def _issuer(db: Session, meta: Metadata, errors: list[str]) -> Company | None:
    found = find_listings(db, meta.issuer_ticker, meta.issuer_mic)
    label = f"{meta.issuer_ticker} {meta.issuer_mic}"
    if not found:
        errors.append(
            f"No listing {label}; register the issuer first (listing-register) — imports never "
            "create issuers or listings"
        )
        return None
    if len({s.company_id for s in found}) > 1:
        errors.append(f"{len(found)} listings of different issuers match {label}")
        return None
    return db.get(Company, found[0].company_id)


def _company_checks(
    company: Company, meta: Metadata, errors: list[str], warnings: list[str]
) -> None:
    if not company.reporting_currency:
        errors.append(
            f"{company.legal_name} has no declared reporting currency; it is never inferred "
            "from a file"
        )
    elif meta.currency != company.reporting_currency:
        errors.append(
            f"currency {meta.currency} differs from the company's reporting currency "
            f"{company.reporting_currency}; amounts are never converted"
        )
    standard = company.accounting_standard
    if standard is None:
        warnings.append(
            f"{company.legal_name} has no accounting standard on record; the file's "
            f"{meta.accounting_standard} is recorded with the import only"
        )
    elif standard != meta.accounting_standard:
        message = (
            f"accounting_standard {meta.accounting_standard} differs from the company's "
            f"{'inferred' if company.accounting_standard_inferred else 'declared'} {standard}"
        )
        (warnings if company.accounting_standard_inferred else errors).append(message)


def _calendar_checks(
    db: Session, company: Company, columns: list[PeriodColumn], errors: list[str]
) -> None:
    """Every column must be the fiscal period it claims, consistent with stored periods."""
    stored = db.scalars(
        select(FiscalPeriodRow).where(FiscalPeriodRow.company_id == company.id)
    ).all()
    stored_fy = {(r.start_date, r.end_date) for r in stored if r.period_type == "FY"}
    file_fy = {(c.start, c.end) for c in columns if c.period_type == "FY"}
    for s, e in sorted(file_fy - stored_fy):
        for a, b in sorted(stored_fy):
            if s <= b and a <= e:
                errors.append(
                    f"FY {s}..{e} overlaps the company's stored fiscal year {a}..{b}; a fiscal "
                    "calendar change is not supported by imports"
                )
    calendar = FiscalCalendar.from_windows(dict.fromkeys(stored_fy | file_fy, 1))
    for s, e in calendar.rejected:
        if (s, e) in file_fy and (s, e) not in stored_fy:
            errors.append(f"FY {s}..{e} overlaps another fiscal year in the file")
    by_window = {(r.period_type, r.start_date, r.end_date): r for r in stored}
    for c in columns:
        where = f"column {c.label!r}"
        if c.period_type == "FY":
            found: FiscalPeriod | None = FiscalPeriod(
                fiscal_year_label(c.end), "FY", c.start, c.end
            )
        else:
            found = calendar.classify(c.start, c.end)
        if found is None:
            errors.append(
                f"{where}: {c.period_type} {c.start}..{c.end} cannot be placed in a known fiscal "
                "year; include the FY column (or import the fiscal year first)"
            )
            continue
        if (found.period_type, found.fiscal_year) != (c.period_type, c.fiscal_year):
            errors.append(
                f"{where}: {c.start}..{c.end} is {found.period_type} of fiscal year "
                f"{found.fiscal_year}, not {c.period_type} {c.fiscal_year} (fiscal years are "
                "labelled by the calendar year in which they end)"
            )
            continue
        row = by_window.get(c.window())
        if row is not None and row.fiscal_year != c.fiscal_year:
            errors.append(f"{where}: stored as fiscal year {row.fiscal_year}")


def _stored_values(
    db: Session, company_id: uuid.UUID
) -> dict[tuple[str, str, date, date], list[StatementImportValue]]:
    out: dict[tuple[str, str, date, date], list[StatementImportValue]] = defaultdict(list)
    for v in db.scalars(
        select(StatementImportValue)
        .where(StatementImportValue.company_id == company_id)
        .order_by(StatementImportValue.id)
    ):
        out[(v.line_item_code, v.period_type, v.period_start, v.period_end)].append(v)
    return out


def _classify_against_stored(
    db: Session,
    company: Company,
    records: list[Record],
    mapping: mapping_mod.MappingSet,
) -> bool:
    """Duplicate / conflicting against stored imports and SEC values. Returns whether an
    SEC comparison was possible."""
    stored = _stored_values(db, company.id)
    sec = sec_reference(db, company, mapping, None)
    candidates = [r for r in records if r.status is Status.ACCEPTED]
    base: dict[tuple[str, date, date], Decimal] = {}
    for r in candidates:
        if r.item is not None and r.item.code in ("BS.TOTAL_ASSETS", "IS.REVENUE") and r.value:
            base.setdefault(r.column.window(), r.value)
    for r in candidates:
        key = r.cell_key
        assert key is not None and r.value is not None and r.item is not None
        mine = stored.get(key, [])
        if any(v.value == r.value for v in mine):
            r.status = Status.DUPLICATE
            r.warn("already_imported", "an identical value was already imported")
            continue
        if mine:
            last = mine[-1]
            r.status = Status.CONFLICTING
            r.warn(
                "differs_from_import",
                f"differs from {decimal_str(last.value)} imported earlier (import "
                f"{last.import_id}, {last.locator}); stored as a second source — opens a "
                "conflict",
            )
        if sec is not None:
            period = FiscalPeriod(r.column.fiscal_year, *r.column.window())
            sec_v = sec.values.get(match_key(mapping, period, r.item.code))
            if sec_v is not None and sec_v.value is not None and sec_v.value != r.value:
                m = materiality(r.item.code, (sec_v.value, r.value), base.get(r.column.window()))
                r.status = Status.CONFLICTING
                r.warn(
                    "differs_from_sec",
                    f"SEC reports {decimal_str(sec_v.value)} ({sec_v.concept}, "
                    f"{sec_v.detail.get('accession')}); difference "
                    f"{decimal_str(m.absolute_difference)} is "
                    f"{'material' if m.is_material else 'immaterial'} — opens a conflict",
                )
    return sec is not None


def validate(
    db: Session,
    *,
    filename: str,
    data: bytes,
    mappings_root: Path,
    default_mapping_version: str,
    today: date | None = None,
) -> ValidationReport:
    """Validate a statement file against the template and the stored data. Reads only."""
    today = today or datetime.now(UTC).date()
    name = PurePath(filename).name
    sha = hashlib.sha256(data).hexdigest()

    def report(errors: list[str], kind: str | None, **kw: Any) -> ValidationReport:
        warnings = kw.pop("warnings", [])
        records = kw.pop("records", [])
        return ValidationReport(
            name, sha, len(data), kind, tuple(errors), tuple(warnings), tuple(records), **kw
        )

    try:
        table = read_table(name, data)
    except FileFormatError as exc:
        return report([str(exc)], None)
    parsed = tpl.parse_grid(table)
    errors = list(parsed.errors)
    meta = parsed.metadata
    if meta is None:
        return report(errors, table.kind)
    version = meta.mapping_version or default_mapping_version
    try:
        mapping = mapping_mod.load(mappings_root, version)
    except mapping_mod.MappingError as exc:
        errors.append(f"mapping_version: {exc}")
        return report(errors, table.kind, metadata=meta)
    warnings: list[str] = []
    company = _issuer(db, meta, errors)
    context: dict[str, Any] = {
        "metadata": meta,
        "mapping_version": version,
        "columns": tuple(parsed.columns),
        "warnings": warnings,
    }
    if company is not None:
        context |= {"company_id": company.id, "company_name": company.legal_name}
        _company_checks(company, meta, errors, warnings)
        if parsed.columns:
            _calendar_checks(db, company, parsed.columns, errors)
    if errors:
        return report(errors, table.kind, **context)
    assert company is not None
    records = tpl.records(parsed, mapping, today)
    if not records:
        return report(["The file has no values"], table.kind, **context)
    sec_compared = _classify_against_stored(db, company, records, mapping)
    return report([], table.kind, records=records, sec_compared=sec_compared, **context)


# --------------------------------------------------------------------------- ingestion


@dataclass(frozen=True)
class IngestResult:
    import_id: uuid.UUID | None
    status: str  # complete | partial | no_new_records
    inserted: int
    duplicates: int
    conflicting: int
    skipped: int
    report: ValidationReport
    dataset: BuildResult | None


def ingest(
    db: Session,
    *,
    filename: str,
    data: bytes,
    source_name: str,
    expected_sha256: str,
    raw_root: Path,
    mappings_root: Path,
    default_mapping_version: str,
    source_reference: str | None = None,
    allow_partial: bool = False,
    allow_conflicts: bool = False,
    build_dataset: bool = True,
    user_id: uuid.UUID | None = None,
    today: date | None = None,
) -> IngestResult:
    """Store the new records of a validated file and build the next import draft.
    The caller commits (or rolls back on :class:`ImportRefusedError`)."""
    source_name = source_name.strip()
    if not source_name:
        raise ImportRefusedError("A source attribution (--source) is required")
    # Serialize statement imports: validation and storage see the same stored state.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(LOCK_KEY))))
    report = validate(
        db, filename=filename, data=data, mappings_root=mappings_root,
        default_mapping_version=default_mapping_version, today=today,
    )  # fmt: skip
    if expected_sha256.strip().lower() != report.file_sha256:
        raise ImportRefusedError(
            f"File SHA-256 is {report.file_sha256}, not the reviewed {expected_sha256}; "
            "validate this exact file first"
        )
    if report.file_errors:
        raise ImportRefusedError("File-level errors: " + "; ".join(report.file_errors))
    skipped = report.skipped
    if skipped and not allow_partial:
        raise ImportRefusedError(
            f"{len(skipped)} record(s) would be skipped ({report.count(Status.UNMAPPED)} "
            f"unmapped, {report.count(Status.AMBIGUOUS)} ambiguous, "
            f"{report.count(Status.REJECTED)} rejected); fix the file or pass --allow-partial"
        )
    conflicting = report.count(Status.CONFLICTING)
    if conflicting and not allow_conflicts:
        raise ImportRefusedError(
            f"{conflicting} record(s) differ from stored imports or SEC values; they would be "
            "stored as second sources and open conflicts that block locking — pass "
            "--allow-conflicts to proceed"
        )
    to_store = report.to_store
    duplicates = report.count(Status.DUPLICATE)
    if not to_store:
        return IngestResult(None, "no_new_records", 0, duplicates, 0, len(skipped), report, None)
    assert report.company_id is not None and report.metadata is not None
    company = db.get(Company, report.company_id)
    assert company is not None

    stored = archive.store(raw_root, PROVIDER_CODE, data)
    meta = report.metadata
    summary: dict[str, Any] = {
        **report.summary,
        "issuer": {
            "company_id": str(company.id),
            "ticker": meta.issuer_ticker,
            "mic": meta.issuer_mic,
        },
        "metadata": meta.as_dict(),
        "mapping_version": report.mapping_version,
        "periods": [c.as_dict() for c in report.columns],
        "sec_compared": report.sec_compared,
        "file_warnings": list(report.file_warnings),
    }
    record = SourceImport(
        id=uuid.uuid4(),
        provider_code=PROVIDER_CODE,
        kind=report.file_kind or "csv",
        template_version=report.template,
        original_filename=report.filename,
        file_sha256=report.file_sha256,
        stored_path=stored.relative_path,
        bytes=report.bytes,
        source_name=source_name,
        source_reference=(source_reference or "").strip() or None,
        status="partial" if skipped else "complete",
        summary=summary,
        skipped=[r.as_dict() for r in skipped[:MAX_SKIPPED_STORED]],
        imported_by=user_id,
    )
    db.add(record)
    db.flush()
    for r in to_store:
        assert r.item is not None and r.value is not None and r.reported_value is not None
        db.add(
            StatementImportValue(
                import_id=record.id, import_row=r.row, locator=r.locator,
                company_id=company.id, mapping_version=report.mapping_version,
                line_item_code=r.item.code, period_type=r.column.period_type,
                fiscal_year=r.column.fiscal_year, period_start=r.column.start,
                period_end=r.column.end, reported_value=r.reported_value, scale=r.scale,
                value=r.value, unit=r.unit, currency=meta.currency,
                source_document=r.column.source_document, source_page=r.source_page,
            )
        )  # fmt: skip
    db.flush()
    audit.record(
        db,
        action="financial_statements_imported",
        entity_type="import",
        entity_id=str(record.id),
        user_id=user_id,
        after={
            "template": report.template,
            "file_sha256": report.file_sha256,
            "filename": report.filename,
            "company_id": str(company.id),
            "source_name": source_name,
            "summary": report.summary,
            "inserted": len(to_store),
        },
    )
    built = None
    if build_dataset:
        assert report.mapping_version is not None
        try:
            built = rebuild_draft(
                db, company, mappings_root=mappings_root, mapping_version=report.mapping_version,
                reason=f"superseded by financial-statement import {record.id}", user_id=user_id,
                basis=IMPORT_BASIS,
            )  # fmt: skip
        except DatasetError as exc:
            raise ImportRefusedError(f"Dataset build failed: {exc}") from exc
    db.flush()
    return IngestResult(
        record.id, record.status, len(to_store), duplicates, conflicting, len(skipped), report,
        built,
    )  # fmt: skip


def load_import_file(db: Session, import_id: uuid.UUID, raw_root: Path) -> bytes:
    """The original bytes of an import, verified against the recorded SHA-256."""
    record = db.get(SourceImport, import_id)
    if record is None:
        raise ImportRefusedError(f"No import {import_id}")
    return archive.load(raw_root, record.stored_path, record.file_sha256)
