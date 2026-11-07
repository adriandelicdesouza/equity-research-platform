"""Financial datasets, values, statements, checks, integrity and conflicts (read-only).

Datasets are immutable versions (D10, D50, D57): every response names the dataset id,
basis, version and status, and values are read from exactly one dataset — callers never
receive a mix of versions. Provenance comes from the stored rows: SEC facts (accession,
form, filing date) for ``reported`` values, the import file and cell for
``manual_import`` values, the derivation rule for ``derived`` values.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend.datasets import stored_canonical, stored_checks
from erp_backend.errors import ApiError
from erp_backend.models import (
    Company,
    ConflictResolution,
    DataConflict,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    ReportedFact,
    SourceImport,
    StandardLineItem,
    StatementImportValue,
    ValidationResult,
)
from erp_backend.read.common import not_found, paginate, period_out, periods_by_id
from erp_backend.read.schemas import (
    CandidateOut,
    CheckOut,
    ConflictOut,
    DatasetDetailOut,
    DatasetOut,
    FactSourceOut,
    FinancialValueOut,
    ImportSourceOut,
    IntegrityOut,
    Page,
    ResolutionOut,
    StatementCellOut,
    StatementOut,
    StatementRowOut,
)
from erp_backend.standardization import validation as checks_mod

STATEMENTS = ("IS", "BS", "CF")
MAX_STATEMENT_PERIODS = 60


def _dataset(d: FinancialDataset) -> DatasetOut:
    return DatasetOut(
        id=d.id,
        company_id=d.company_id,
        basis=d.basis,
        version_no=d.version_no,
        status=d.status,
        parent_dataset_id=d.parent_dataset_id,
        currency=d.currency,
        mapping_version=d.mapping_version,
        taxonomy=d.taxonomy,
        selection_policy=d.selection_policy,
        facts_as_of=d.facts_as_of,
        interim_coverage=d.interim_coverage,
        value_count=d.value_count,
        format_version=d.format_version,
        content_sha256=d.content_sha256,
        input_sha256=d.input_sha256,
        created_at=d.created_at,
        locked_at=d.locked_at,
        discarded_at=d.discarded_at,
        status_reason=d.status_reason,
    )


def require_dataset(db: Session, dataset_id: uuid.UUID) -> FinancialDataset:
    d = db.get(FinancialDataset, dataset_id)
    if d is None:
        raise not_found("dataset", dataset_id)
    return d


def list_datasets(
    db: Session,
    company_id: uuid.UUID,
    *,
    basis: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> Page[DatasetOut]:
    if db.get(Company, company_id) is None:
        raise not_found("company", company_id)
    stmt = select(FinancialDataset).where(FinancialDataset.company_id == company_id)
    if basis:
        stmt = stmt.where(FinancialDataset.basis == basis)
    if status:
        stmt = stmt.where(FinancialDataset.status == status)
    stmt = stmt.order_by(FinancialDataset.basis, FinancialDataset.version_no.desc())
    return paginate(db, stmt, limit, offset, lambda rows: [_dataset(d) for d in rows])


def get_dataset(db: Session, dataset_id: uuid.UUID) -> DatasetDetailOut:
    d = require_dataset(db, dataset_id)
    checks = dict(
        db.execute(
            select(ValidationResult.status, func.count())
            .where(ValidationResult.dataset_id == d.id)
            .group_by(ValidationResult.status)
        ).all()
    )
    conflicts = dict(
        db.execute(
            select(DataConflict.status, func.count())
            .where(DataConflict.dataset_id == d.id)
            .group_by(DataConflict.status)
        ).all()
    )
    open_material = db.scalar(
        select(func.count()).where(
            DataConflict.dataset_id == d.id,
            DataConflict.is_material.is_(True),
            DataConflict.status == "open",
        )
    )
    return DatasetDetailOut(
        **_dataset(d).model_dump(),
        source_summary=d.source_summary,
        check_counts=checks,
        conflict_counts=conflicts,
        open_material_conflicts=open_material or 0,
    )


# --------------------------------------------------------------------------- values


def _line_items(db: Session, mapping_version: str) -> dict[str, StandardLineItem]:
    return {
        li.code: li
        for li in db.scalars(
            select(StandardLineItem).where(StandardLineItem.mapping_version == mapping_version)
        )
    }


def _values_stmt(
    d: FinancialDataset,
    *,
    statement: str | None,
    line_items: list[str] | None,
    period_type: str | None,
    fiscal_year_from: int | None,
    fiscal_year_to: int | None,
    provenance: str | None,
) -> Any:
    stmt = (
        select(FinancialValue, FiscalPeriodRow)
        .join(FiscalPeriodRow, FiscalPeriodRow.id == FinancialValue.period_id)
        .where(FinancialValue.dataset_id == d.id)
    )
    if statement:
        stmt = stmt.where(FinancialValue.line_item_code.like(f"{statement}.%"))
    if line_items:
        stmt = stmt.where(FinancialValue.line_item_code.in_(line_items))
    if period_type:
        stmt = stmt.where(FiscalPeriodRow.period_type == period_type)
    if fiscal_year_from is not None:
        stmt = stmt.where(FiscalPeriodRow.fiscal_year >= fiscal_year_from)
    if fiscal_year_to is not None:
        stmt = stmt.where(FiscalPeriodRow.fiscal_year <= fiscal_year_to)
    if provenance:
        stmt = stmt.where(FinancialValue.provenance_kind == provenance)
    return stmt.order_by(
        FiscalPeriodRow.end_date,
        FiscalPeriodRow.start_date,
        FinancialValue.line_item_code,
    )


def _fact_sources(db: Session, ids: set[int]) -> dict[int, FactSourceOut]:
    if not ids:
        return {}
    return {
        f.id: FactSourceOut(
            fact_id=f.id,
            taxonomy=f.taxonomy,
            concept=f.concept,
            unit=f.unit,
            value=f.value,
            accession_no=f.accession_no,
            form=f.form,
            filed_date=f.filed_date,
            period_start=f.period_start,
            period_end=f.period_end,
        )
        for f in db.scalars(select(ReportedFact).where(ReportedFact.id.in_(ids)))
    }


def _import_sources(
    db: Session, rows: Sequence[FinancialValue]
) -> dict[tuple[uuid.UUID, int | None], ImportSourceOut]:
    value_ids = {v.import_value_id for v in rows if v.import_value_id is not None}
    import_ids = {v.import_id for v in rows if v.import_id is not None}
    if not import_ids:
        return {}
    cells = {
        c.id: c
        for c in db.scalars(
            select(StatementImportValue).where(StatementImportValue.id.in_(value_ids))
        )
    } if value_ids else {}  # fmt: skip
    files = {
        i.id: i for i in db.scalars(select(SourceImport).where(SourceImport.id.in_(import_ids)))
    }
    out: dict[tuple[uuid.UUID, int | None], ImportSourceOut] = {}
    for v in rows:
        if v.import_id is None:
            continue
        cell = cells.get(v.import_value_id) if v.import_value_id is not None else None
        file = files.get(v.import_id)
        out[(v.import_id, v.import_value_id)] = ImportSourceOut(
            import_id=v.import_id,
            import_value_id=v.import_value_id,
            original_filename=file.original_filename if file else None,
            file_sha256=file.file_sha256 if file else None,
            locator=cell.locator if cell else None,
            source_document=cell.source_document if cell else None,
            source_page=cell.source_page if cell else None,
        )
    return out


def list_values(
    db: Session,
    dataset_id: uuid.UUID,
    *,
    statement: str | None,
    line_items: list[str] | None,
    period_type: str | None,
    fiscal_year_from: int | None,
    fiscal_year_to: int | None,
    provenance: str | None,
    limit: int,
    offset: int,
) -> Page[FinancialValueOut]:
    d = require_dataset(db, dataset_id)
    labels = _line_items(db, d.mapping_version)
    stmt = _values_stmt(
        d, statement=statement, line_items=line_items, period_type=period_type,
        fiscal_year_from=fiscal_year_from, fiscal_year_to=fiscal_year_to, provenance=provenance,
    )  # fmt: skip

    def mapper(rows: Sequence[Any]) -> list[FinancialValueOut]:
        values = [v for v, _ in rows]
        facts = _fact_sources(
            db,
            {
                i
                for v in values
                if v.provenance_kind != "manual_import"
                for i in (v.source_fact_ids or [])
            },
        )
        imports = _import_sources(db, values)
        return [
            FinancialValueOut(
                line_item_code=v.line_item_code,
                label=labels[v.line_item_code].label if v.line_item_code in labels else None,
                statement=v.line_item_code.split(".", 1)[0],
                period=period_out(p),
                value=v.value,
                unit=v.unit,
                scale=v.scale,
                status=v.status,
                provenance_kind=v.provenance_kind,
                concept=v.concept,
                sign_multiplier=v.sign_multiplier,
                derivation=v.detail,
                fact_sources=[
                    facts[i]
                    for i in (v.source_fact_ids or [])
                    if v.provenance_kind != "manual_import" and i in facts
                ],
                import_source=imports.get((v.import_id, v.import_value_id))
                if v.import_id is not None
                else None,
            )
            for v, p in rows
        ]

    return paginate(db, stmt, limit, offset, mapper, scalars=False)


def get_statement(
    db: Session,
    dataset_id: uuid.UUID,
    statement: str,
    *,
    period_type: str,
    fiscal_year_from: int | None,
    fiscal_year_to: int | None,
) -> StatementOut:
    """One statement of one dataset as a grid: line items (mapping order) × periods."""
    d = require_dataset(db, dataset_id)
    rows = db.execute(
        _values_stmt(
            d, statement=statement, line_items=None, period_type=period_type,
            fiscal_year_from=fiscal_year_from, fiscal_year_to=fiscal_year_to, provenance=None,
        )
    ).all()  # fmt: skip
    periods = sorted({p for _, p in rows}, key=lambda p: (p.end_date, p.start_date))
    if len(periods) > MAX_STATEMENT_PERIODS:
        raise ApiError(
            422,
            "too_many_periods",
            f"{len(periods)} periods match; narrow fiscal_year_from/fiscal_year_to "
            f"(at most {MAX_STATEMENT_PERIODS})",
        )
    column = {p.id: i for i, p in enumerate(periods)}
    items = _line_items(db, d.mapping_version)
    grid: dict[str, list[StatementCellOut | None]] = {}
    units: dict[str, str] = {}
    for v, p in rows:
        cells = grid.setdefault(v.line_item_code, [None] * len(periods))
        cells[column[p.id]] = StatementCellOut(
            value=v.value, provenance_kind=v.provenance_kind, status=v.status
        )
        units.setdefault(v.line_item_code, v.unit)
    order = sorted(
        grid, key=lambda code: (items[code].sort_order if code in items else 1_000_000, code)
    )
    return StatementOut(
        dataset_id=d.id,
        basis=d.basis,
        version_no=d.version_no,
        status=d.status,
        currency=d.currency,
        statement=statement,
        periods=[period_out(p) for p in periods],
        rows=[
            StatementRowOut(
                line_item_code=code,
                label=items[code].label if code in items else None,
                is_subtotal=bool(items[code].is_subtotal) if code in items else False,
                value_type=items[code].value_type if code in items else None,
                unit=units.get(code),
                cells=grid[code],
            )
            for code in order
        ],
    )


# --------------------------------------------------------------------------- checks


def list_checks(
    db: Session,
    dataset_id: uuid.UUID,
    *,
    severity: str | None,
    status: str | None,
    check_code: str | None,
    limit: int,
    offset: int,
) -> Page[CheckOut]:
    require_dataset(db, dataset_id)
    stmt = select(ValidationResult).where(ValidationResult.dataset_id == dataset_id)
    if severity:
        stmt = stmt.where(ValidationResult.severity == severity)
    if status:
        stmt = stmt.where(ValidationResult.status == status)
    if check_code:
        stmt = stmt.where(ValidationResult.check_code == check_code)
    stmt = stmt.order_by(ValidationResult.id)

    def mapper(rows: Sequence[ValidationResult]) -> list[CheckOut]:
        periods = periods_by_id(db, {r.period_id for r in rows if r.period_id is not None})
        return [
            CheckOut(
                id=r.id,
                check_code=r.check_code,
                subject=r.subject,
                period=period_out(periods[r.period_id]) if r.period_id in periods else None,
                severity=r.severity,
                status=r.status,
                expected=r.expected,
                actual=r.actual,
                difference=r.difference,
                tolerance=r.tolerance,
                unit=r.unit,
                message=r.message,
                inputs=r.inputs,
            )
            for r in rows
        ]

    return paginate(db, stmt, limit, offset, mapper)


def integrity(db: Session, dataset_id: uuid.UUID) -> IntegrityOut:
    """Stored-row integrity: values and checks re-hash to the recorded fingerprints.

    The full rebuild comparison (re-running the build from facts/imports) stays in the CLI
    ``dataset-verify``: it is heavy and not suitable for an interactive request."""
    d = require_dataset(db, dataset_id)
    if d.status == "discarded":
        return IntegrityOut(
            dataset_id=d.id, status=d.status, stored_values_match=None,
            stored_checks_match=None, note="Discarded datasets hold no values.",
        )  # fmt: skip
    values_ok = hashlib.sha256(stored_canonical(db, d)).hexdigest() == d.content_sha256
    checks_ok = (
        d.validation_sha256 is not None
        and checks_mod.sha256(stored_checks(db, d)) == d.validation_sha256
    )
    return IntegrityOut(
        dataset_id=d.id,
        status=d.status,
        stored_values_match=values_ok,
        stored_checks_match=checks_ok,
        note="Stored rows only; the rebuild-from-sources comparison is `dataset-verify` (CLI).",
    )


# --------------------------------------------------------------------------- conflicts


def _decimal(text: Any) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(str(text))
    except InvalidOperation:
        return None


def _candidate(raw: dict[str, Any]) -> CandidateOut:
    known = {"source", "fact_id", "value", "role", "concept", "form", "filed", "accession"}
    return CandidateOut(
        source=raw.get("source", "reported_fact"),
        candidate_id=raw.get("fact_id"),
        value=_decimal(raw.get("value")),
        role=raw.get("role"),
        concept=raw.get("concept"),
        form=raw.get("form"),
        filed=raw.get("filed"),
        accession=raw.get("accession"),
        detail={k: v for k, v in raw.items() if k not in known},
    )


def _resolution(r: ConflictResolution) -> ResolutionOut:
    return ResolutionOut(
        id=r.id,
        conflict_key=r.conflict_key,
        action=r.action,
        candidate_source=r.candidate_source or "reported_fact",
        candidate_id=r.candidate_fact_id,
        value=r.value,
        source_reference=r.source_reference,
        reason=r.reason,
        line_item_code=r.line_item_code,
        period_type=r.period_type,
        period_start=r.period_start,
        period_end=r.period_end,
        detected_in_dataset_id=r.detected_in_dataset_id,
        created_at=r.created_at,
    )


def list_conflicts(
    db: Session,
    dataset_id: uuid.UUID,
    *,
    material: bool | None,
    status: str | None,
    kind: str | None,
    line_item: str | None,
    limit: int,
    offset: int,
) -> Page[ConflictOut]:
    require_dataset(db, dataset_id)
    stmt = select(DataConflict).where(DataConflict.dataset_id == dataset_id)
    if material is not None:
        stmt = stmt.where(DataConflict.is_material.is_(material))
    if status:
        stmt = stmt.where(DataConflict.status == status)
    if kind:
        stmt = stmt.where(DataConflict.kind == kind)
    if line_item:
        stmt = stmt.where(DataConflict.line_item_code == line_item)
    stmt = stmt.order_by(DataConflict.id)

    def mapper(rows: Sequence[DataConflict]) -> list[ConflictOut]:
        periods = periods_by_id(db, {c.period_id for c in rows})
        res_ids = {c.resolution_id for c in rows if c.resolution_id is not None}
        resolutions = {
            r.id: r
            for r in db.scalars(
                select(ConflictResolution).where(ConflictResolution.id.in_(res_ids))
            )
        } if res_ids else {}  # fmt: skip
        return [
            ConflictOut(
                id=c.id,
                dataset_id=c.dataset_id,
                conflict_key=c.conflict_key,
                kind=c.kind,
                line_item_code=c.line_item_code,
                period=period_out(periods[c.period_id]),
                is_material=c.is_material,
                absolute_difference=c.absolute_difference,
                relative_difference=c.relative_difference,
                threshold=c.threshold,
                materiality_basis=c.materiality_basis,
                status=c.status,
                candidates=[_candidate(x) for x in c.candidates],
                policy_candidate_id=c.policy_fact_id,
                resolution=_resolution(resolutions[c.resolution_id])
                if c.resolution_id in resolutions
                else None,
                detail=c.detail,
            )
            for c in rows
        ]

    return paginate(db, stmt, limit, offset, mapper)


def list_resolutions(
    db: Session, company_id: uuid.UUID, *, conflict_key: str | None, limit: int, offset: int
) -> Page[ResolutionOut]:
    if db.get(Company, company_id) is None:
        raise not_found("company", company_id)
    stmt = select(ConflictResolution).where(ConflictResolution.company_id == company_id)
    if conflict_key:
        stmt = stmt.where(ConflictResolution.conflict_key == conflict_key)
    stmt = stmt.order_by(ConflictResolution.id.desc())
    return paginate(db, stmt, limit, offset, lambda rows: [_resolution(r) for r in rows])
