"""Versioned standardized datasets: build, verify, lock, discard (Blueprint §3.4; D10, D50).

Every mutation writes an audit event in the same transaction. Lifecycle rules are also
enforced by database triggers (migration 0006), so this module cannot bypass them.

* ``build_dataset`` creates draft version N+1 from the stored facts, unless the result is
  byte-identical to the company's current version (then that version is returned and
  nothing is written). A company has at most one draft; it must be locked or discarded
  before a different build. The parent is the latest locked version, and the draft's
  summary records the diff against it.
* ``verify_dataset`` re-serializes the stored rows and rebuilds from the facts with the
  recorded parameters; both must reproduce ``content_sha256`` byte for byte.
* ``lock_dataset`` requires a successful verification and no ambiguous values (D13).
  Failed integrity checks (D51) do not block locking: they are recorded with the dataset
  and gate approval for valuation, which remains the analyst's decision.
* Integrity checks run on every build; their results are stored with the draft
  (``fin.validation_results``) and fingerprinted (``validation_sha256``).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import Policy
from erp_backend.models import (
    Company,
    ConflictResolution,
    DataConflict,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    ReportedFact,
    SourceImport,
    StatementImportValue,
    ValidationResult,
)
from erp_backend.standardization import conflicts as conflicts_mod
from erp_backend.standardization import dataset as ds
from erp_backend.standardization import imported as imp
from erp_backend.standardization import mapping as mapping_mod
from erp_backend.standardization import validation as checks_mod
from erp_backend.standardization.periods import FiscalPeriod
from erp_backend.standardization.resolve import taxonomy_for_standard

BASIS = "standardized"  # SEC-built datasets
IMPORT_BASIS = imp.BASIS  # "reported": built from financial-statement imports (D57)


class DatasetError(Exception):
    pass


def facts_with_ids(db: Session, company_id: uuid.UUID) -> dict[RawFact, int]:
    rows = db.scalars(select(ReportedFact).where(ReportedFact.company_id == company_id)).all()
    return {
        RawFact(
            r.taxonomy, r.concept, r.unit, r.value, r.period_start, r.period_end,
            r.fiscal_year, r.fiscal_period, r.form, r.filed_date, r.accession_no, r.frame,
        ): r.id
        for r in rows
    }  # fmt: skip


def _company_inputs(company: Company) -> tuple[str, str]:
    taxonomy = taxonomy_for_standard(company.accounting_standard)
    if taxonomy is None or not company.reporting_currency:
        raise DatasetError(
            f"{company.legal_name}: accounting standard and reporting currency are required"
        )
    return taxonomy, company.reporting_currency


def _build(
    db: Session,
    company: Company,
    mapping: mapping_mod.MappingSet,
    policy: Policy,
    facts_as_of: date | None,
    resolution_cutoff_id: int | None,
) -> ds.BuiltDataset:
    taxonomy, currency = _company_inputs(company)
    try:
        return ds.build(
            facts_with_ids(db, company.id),
            mapping,
            taxonomy=taxonomy,
            currency=currency,
            policy=policy,
            facts_as_of=facts_as_of,
            resolutions=resolutions_for(db, company.id, resolution_cutoff_id),
            resolution_cutoff_id=resolution_cutoff_id,
        )
    except ds.DatasetBuildError as exc:
        raise DatasetError(f"{company.legal_name}: {exc}") from exc


def _period_rows(
    db: Session, company_id: uuid.UUID, periods: tuple[FiscalPeriod, ...]
) -> dict[FiscalPeriod, uuid.UUID]:
    existing = {
        (r.period_type, r.start_date, r.end_date): r
        for r in db.scalars(
            select(FiscalPeriodRow).where(FiscalPeriodRow.company_id == company_id)
        ).all()
    }
    out: dict[FiscalPeriod, uuid.UUID] = {}
    for p in periods:
        row = existing.get((p.period_type, p.start, p.end))
        if row is None:
            row = FiscalPeriodRow(
                id=uuid.uuid4(),
                company_id=company_id,
                fiscal_year=p.fiscal_year,
                period_type=p.period_type,
                start_date=p.start,
                end_date=p.end,
                weeks=p.weeks,
            )
            db.add(row)
        elif row.fiscal_year != p.fiscal_year:
            raise DatasetError(
                f"Period {p.period_type} {p.start}..{p.end} was stored as fiscal year "
                f"{row.fiscal_year}, now computed as {p.fiscal_year}; period rules changed"
            )
        out[p] = row.id
    db.flush()
    return out


def resolutions_for(
    db: Session, company_id: uuid.UUID, cutoff: int | None
) -> dict[str, conflicts_mod.Resolution]:
    """Latest resolution per conflict key with id ≤ cutoff (none when cutoff is None)."""
    if cutoff is None:
        return {}
    rows = db.scalars(
        select(ConflictResolution)
        .where(ConflictResolution.company_id == company_id, ConflictResolution.id <= cutoff)
        .order_by(ConflictResolution.id)
    ).all()
    return {r.conflict_key: _resolution(r) for r in rows}


def _resolution(r: ConflictResolution) -> conflicts_mod.Resolution:
    return conflicts_mod.Resolution(
        r.id, r.conflict_key, r.action, r.candidate_fact_id, r.value, r.source_reference,
        r.candidate_source,
    )  # fmt: skip


def _candidate(d: dict[str, Any]) -> conflicts_mod.Candidate:
    return conflicts_mod.Candidate(
        d["fact_id"], d["accession"], d["form"], date.fromisoformat(d["filed"]), d["concept"],
        Decimal(d["value"]), d["role"], d.get("source", "reported_fact"),
    )  # fmt: skip


def stored_conflicts(db: Session, dataset: FinancialDataset) -> list[conflicts_mod.Conflict]:
    rows = db.execute(
        select(DataConflict, FiscalPeriodRow, ConflictResolution)
        .join(FiscalPeriodRow, FiscalPeriodRow.id == DataConflict.period_id)
        .outerjoin(ConflictResolution, ConflictResolution.id == DataConflict.resolution_id)
        .where(DataConflict.dataset_id == dataset.id)
    ).all()
    return [
        conflicts_mod.Conflict(
            key=c.conflict_key,
            kind=c.kind,
            line_item=c.line_item_code,
            period=FiscalPeriod(p.fiscal_year, p.period_type, p.start_date, p.end_date),
            candidates=tuple(_candidate(d) for d in c.candidates),
            policy_fact_id=c.policy_fact_id,
            materiality=conflicts_mod.Materiality(
                c.is_material,
                c.absolute_difference,
                c.relative_difference,
                c.threshold,
                c.materiality_basis,
            ),
            status=c.status,
            resolution=None if r is None else _resolution(r),
            detail=c.detail,
        )
        for c, p, r in rows
    ]


def _store_conflicts(
    db: Session,
    dataset: FinancialDataset,
    built: ds.BuiltDataset | imp.BuiltImport,
    period_ids: dict[FiscalPeriod, uuid.UUID],
    user_id: uuid.UUID | None,
) -> None:
    for c in built.conflicts:
        m = c.materiality
        db.add(
            DataConflict(
                dataset_id=dataset.id, conflict_key=c.key, kind=c.kind,
                line_item_code=c.line_item, period_id=period_ids[c.period],
                candidates=[x.as_dict() for x in c.candidates], policy_fact_id=c.policy_fact_id,
                is_material=m.is_material, absolute_difference=m.absolute_difference,
                relative_difference=m.relative_difference, threshold=m.threshold,
                materiality_basis=m.basis, status=c.status,
                resolution_id=c.resolution.id if c.resolution else None, detail=dict(c.detail),
            )
        )  # fmt: skip
    if built.conflicts:
        audit.record(
            db,
            action="conflicts_detected",
            entity_type="financial_dataset",
            entity_id=str(dataset.id),
            user_id=user_id,
            after={
                "summary": built.summary["conflicts"],
                "open_material": sorted(
                    c.key
                    for c in built.conflicts
                    if c.status == "open" and c.materiality.is_material
                ),
            },
        )


def stored_values(db: Session, dataset: FinancialDataset) -> list[ds.DatasetValue]:
    rows = db.execute(
        select(FinancialValue, FiscalPeriodRow)
        .join(FiscalPeriodRow, FiscalPeriodRow.id == FinancialValue.period_id)
        .where(FinancialValue.dataset_id == dataset.id)
    ).all()
    return [
        ds.DatasetValue(
            period=FiscalPeriod(p.fiscal_year, p.period_type, p.start_date, p.end_date),
            line_item=v.line_item_code,
            status=v.status,
            value=v.value,
            unit=v.unit,
            provenance=v.provenance_kind,
            concept=v.concept,
            priority=v.concept_priority,
            sign=v.sign_multiplier,
            source_fact_ids=tuple(v.source_fact_ids),
            detail=v.detail,
            import_value_id=v.import_value_id,
            import_id=None if v.import_id is None else str(v.import_id),
        )
        for v, p in rows
    ]


def header_of(dataset: FinancialDataset) -> ds.DatasetHeader:
    return ds.DatasetHeader(
        dataset.mapping_version,
        dataset.mapping_sha256,
        dataset.taxonomy,
        dataset.currency,
        dataset.selection_policy,
        dataset.facts_as_of,
        dataset.input_sha256,
        dataset.format_version,
        dataset.resolution_cutoff_id,
    )


def import_header_of(dataset: FinancialDataset) -> imp.ImportHeader:
    sec = dataset.source_summary.get("sec_reference_header")
    assert dataset.import_cutoff_id is not None
    return imp.ImportHeader(
        dataset.mapping_version, dataset.mapping_sha256, dataset.currency,
        dataset.selection_policy, dataset.facts_as_of, dataset.input_sha256,
        dataset.import_cutoff_id, dataset.resolution_cutoff_id,
        sec["taxonomy"] if sec else None, sec["input_sha256"] if sec else None,
        dataset.taxonomy, dataset.format_version,
    )  # fmt: skip


def stored_canonical(db: Session, dataset: FinancialDataset) -> bytes:
    values = stored_values(db, dataset)
    periods = sorted({v.period for v in values}, key=FiscalPeriod.sort_key)
    conflicts = stored_conflicts(db, dataset)
    if dataset.basis == IMPORT_BASIS:
        return imp.canonical_bytes(import_header_of(dataset), periods, values, conflicts)
    return ds.canonical_bytes(header_of(dataset), periods, values, conflicts)


def _current(db: Session, company_id: uuid.UUID, basis: str = BASIS) -> FinancialDataset | None:
    return db.scalars(
        select(FinancialDataset)
        .where(
            FinancialDataset.company_id == company_id,
            FinancialDataset.basis == basis,
            FinancialDataset.status != "discarded",
        )
        .order_by(FinancialDataset.version_no.desc())
        .limit(1)
    ).first()


def _latest_locked(
    db: Session, company_id: uuid.UUID, basis: str = BASIS
) -> FinancialDataset | None:
    return db.scalars(
        select(FinancialDataset)
        .where(
            FinancialDataset.company_id == company_id,
            FinancialDataset.basis == basis,
            FinancialDataset.status == "locked",
        )
        .order_by(FinancialDataset.version_no.desc())
        .limit(1)
    ).first()


@dataclass(frozen=True)
class BuildResult:
    dataset: FinancialDataset
    created: bool


def build_dataset(
    db: Session,
    company: Company,
    *,
    mappings_root: Path,
    mapping_version: str,
    policy: Policy = Policy.LATEST_REPORTED,
    facts_as_of: date | None = None,
    user_id: uuid.UUID | None = None,
) -> BuildResult:
    mapping = mapping_mod.load(mappings_root, mapping_version)
    cutoff = db.scalar(
        select(func.max(ConflictResolution.id)).where(ConflictResolution.company_id == company.id)
    )
    built = _build(db, company, mapping, policy, facts_as_of, cutoff)
    current = _current(db, company.id)
    if (
        current is not None
        and current.content_sha256 == built.content_sha256
        and current.validation_sha256 == built.validation_sha256
    ):
        return BuildResult(current, created=False)
    if current is not None and current.status == "draft":
        raise DatasetError(
            f"{company.legal_name} has an open draft (version {current.version_no}); "
            "lock or discard it before building a different version"
        )
    return _create_version(db, company, BASIS, built, resolution_cutoff_id=cutoff, user_id=user_id)


def _create_version(
    db: Session,
    company: Company,
    basis: str,
    built: ds.BuiltDataset | imp.BuiltImport,
    *,
    resolution_cutoff_id: int | None,
    user_id: uuid.UUID | None,
    import_cutoff_id: int | None = None,
    extra_summary: dict[str, Any] | None = None,
) -> BuildResult:
    """Persist a new draft version (values, checks, conflicts) with its audit events."""
    parent = _latest_locked(db, company.id, basis)
    version_no = (
        db.scalar(
            select(func.coalesce(func.max(FinancialDataset.version_no), 0)).where(
                FinancialDataset.company_id == company.id, FinancialDataset.basis == basis
            )
        )
        or 0
    ) + 1
    summary = dict(built.summary) | (extra_summary or {})
    summary["diff_vs_parent"] = (
        ds.diff(stored_values(db, parent), list(built.values)) if parent is not None else None
    )
    if parent is not None:
        summary["parent_changes"] = {
            "mapping": parent.mapping_sha256 != built.header.mapping_sha256,
            "inputs": parent.input_sha256 != built.header.input_sha256,
            "facts_as_of": [parent.facts_as_of.isoformat(), built.header.facts_as_of.isoformat()],
        }
    h = built.header
    dataset = FinancialDataset(
        company_id=company.id,
        basis=basis,
        version_no=version_no,
        parent_dataset_id=parent.id if parent else None,
        status="draft",
        mapping_version=h.mapping_version,
        mapping_sha256=h.mapping_sha256,
        taxonomy=h.taxonomy,
        currency=h.currency,
        selection_policy=h.selection_policy,
        facts_as_of=h.facts_as_of,
        format_version=h.format_version,
        input_sha256=h.input_sha256,
        content_sha256=built.content_sha256,
        validation_sha256=built.validation_sha256,
        resolution_cutoff_id=resolution_cutoff_id,
        import_cutoff_id=import_cutoff_id,
        interim_coverage=built.interim_coverage,
        value_count=len(built.values),
        source_summary=summary,
        created_by=user_id,
    )
    db.add(dataset)
    db.flush()
    period_ids = _period_rows(db, company.id, built.periods)
    db.bulk_insert_mappings(
        FinancialValue,
        [
            {
                "dataset_id": dataset.id,
                "period_id": period_ids[v.period],
                "line_item_code": v.line_item,
                "mapping_version": h.mapping_version,
                "status": v.status,
                "value": v.value,
                "unit": v.unit,
                "scale": 0,
                "provenance_kind": v.provenance,
                "concept": v.concept,
                "concept_priority": v.priority,
                "sign_multiplier": v.sign,
                "source_fact_ids": list(v.source_fact_ids),
                "detail": dict(v.detail),
                "import_value_id": v.import_value_id,
                "import_id": None if v.import_id is None else uuid.UUID(v.import_id),
            }
            for v in built.values
        ],
    )
    db.bulk_insert_mappings(
        ValidationResult,
        [
            {
                "dataset_id": dataset.id,
                "check_code": r.check_code,
                "subject": r.subject,
                "period_id": period_ids[r.period] if r.period is not None else None,
                "severity": r.severity,
                "status": r.status,
                "expected": r.expected,
                "actual": r.actual,
                "difference": r.difference,
                "tolerance": r.tolerance,
                "unit": r.unit,
                "message": r.message,
                "inputs": dict(r.inputs),
            }
            for r in built.checks
        ],
    )
    after: dict[str, Any] = {
        "company_id": str(company.id),
        "version_no": version_no,
        "content_sha256": built.content_sha256,
        "input_sha256": h.input_sha256,
        "mapping": f"{h.mapping_version}:{h.mapping_sha256}",
        "selection_policy": h.selection_policy,
        "facts_as_of": h.facts_as_of.isoformat(),
        "values": len(built.values),
        "validation_sha256": built.validation_sha256,
        "validation": built.summary["validation"],
        "conflicts": built.summary["conflicts"],
        "resolution_cutoff_id": resolution_cutoff_id,
        "parent_dataset_id": str(parent.id) if parent else None,
    }
    if basis != BASIS:
        after |= {"basis": basis, "import_cutoff_id": import_cutoff_id}
    audit.record(
        db,
        action="dataset_created",
        entity_type="financial_dataset",
        entity_id=str(dataset.id),
        user_id=user_id,
        after=after,
    )
    _store_conflicts(db, dataset, built, period_ids, user_id)
    db.flush()
    return BuildResult(dataset, created=True)


# ---- Import datasets (basis "reported"; Blueprint §4.6; D57) ---------------------------------


def import_records(
    db: Session, company_id: uuid.UUID, cutoff: int | None = None
) -> list[imp.ImportedRecord]:
    """Stored statement-import values of a company (ids ≤ cutoff when given)."""
    query = (
        select(StatementImportValue, SourceImport)
        .join(SourceImport, SourceImport.id == StatementImportValue.import_id)
        .where(StatementImportValue.company_id == company_id)
        .order_by(StatementImportValue.id)
    )
    if cutoff is not None:
        query = query.where(StatementImportValue.id <= cutoff)
    return [
        imp.ImportedRecord(
            id=v.id,
            import_id=str(v.import_id),
            imported_on=i.imported_at.astimezone(UTC).date(),
            filename=i.original_filename,
            locator=v.locator,
            line_item=v.line_item_code,
            period=FiscalPeriod(v.fiscal_year, v.period_type, v.period_start, v.period_end),
            value=v.value,
            unit=v.unit,
            reported_value=v.reported_value,
            scale=v.scale,
            source_document=v.source_document,
            source_page=v.source_page,
        )
        for v, i in db.execute(query).all()
    ]


def sec_reference(
    db: Session, company: Company, mapping: mapping_mod.MappingSet, facts_as_of: date | None
) -> imp.SecReference | None:
    """Latest-reported SEC values (no resolutions) for cross-source comparison, or None
    when the company has no usable SEC facts (e.g. a TSX-only issuer)."""
    taxonomy = taxonomy_for_standard(company.accounting_standard)
    if taxonomy is None or not company.reporting_currency:
        return None
    facts = facts_with_ids(db, company.id)
    if not facts:
        return None
    try:
        built = ds.build(
            facts, mapping, taxonomy=taxonomy, currency=company.reporting_currency,
            policy=Policy.LATEST_REPORTED, facts_as_of=facts_as_of,
        )  # fmt: skip
    except ds.DatasetBuildError:
        return None
    return imp.SecReference.from_values(
        built.values, mapping, taxonomy=taxonomy, facts_as_of=built.header.facts_as_of,
        input_sha256=built.header.input_sha256,
    )  # fmt: skip


def _build_import(
    db: Session,
    company: Company,
    mapping: mapping_mod.MappingSet,
    policy: Policy,
    facts_as_of: date | None,
    import_cutoff_id: int,
    resolution_cutoff_id: int | None,
) -> imp.BuiltImport:
    if not company.reporting_currency:
        raise DatasetError(f"{company.legal_name}: a declared reporting currency is required")
    records = import_records(db, company.id, import_cutoff_id)
    sec = sec_reference(db, company, mapping, facts_as_of)
    if facts_as_of is None:
        # The SEC comparison cut-off; without SEC facts, the date of the latest import.
        facts_as_of = (
            sec.facts_as_of
            if sec
            else max((r.imported_on for r in records), default=datetime.now(UTC).date())
        )
    try:
        return imp.build(
            records, mapping, currency=company.reporting_currency,
            import_cutoff_id=import_cutoff_id, facts_as_of=facts_as_of, sec=sec, policy=policy,
            resolutions=resolutions_for(db, company.id, resolution_cutoff_id),
            resolution_cutoff_id=resolution_cutoff_id,
        )  # fmt: skip
    except imp.ImportBuildError as exc:
        raise DatasetError(f"{company.legal_name}: {exc}") from exc


def build_import_dataset(
    db: Session,
    company: Company,
    *,
    mappings_root: Path,
    mapping_version: str,
    policy: Policy = Policy.LATEST_REPORTED,
    facts_as_of: date | None = None,
    user_id: uuid.UUID | None = None,
) -> BuildResult:
    """Draft ``reported`` dataset from the company's statement imports (Blueprint §4.6).

    Same lifecycle as SEC datasets: one open draft per company and basis, byte-identical
    builds create nothing, the parent is the latest locked version of this basis."""
    mapping = mapping_mod.load(mappings_root, mapping_version)
    import_cutoff = db.scalar(
        select(func.max(StatementImportValue.id)).where(
            StatementImportValue.company_id == company.id
        )
    )
    if import_cutoff is None:
        raise DatasetError(f"{company.legal_name}: no imported financial statements")
    cutoff = db.scalar(
        select(func.max(ConflictResolution.id)).where(ConflictResolution.company_id == company.id)
    )
    built = _build_import(db, company, mapping, policy, facts_as_of, import_cutoff, cutoff)
    current = _current(db, company.id, IMPORT_BASIS)
    if (
        current is not None
        and current.content_sha256 == built.content_sha256
        and current.validation_sha256 == built.validation_sha256
    ):
        return BuildResult(current, created=False)
    if current is not None and current.status == "draft":
        raise DatasetError(
            f"{company.legal_name} has an open import draft (version {current.version_no}); "
            "lock or discard it before building a different version"
        )
    h = built.header
    sec_header = (
        None
        if h.sec_taxonomy is None
        else {"taxonomy": h.sec_taxonomy, "input_sha256": h.sec_input_sha256}
    )
    return _create_version(
        db, company, IMPORT_BASIS, built, resolution_cutoff_id=cutoff, user_id=user_id,
        import_cutoff_id=import_cutoff, extra_summary={"sec_reference_header": sec_header},
    )  # fmt: skip


def stored_checks(db: Session, dataset: FinancialDataset) -> list[checks_mod.CheckResult]:
    rows = db.execute(
        select(ValidationResult, FiscalPeriodRow)
        .outerjoin(FiscalPeriodRow, FiscalPeriodRow.id == ValidationResult.period_id)
        .where(ValidationResult.dataset_id == dataset.id)
    ).all()
    return [
        checks_mod.CheckResult(
            check_code=r.check_code,
            subject=r.subject,
            period=None
            if p is None
            else FiscalPeriod(p.fiscal_year, p.period_type, p.start_date, p.end_date),
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
        for r, p in rows
    ]


@dataclass(frozen=True)
class Verification:
    stored_matches: bool  # stored rows re-serialize to content_sha256
    validation_matches: bool  # stored checks = recorded hash = checks re-run on stored values
    rebuild_matches: bool | None  # rebuild from facts reproduces the bytes (None: older format)
    inputs_match: bool  # the eligible facts are unchanged (input_sha256)
    mapping_matches: bool  # the mapping files still have the recorded fingerprint
    recomputed_sha256: str

    @property
    def ok(self) -> bool:
        return self.stored_matches and self.validation_matches and self.rebuild_matches is True


def verify_dataset(db: Session, dataset: FinancialDataset, *, mappings_root: Path) -> Verification:
    """Re-serialize stored rows and rebuild from facts; both must reproduce the hashes.

    A rebuild comparison is only meaningful with the code's current dataset format; for an
    older format, ``rebuild_matches`` is None and only the stored-row checks apply.
    """
    if dataset.status == "discarded":
        raise DatasetError(f"Dataset {dataset.id} is discarded and holds no values")
    values = stored_values(db, dataset)
    stored_ok = hashlib.sha256(stored_canonical(db, dataset)).hexdigest() == (
        dataset.content_sha256
    )
    mapping = mapping_mod.load(mappings_root, dataset.mapping_version)
    rerun = checks_mod.validate(values, mapping, dataset.currency)
    validation_ok = (
        dataset.validation_sha256 is not None
        and checks_mod.sha256(stored_checks(db, dataset)) == dataset.validation_sha256
        and checks_mod.sha256(rerun) == dataset.validation_sha256
    )
    company = db.get(Company, dataset.company_id)
    assert company is not None
    rebuilt: ds.BuiltDataset | imp.BuiltImport
    if dataset.basis == IMPORT_BASIS:
        assert dataset.import_cutoff_id is not None
        rebuilt = _build_import(
            db, company, mapping, Policy(dataset.selection_policy), dataset.facts_as_of,
            dataset.import_cutoff_id, dataset.resolution_cutoff_id,
        )  # fmt: skip
        same_format = dataset.format_version == imp.FORMAT_VERSION
    else:
        rebuilt = _build(
            db,
            company,
            mapping,
            Policy(dataset.selection_policy),
            dataset.facts_as_of,
            dataset.resolution_cutoff_id,
        )
        same_format = dataset.format_version == ds.FORMAT_VERSION
    return Verification(
        stored_matches=stored_ok,
        validation_matches=validation_ok,
        rebuild_matches=(rebuilt.content_sha256 == dataset.content_sha256) if same_format else None,
        inputs_match=rebuilt.header.input_sha256 == dataset.input_sha256,
        mapping_matches=mapping.sha256 == dataset.mapping_sha256,
        recomputed_sha256=rebuilt.content_sha256,
    )


def _draft(db: Session, dataset_id: uuid.UUID) -> FinancialDataset:
    dataset = db.get(FinancialDataset, dataset_id, with_for_update=True)
    if dataset is None:
        raise DatasetError(f"No dataset {dataset_id}")
    if dataset.status != "draft":
        raise DatasetError(f"Dataset {dataset_id} is {dataset.status}; only drafts can change")
    return dataset


def lock_dataset(
    db: Session,
    dataset_id: uuid.UUID,
    *,
    mappings_root: Path,
    reason: str,
    user_id: uuid.UUID | None = None,
) -> FinancialDataset:
    dataset = _draft(db, dataset_id)
    ambiguous = db.scalar(
        select(func.count())
        .select_from(FinancialValue)
        .where(FinancialValue.dataset_id == dataset.id, FinancialValue.status == "ambiguous")
    )
    if ambiguous:
        raise DatasetError(f"{ambiguous} ambiguous value(s) must be resolved before locking (D13)")
    open_material = db.scalar(
        select(func.count())
        .select_from(DataConflict)
        .where(
            DataConflict.dataset_id == dataset.id,
            DataConflict.is_material.is_(True),
            DataConflict.status == "open",
        )
    )
    if open_material:
        raise DatasetError(
            f"{open_material} open material conflict(s) must be resolved or overridden with a "
            "reason before locking (D13, D52)"
        )
    check = verify_dataset(db, dataset, mappings_root=mappings_root)
    if not check.ok:
        raise DatasetError(f"Dataset {dataset.id} failed verification: {check}")
    dataset.status = "locked"
    dataset.locked_at = datetime.now(UTC)
    dataset.locked_by = user_id
    dataset.status_reason = reason
    audit.record(
        db,
        action="dataset_locked",
        entity_type="financial_dataset",
        entity_id=str(dataset.id),
        user_id=user_id,
        before={"status": "draft"},
        after={
            "status": "locked",
            "content_sha256": dataset.content_sha256,
            "validation_sha256": dataset.validation_sha256,
            "validation": dataset.source_summary.get("validation"),
        },
        reason=reason,
    )
    db.flush()
    return dataset


def discard_dataset(
    db: Session, dataset_id: uuid.UUID, *, reason: str, user_id: uuid.UUID | None = None
) -> FinancialDataset:
    dataset = _draft(db, dataset_id)
    db.execute(delete(ValidationResult).where(ValidationResult.dataset_id == dataset.id))
    db.execute(delete(FinancialValue).where(FinancialValue.dataset_id == dataset.id))
    dataset.status = "discarded"
    dataset.discarded_at = datetime.now(UTC)
    dataset.status_reason = reason
    audit.record(
        db,
        action="dataset_discarded",
        entity_type="financial_dataset",
        entity_id=str(dataset.id),
        user_id=user_id,
        before={"status": "draft", "values": dataset.value_count},
        after={"status": "discarded"},
        reason=reason,
    )
    db.flush()
    return dataset


# ---- Conflict resolution workflow (Blueprint §10.3; D52) ------------------------------------

MIN_REASON = 10


def _meaningful(reason: str | None) -> str:
    text = (reason or "").strip()
    if len(text) < MIN_REASON or not any(ch.isalpha() for ch in text):
        raise DatasetError(f"A meaningful reason of at least {MIN_REASON} characters is required")
    return text


def resolve_conflict(
    db: Session,
    dataset_id: uuid.UUID,
    conflict_key: str,
    action: str,
    *,
    reason: str,
    candidate_fact_id: int | None = None,
    value: Decimal | None = None,
    source_reference: str | None = None,
    candidate_source: str | None = None,
    user_id: uuid.UUID | None = None,
) -> ConflictResolution:
    """Record an analyst decision for a conflict detected in ``dataset_id``.

    The decision is appended (never edits a dataset). It takes effect in the next build of
    the company's dataset (see :func:`rebuild_draft`); locked versions are never changed.
    """
    reason_text = _meaningful(reason)
    if action not in conflicts_mod.ACTIONS:
        raise DatasetError(f"Unknown action {action!r}")
    dataset = db.get(FinancialDataset, dataset_id)
    if dataset is None:
        raise DatasetError(f"No dataset {dataset_id}")
    found = db.execute(
        select(DataConflict, FiscalPeriodRow)
        .join(FiscalPeriodRow, FiscalPeriodRow.id == DataConflict.period_id)
        .where(DataConflict.dataset_id == dataset.id, DataConflict.conflict_key == conflict_key)
    ).first()
    if found is None:
        raise DatasetError(f"Dataset {dataset_id} has no conflict {conflict_key}")
    conflict, period = found
    if candidate_source is not None and candidate_source not in conflicts_mod.CANDIDATE_SOURCES:
        raise DatasetError(f"Unknown candidate source {candidate_source!r}")
    if action == "select_candidate":
        matches = [
            c
            for c in conflict.candidates
            if c["fact_id"] == candidate_fact_id
            and candidate_source in (None, c.get("source", "reported_fact"))
        ]
        if not matches:
            raise DatasetError(f"Fact {candidate_fact_id} is not a candidate of {conflict_key}")
        if len(matches) > 1:
            raise DatasetError(
                f"Id {candidate_fact_id} matches several candidates of {conflict_key}; "
                "name the candidate source (reported_fact or import_value)"
            )
        # Import datasets record the id space explicitly (D57); SEC datasets have one.
        candidate_source = (
            matches[0].get("source", "reported_fact") if dataset.basis == IMPORT_BASIS else None
        )
    else:
        candidate_source = None
    if action == "enter_value" and (value is None or not (source_reference or "").strip()):
        raise DatasetError("enter_value needs a value and a source reference")
    if action == "override" and conflict.kind == "within_filing":
        raise DatasetError(
            "A within-filing conflict has no usable value to keep; select a candidate or "
            "enter a value"
        )
    previous = db.scalars(
        select(ConflictResolution)
        .where(
            ConflictResolution.company_id == dataset.company_id,
            ConflictResolution.conflict_key == conflict_key,
        )
        .order_by(ConflictResolution.id.desc())
        .limit(1)
    ).first()
    if action == "revoke" and (previous is None or previous.action == "revoke"):
        raise DatasetError(f"No active resolution for {conflict_key} to revoke")
    resolution = ConflictResolution(
        company_id=dataset.company_id,
        conflict_key=conflict_key,
        action=action,
        candidate_fact_id=candidate_fact_id if action == "select_candidate" else None,
        candidate_source=candidate_source,
        value=value if action == "enter_value" else None,
        source_reference=source_reference.strip()
        if action == "enter_value" and source_reference
        else None,
        reason=reason_text,
        detected_in_dataset_id=dataset.id,
        line_item_code=conflict.line_item_code,
        period_type=period.period_type,
        period_start=period.start_date,
        period_end=period.end_date,
        created_by=user_id,
    )
    db.add(resolution)
    db.flush()
    audit.record(
        db,
        action={"override": "conflict_overridden", "revoke": "conflict_resolution_revoked"}.get(
            action, "conflict_resolved"
        ),
        entity_type="data_conflict",
        entity_id=conflict_key,
        user_id=user_id,
        before={
            "dataset_id": str(dataset.id),
            "status": conflict.status,
            "candidates": conflict.candidates,
            "previous_resolution_id": previous.id if previous else None,
        },
        after={
            "resolution_id": resolution.id,
            "action": action,
            "candidate_fact_id": resolution.candidate_fact_id,
            "candidate_source": resolution.candidate_source,
            "value": None if value is None else str(value),
            "source_reference": resolution.source_reference,
        },
        reason=reason_text,
    )
    return resolution


def rebuild_draft(
    db: Session,
    company: Company,
    *,
    mappings_root: Path,
    mapping_version: str,
    reason: str,
    user_id: uuid.UUID | None = None,
    basis: str = BASIS,
) -> BuildResult:
    """Discard the company's open draft of ``basis`` (if any) and build a new one with the
    current inputs and resolutions, keeping its selection policy and cut-off (for imports:
    the SEC comparison cut-off; the import cut-off advances to the latest import). Locked
    versions stay as they are; the new draft's parent is the latest locked version."""
    current = _current(db, company.id, basis)
    policy, as_of = Policy.LATEST_REPORTED, None
    if current is not None and current.status == "draft":
        policy, as_of = Policy(current.selection_policy), current.facts_as_of
        discard_dataset(db, current.id, reason=_meaningful(reason), user_id=user_id)
    build = build_import_dataset if basis == IMPORT_BASIS else build_dataset
    return build(
        db,
        company,
        mappings_root=mappings_root,
        mapping_version=mapping_version,
        policy=policy,
        facts_as_of=as_of,
        user_id=user_id,
    )
