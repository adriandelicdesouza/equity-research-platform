"""Damodaran reference datasets: validate, preview, then explicit versioned ingestion (D58).

Blueprint §4.2 (``damodaran``: ERP, CRP, industry betas, rating→spread table — annual
reference datasets), §3.2 (``ref.reference_datasets``), §7.5.2–§7.5.4 (how they are used:
as *suggested* WACC inputs with the table and edition recorded; not wired yet).

The published Damodaran files are spreadsheets whose layouts change between editions, so
the platform does not parse them directly: the analyst transcribes (or exports) the needed
columns into one of four strict CSV templates (``templates/reference/``, documented in
``docs/operations/reference-data-import.md``):

* ``damodaran_implied_erp.v1`` — implied ERP history for a registered index (``SP500``);
* ``damodaran_country_risk.v1`` — per-country default spread, CRP and total ERP;
* ``damodaran_industry_betas.v1`` — per-industry betas for one region;
* ``damodaran_rating_spreads.v1`` — interest-coverage → rating → spread, large or small cap.

**Units are explicit in every cell:** percentage fields must carry a ``%`` sign (``4.33%``)
and are stored in percentage points; ratio/multiple fields must not. A bare number in a
percent field is rejected, so 0.0433 vs 4.33 confusion cannot pass silently.

**Versioning:** a *lineage* is (kind, edition date, region, variant). Its first import is
version 1. A later file for the same lineage is compared record by record with the current
version: identical → nothing is stored (idempotent); any difference (changed, added or
removed records) needs ``supersede=True`` and a reason, and creates version *n+1* that
references the version it supersedes. Earlier versions are never modified (append-only).

Record statuses: ``accepted`` (new key), ``duplicate`` (same as the current version, or a
repeat within the file), ``conflicting`` (differs from the current version, or the same key
twice in the file with different values), ``unmapped`` (identifier outside the registry:
an unknown country code, index or rating) and ``rejected`` (invalid).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import pairwise
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.ingestion import archive
from erp_backend.market.tabular import FileFormatError, RawTable, read_table
from erp_backend.models import ReferenceDataset, ReferenceValue, SourceImport
from erp_backend.reference.countries import ISO_3166_ALPHA2

PROVIDER_CODE = "damodaran"
MAX_SKIPPED_STORED = 5000
MIN_REASON = 10
MAX_TEXT = 200
MATURE_ERP_TOLERANCE = Decimal("0.01")  # percentage points
COVERAGE_GAP_TOLERANCE = Decimal("0.01")
ZERO_CRP_COUNTRIES = frozenset({"US", "CA"})  # Aaa sovereigns, Blueprint §7.5.2 rule 4

_PLAIN = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_PERCENT = re.compile(r"^(-?[0-9]+(\.[0-9]+)?)\s*%$")
_INTEGER = re.compile(r"^[0-9]+$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class Kind(StrEnum):
    IMPLIED_ERP = "implied_erp"
    COUNTRY_RISK = "country_risk"
    INDUSTRY_BETAS = "industry_betas"
    RATING_SPREADS = "rating_spreads"


class Unit(StrEnum):
    PERCENT = "percent"  # percentage points
    RATIO = "ratio"
    MULTIPLE = "multiple"
    COUNT = "count"
    TEXT = "text"
    DATE = "date"


class Status(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICTING = "conflicting"
    UNMAPPED = "unmapped"
    REJECTED = "rejected"


class ImportRefusedError(Exception):
    """Ingestion (or validation set-up) refused; nothing was written."""


# Registries. Adding an entry is a deliberate code change, never inferred from a file.
INDEXES = frozenset({"SP500"})  # implied ERP: S&P 500 (Blueprint §7.5.2)
# Damodaran's regional beta tables (§7.5.3: no Canada-specific table; region is chosen by
# the analyst).
REGIONS = ("us", "europe", "japan", "aus_nz_canada", "emerging", "china", "india", "global")
VARIANTS = ("large_cap", "small_cap")  # synthetic-rating tables (§7.5.4)
# Rating scale used by the synthetic-rating table (S&P-style symbols), best to worst.
RATING_SCALE = (
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC+",
    "CCC",
    "CCC-",
    "CC",
    "C",
    "D",
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    unit: Unit
    required: bool


@dataclass(frozen=True)
class Template:
    code: str
    kind: Kind
    fields: tuple[FieldSpec, ...]
    needs_region: bool = False
    needs_variant: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def spec(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)


P, R, M, C, T, D = Unit.PERCENT, Unit.RATIO, Unit.MULTIPLE, Unit.COUNT, Unit.TEXT, Unit.DATE
TEMPLATES: dict[str, Template] = {
    t.code: t
    for t in (
        Template(
            "damodaran_implied_erp.v1",
            Kind.IMPLIED_ERP,
            (
                FieldSpec("as_of_date", D, True),
                FieldSpec("index", T, True),
                FieldSpec("implied_erp", P, True),
                FieldSpec("t_bond_rate", P, False),
                FieldSpec("method", T, False),
            ),
        ),
        Template(
            "damodaran_country_risk.v1",
            Kind.COUNTRY_RISK,
            (
                FieldSpec("country_name", T, True),
                FieldSpec("country_iso2", T, False),
                FieldSpec("region", T, False),
                FieldSpec("moodys_rating", T, False),
                FieldSpec("default_spread", P, False),
                FieldSpec("country_risk_premium", P, True),
                FieldSpec("total_erp", P, True),
                FieldSpec("corporate_tax_rate", P, False),
            ),
        ),
        Template(
            "damodaran_industry_betas.v1",
            Kind.INDUSTRY_BETAS,
            (
                FieldSpec("industry_name", T, True),
                FieldSpec("number_of_firms", C, True),
                FieldSpec("beta", R, False),
                FieldSpec("de_ratio", P, False),
                FieldSpec("effective_tax_rate", P, False),
                FieldSpec("unlevered_beta", R, True),
                FieldSpec("cash_firm_value", P, False),
                FieldSpec("unlevered_beta_cash_corrected", R, False),
            ),
            needs_region=True,
        ),
        Template(
            "damodaran_rating_spreads.v1",
            Kind.RATING_SPREADS,
            (
                FieldSpec("coverage_min", M, True),
                FieldSpec("coverage_max", M, True),
                FieldSpec("rating", T, True),
                FieldSpec("spread", P, True),
            ),
            needs_variant=True,
        ),
    )
}


# --------------------------------------------------------------------------- report types


@dataclass(frozen=True)
class RecordIssue:
    level: str  # "error" or "warning"
    code: str
    message: str
    column: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "column": self.column,
        }


Value = tuple[Unit, Decimal | str]


@dataclass(frozen=True)
class RecordResult:
    row: int
    locator: str
    status: Status
    record_key: str | None
    issues: tuple[RecordIssue, ...]
    storable: bool = False  # part of the dataset if this file is ingested
    values: dict[str, Value] | None = field(default=None, compare=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "locator": self.locator,
            "status": self.status.value,
            "record_key": self.record_key,
            "storable": self.storable,
            "issues": [i.as_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class Lineage:
    kind: Kind
    edition_date: date
    region: str | None
    variant: str | None

    @property
    def key(self) -> str:
        return (
            f"{self.kind.value}|{self.edition_date.isoformat()}|"
            f"{self.region or '-'}|{self.variant or '-'}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "edition_date": self.edition_date.isoformat(),
            "region": self.region,
            "variant": self.variant,
        }


@dataclass(frozen=True)
class ValidationReport:
    template: str
    lineage: Lineage
    filename: str
    file_sha256: str
    bytes: int
    file_kind: str | None
    file_errors: tuple[str, ...]
    records: tuple[RecordResult, ...]
    current_version: dict[str, Any] | None = None
    removed_keys: tuple[str, ...] = ()
    content_sha256: str | None = None

    def count(self, status: Status) -> int:
        return sum(1 for r in self.records if r.status is status)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "records": len(self.records),
            **{s.value: self.count(s) for s in Status},
            "storable": len(self.storable),
            "removed_from_current": len(self.removed_keys),
            "warnings": sum(1 for r in self.records for i in r.issues if i.level == "warning"),
        }

    @property
    def storable(self) -> tuple[RecordResult, ...]:
        return tuple(r for r in self.records if r.storable)

    @property
    def skipped(self) -> tuple[RecordResult, ...]:
        """Records that would not be part of the dataset (excluding exact in-file repeats)."""
        return tuple(r for r in self.records if not r.storable and r.status is not Status.DUPLICATE)

    @property
    def outcome(self) -> str:
        if self.file_errors:
            return "file_errors"
        if not self.storable:
            return "nothing_to_store"
        if self.current_version is None:
            return "new_dataset"
        if self.current_version["content_sha256"] == self.content_sha256:
            return "identical_to_current"
        return "new_version_requires_supersede"

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "lineage": self.lineage.as_dict(),
            "filename": self.filename,
            "file_sha256": self.file_sha256,
            "bytes": self.bytes,
            "file_errors": list(self.file_errors),
            "summary": self.summary,
            "outcome": self.outcome,
            "current_version": self.current_version,
            "removed_keys": list(self.removed_keys),
            "content_sha256": self.content_sha256,
            "records": [r.as_dict() for r in self.records],
        }


# --------------------------------------------------------------------------- cell parsing


class _Row:
    def __init__(self, cells: dict[str, str]) -> None:
        self.cells = cells
        self.issues: list[RecordIssue] = []
        self.unmapped = False

    def error(self, code: str, message: str, column: str | None = None) -> None:
        self.issues.append(RecordIssue("error", code, message, column))

    def warn(self, code: str, message: str, column: str | None = None) -> None:
        self.issues.append(RecordIssue("warning", code, message, column))

    def unmap(self, code: str, message: str, column: str) -> None:
        self.unmapped = True
        self.issues.append(RecordIssue("error", code, message, column))

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def parse(self, spec: FieldSpec, today: date) -> Decimal | str | None:
        raw = " ".join(self.cells.get(spec.name, "").split())
        if raw == "":
            if spec.required:
                self.error("missing_value", f"{spec.name} is required", spec.name)
            return None
        if spec.unit is Unit.TEXT:
            if len(raw) > MAX_TEXT:
                self.error("text_too_long", f"{spec.name} exceeds {MAX_TEXT} characters", spec.name)
                return None
            return raw
        if spec.unit is Unit.DATE:
            try:
                if not _ISO_DATE.fullmatch(raw):
                    raise ValueError
                value = date.fromisoformat(raw)
            except ValueError:
                self.error("invalid_date", f"{spec.name} {raw!r} is not YYYY-MM-DD", spec.name)
                return None
            if value > today:
                self.error("future_date", f"{spec.name} {raw} is after today", spec.name)
                return None
            return value.isoformat()
        if spec.unit is Unit.PERCENT:
            m = _PERCENT.fullmatch(raw)
            if m is None:
                code = "percent_sign_required" if _PLAIN.fullmatch(raw) else "invalid_number"
                self.error(
                    code,
                    f"{spec.name} {raw!r} must be a percentage written with '%' (e.g. 4.33%)",
                    spec.name,
                )
                return None
            return Decimal(m.group(1))
        if "%" in raw:
            self.error(
                "unexpected_percent",
                f"{spec.name} {raw!r} is a {spec.unit.value}, not a %",
                spec.name,
            )
            return None
        if spec.unit is Unit.COUNT:
            if not _INTEGER.fullmatch(raw):
                self.error(
                    "invalid_integer", f"{spec.name} {raw!r} is not a whole number", spec.name
                )
                return None
            return Decimal(int(raw))
        if not _PLAIN.fullmatch(raw):
            self.error(
                "invalid_number",
                f"{spec.name} {raw!r} is not a plain decimal (no separators or exponents)",
                spec.name,
            )
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:  # pragma: no cover - excluded by the pattern
            self.error("invalid_number", f"{spec.name} {raw!r}", spec.name)
            return None


def _range(row: _Row, name: str, value: Any, low: str, high: str, *, hard: bool) -> None:
    if not isinstance(value, Decimal):
        return
    if not Decimal(low) <= value <= Decimal(high):
        message = f"{name} {value} is outside [{low}, {high}]"
        if hard:
            row.error("out_of_range", message, name)
        else:
            row.warn("unusual_value", message, name)


# --------------------------------------------------------------------------- per-kind rules


def _record_key(tpl: Template, row: _Row, v: dict[str, Any]) -> str | None:
    if tpl.kind is Kind.IMPLIED_ERP:
        index = v.get("index")
        if index is None or v.get("as_of_date") is None:
            return None
        if str(index).upper() not in INDEXES:
            row.unmap("unknown_index", f"index {index!r} is not one of {sorted(INDEXES)}", "index")
            return None
        v["index"] = str(index).upper()
        return f"{v['index']}|{v['as_of_date']}"
    if tpl.kind is Kind.COUNTRY_RISK:
        code = v.get("country_iso2")
        if code is None:
            row.unmap(
                "unmapped_country",
                f"{v.get('country_name')!r} has no ISO 3166-1 alpha-2 code (aggregate or "
                "unmapped name)",
                "country_iso2",
            )
            return None
        code = str(code).upper()
        if code not in ISO_3166_ALPHA2:
            row.unmap(
                "unknown_country_code",
                f"{code!r} is not an officially assigned ISO 3166-1 alpha-2 code",
                "country_iso2",
            )
            return None
        v["country_iso2"] = code
        return code
    if tpl.kind is Kind.INDUSTRY_BETAS:
        name = v.get("industry_name")
        return str(name) if name is not None else None
    rating = v.get("rating")
    if rating is None:
        return None
    rating = str(rating).upper()
    if rating not in RATING_SCALE:
        row.unmap("unknown_rating", f"rating {rating!r} is not on the registered scale", "rating")
        return None
    v["rating"] = rating
    return rating


def _row_checks(tpl: Template, row: _Row, v: dict[str, Any], edition: date) -> None:
    if tpl.kind is Kind.IMPLIED_ERP:
        _range(row, "implied_erp", v.get("implied_erp"), "0.01", "25", hard=True)
        _range(row, "implied_erp", v.get("implied_erp"), "1", "10", hard=False)
        _range(row, "t_bond_rate", v.get("t_bond_rate"), "-5", "25", hard=True)
        as_of = v.get("as_of_date")
        if as_of is not None and date.fromisoformat(as_of) > edition:
            row.error(
                "after_edition",
                f"as_of_date {as_of} is after the edition date {edition}",
                "as_of_date",
            )
    elif tpl.kind is Kind.COUNTRY_RISK:
        crp, total = v.get("country_risk_premium"), v.get("total_erp")
        _range(row, "country_risk_premium", crp, "0", "50", hard=True)
        _range(row, "total_erp", total, "0", "60", hard=True)
        _range(row, "default_spread", v.get("default_spread"), "0", "50", hard=True)
        _range(row, "corporate_tax_rate", v.get("corporate_tax_rate"), "0", "100", hard=True)
        if isinstance(crp, Decimal) and isinstance(total, Decimal) and total < crp:
            row.error("total_below_crp", f"total_erp {total} is below the CRP {crp}", "total_erp")
    elif tpl.kind is Kind.INDUSTRY_BETAS:
        if v.get("number_of_firms") == 0:
            row.error("no_firms", "number_of_firms must be at least 1", "number_of_firms")
        for name in ("beta", "unlevered_beta", "unlevered_beta_cash_corrected"):
            _range(row, name, v.get(name), "-5", "10", hard=True)
            _range(row, name, v.get(name), "0", "4", hard=False)
        _range(row, "de_ratio", v.get("de_ratio"), "0", "10000", hard=True)
        _range(row, "effective_tax_rate", v.get("effective_tax_rate"), "0", "100", hard=True)
        _range(row, "cash_firm_value", v.get("cash_firm_value"), "0", "100", hard=True)
        levered, unlevered = v.get("beta"), v.get("unlevered_beta")
        if isinstance(levered, Decimal) and isinstance(unlevered, Decimal) and unlevered > levered:
            row.warn(
                "unlevered_above_levered",
                f"unlevered beta {unlevered} exceeds levered beta {levered}",
                "unlevered_beta",
            )
    else:
        low, high = v.get("coverage_min"), v.get("coverage_max")
        if isinstance(low, Decimal) and isinstance(high, Decimal) and high <= low:
            row.error(
                "empty_range", f"coverage_max {high} must exceed coverage_min {low}", "coverage_max"
            )
        _range(row, "spread", v.get("spread"), "0", "50", hard=True)


def _cross_checks(tpl: Template, rows: list[tuple[_Row, dict[str, Any]]]) -> None:
    """Checks across the file's valid records (rows still ok)."""
    valid = [(r, v) for r, v in rows if r.ok]
    if tpl.kind is Kind.COUNTRY_RISK:
        mature = {
            id(r): v["total_erp"] - v["country_risk_premium"]
            for r, v in valid
            if isinstance(v.get("total_erp"), Decimal)
        }
        if mature:
            mode, _ = Counter(mature.values()).most_common(1)[0]
            for r, _ in valid:
                implied = mature.get(id(r))
                if implied is not None and abs(implied - mode) > MATURE_ERP_TOLERANCE:
                    r.warn(
                        "inconsistent_mature_erp",
                        f"total_erp − CRP = {implied}, most rows imply {mode} (the mature ERP)",
                        "total_erp",
                    )
        for r, v in valid:
            if v.get("country_iso2") in ZERO_CRP_COUNTRIES and v["country_risk_premium"] != 0:
                r.warn(
                    "nonzero_crp_aaa",
                    f"{v['country_iso2']} CRP is {v['country_risk_premium']}; the Blueprint "
                    "default for Aaa sovereigns is 0 (§7.5.2)",
                    "country_risk_premium",
                )
    elif tpl.kind is Kind.RATING_SPREADS:
        ordered = sorted(valid, key=lambda rv: rv[1]["coverage_min"])
        for (r1, v1), (r2, v2) in pairwise(ordered):
            if v2["coverage_min"] < v1["coverage_max"]:
                for r in (r1, r2):
                    r.error(
                        "overlapping_range",
                        f"coverage ranges of {v1['rating']} and {v2['rating']} overlap",
                    )
            elif v2["coverage_min"] - v1["coverage_max"] > COVERAGE_GAP_TOLERANCE:
                r2.warn(
                    "coverage_gap",
                    f"gap between {v1['coverage_max']} and {v2['coverage_min']}",
                    "coverage_min",
                )
            if v2["spread"] > v1["spread"]:
                r2.warn(
                    "spread_not_decreasing",
                    f"{v2['rating']} (higher coverage) has a larger spread than {v1['rating']}",
                    "spread",
                )


# --------------------------------------------------------------------------- validation


def _fingerprint(values: dict[str, Value]) -> tuple[tuple[str, str, str], ...]:
    out = []
    for name, (unit, value) in sorted(values.items()):
        text = format(value.normalize(), "f") if isinstance(value, Decimal) else value
        out.append((name, unit.value, text))
    return tuple(out)


def content_hash(records: dict[str, dict[str, Value]]) -> str:
    canonical = [[key, list(_fingerprint(vals))] for key, vals in sorted(records.items())]
    data = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def lineage_for(
    tpl: Template, edition_date: date, region: str | None, variant: str | None, today: date
) -> Lineage:
    if edition_date > today:
        raise ImportRefusedError(f"Edition date {edition_date} is in the future")
    region = (region or "").strip().lower() or None
    variant = (variant or "").strip().lower() or None
    if tpl.needs_region:
        if region not in REGIONS:
            raise ImportRefusedError(
                f"{tpl.code} needs --region, one of: {', '.join(REGIONS)} (got {region!r})"
            )
    elif region is not None:
        raise ImportRefusedError(f"{tpl.code} does not take a region")
    if tpl.needs_variant:
        if variant not in VARIANTS:
            raise ImportRefusedError(
                f"{tpl.code} needs --variant, one of: {', '.join(VARIANTS)} (got {variant!r})"
            )
    elif variant is not None:
        raise ImportRefusedError(f"{tpl.code} does not take a variant")
    return Lineage(tpl.kind, edition_date, region, variant)


def current_version(db: Session, lineage: Lineage) -> ReferenceDataset | None:
    return db.scalars(
        select(ReferenceDataset)
        .where(ReferenceDataset.lineage_key == lineage.key)
        .order_by(ReferenceDataset.version_no.desc())
        .limit(1)
    ).first()


def dataset_records(db: Session, dataset_id: uuid.UUID) -> dict[str, dict[str, Value]]:
    out: dict[str, dict[str, Value]] = {}
    for v in db.scalars(
        select(ReferenceValue)
        .where(ReferenceValue.dataset_id == dataset_id)
        .order_by(ReferenceValue.id)
    ):
        value: Decimal | str = v.value if v.value is not None else str(v.value_text)
        out.setdefault(v.record_key, {})[v.field] = (Unit(v.unit), value)
    return out


def _version_summary(ds: ReferenceDataset) -> dict[str, Any]:
    return {
        "id": str(ds.id),
        "version_no": ds.version_no,
        "record_count": ds.record_count,
        "content_sha256": ds.content_sha256,
        "import_id": str(ds.import_id),
    }


def _check_header(tpl: Template, table: RawTable) -> list[str]:
    names = [h.strip().lower() for h in table.header]
    errors: list[str] = []
    if any(n == "" for n in names):
        errors.append("Header has an empty column name")
    duplicates = sorted({n for n in names if n and names.count(n) > 1})
    if duplicates:
        errors.append(f"Duplicate columns: {', '.join(duplicates)}")
    unknown = sorted({n for n in names if n and n not in tpl.names})
    if unknown:
        errors.append(
            f"Unknown columns for {tpl.code}: {', '.join(unknown)} "
            f"(allowed: {', '.join(tpl.names)})"
        )
    missing = [f.name for f in tpl.fields if f.required and f.name not in names]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
    return errors


def validate(
    db: Session,
    *,
    filename: str,
    data: bytes,
    template: str,
    edition_date: date,
    region: str | None = None,
    variant: str | None = None,
    today: date | None = None,
) -> ValidationReport:
    """Validate a file for one lineage and compare it with the current version. Reads only."""
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise ImportRefusedError(f"Unknown template {template!r}; one of: {', '.join(TEMPLATES)}")
    today = today or datetime.now(UTC).date()
    lineage = lineage_for(tpl, edition_date, region, variant, today)
    name = PurePath(filename).name
    sha = hashlib.sha256(data).hexdigest()

    def report(errors: list[str], kind: str | None) -> ValidationReport:
        return ValidationReport(tpl.code, lineage, name, sha, len(data), kind, tuple(errors), ())

    try:
        table = read_table(name, data)
    except FileFormatError as exc:
        return report([str(exc)], None)
    errors = _check_header(tpl, table)
    if errors:
        return report(errors, table.kind)
    if not table.rows:
        return report(["File has a header but no data rows"], table.kind)

    header = [h.strip().lower() for h in table.header]
    parsed: list[tuple[_Row, dict[str, Any], str | None]] = []
    for raw in table.rows:
        if len(raw.cells) != len(header):
            row = _Row({})
            row.error("field_count", f"{len(raw.cells)} fields; the header has {len(header)}")
            parsed.append((row, {}, None))
            continue
        row = _Row(dict(zip(header, raw.cells, strict=True)))
        raw_values = {f.name: row.parse(f, today) for f in tpl.fields if f.name in header}
        values: dict[str, Any] = {k: v for k, v in raw_values.items() if v is not None}
        key = _record_key(tpl, row, values) if row.ok else None
        if row.ok:
            _row_checks(tpl, row, values, edition_date)
        parsed.append((row, values, key))
    _cross_checks(tpl, [(r, v) for r, v, k in parsed if k is not None])

    current = current_version(db, lineage)
    stored = dataset_records(db, current.id) if current is not None else {}
    stored_fp = {k: _fingerprint(v) for k, v in stored.items()}

    typed: list[dict[str, Value] | None] = []
    for row, values, key in parsed:
        if key is None or not row.ok:
            typed.append(None)
            continue
        typed.append({n: (tpl.spec(n).unit, val) for n, val in values.items()})

    by_key: dict[str, list[int]] = {}
    for i, ((_row, _, key), vals) in enumerate(zip(parsed, typed, strict=True)):
        if vals is not None and key is not None:
            by_key.setdefault(key, []).append(i)

    statuses: list[Status] = []
    storable = [False] * len(parsed)
    for row, _, key in parsed:
        if row.unmapped:
            statuses.append(Status.UNMAPPED)
        elif not row.ok or key is None:
            statuses.append(Status.REJECTED)
        else:
            statuses.append(Status.ACCEPTED)
    for key, indexes in by_key.items():
        fps = {_fingerprint(typed[i]) for i in indexes}  # type: ignore[arg-type]
        if len(fps) > 1:
            for i in indexes:
                parsed[i][0].error("conflict_in_file", f"{key!r} appears with different values")
                statuses[i] = Status.CONFLICTING
            continue
        first = indexes[0]
        for i in indexes[1:]:
            parsed[i][0].warn("duplicate_in_file", f"repeats row {table.rows[first].number}")
            statuses[i] = Status.DUPLICATE
        storable[first] = True
        fp = next(iter(fps))
        if key in stored_fp:
            if stored_fp[key] == fp:
                statuses[first] = Status.DUPLICATE
            else:
                parsed[first][0].warn(
                    "differs_from_current_version",
                    f"{key!r} differs from version {current.version_no} "  # type: ignore[union-attr]
                    "(storing it requires a superseding version)",
                )
                statuses[first] = Status.CONFLICTING

    records = [
        RecordResult(
            raw.number,
            raw.locator,
            statuses[i],
            key,
            tuple(row.issues),
            storable[i],
            typed[i] if storable[i] else None,
        )
        for i, (raw, (row, _, key)) in enumerate(zip(table.rows, parsed, strict=True))
    ]
    kept = {r.record_key: r.values for r in records if r.storable and r.record_key and r.values}
    removed = tuple(sorted(set(stored) - set(kept)))
    return ValidationReport(
        tpl.code,
        lineage,
        name,
        sha,
        len(data),
        table.kind,
        (),
        tuple(records),
        _version_summary(current) if current is not None else None,
        removed,
        content_hash(kept) if kept else None,
    )


# --------------------------------------------------------------------------- ingestion


@dataclass(frozen=True)
class IngestResult:
    status: str  # "created", "superseded" or "identical_to_current"
    dataset_id: uuid.UUID | None
    import_id: uuid.UUID | None
    version_no: int | None
    stored_records: int
    report: ValidationReport


def ingest(
    db: Session,
    *,
    filename: str,
    data: bytes,
    template: str,
    edition_date: date,
    source_name: str,
    expected_sha256: str,
    raw_root: Path,
    region: str | None = None,
    variant: str | None = None,
    source_reference: str | None = None,
    allow_partial: bool = False,
    supersede: bool = False,
    reason: str | None = None,
    user_id: uuid.UUID | None = None,
    today: date | None = None,
) -> IngestResult:
    """Store a validated file as a new dataset version. The caller commits."""
    source_name = source_name.strip()
    if not source_name:
        raise ImportRefusedError("A source attribution (--source) is required")
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise ImportRefusedError(f"Unknown template {template!r}; one of: {', '.join(TEMPLATES)}")
    today = today or datetime.now(UTC).date()
    lineage = lineage_for(tpl, edition_date, region, variant, today)
    # Serialize imports of one lineage: version numbers and the comparison base stay exact.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext("ref:" + lineage.key))))
    report = validate(
        db,
        filename=filename,
        data=data,
        template=template,
        edition_date=edition_date,
        region=region,
        variant=variant,
        today=today,
    )
    if expected_sha256.strip().lower() != report.file_sha256:
        raise ImportRefusedError(
            f"File SHA-256 is {report.file_sha256}, not the reviewed {expected_sha256}; "
            "validate this exact file first"
        )
    if report.file_errors:
        raise ImportRefusedError("File-level errors: " + "; ".join(report.file_errors))
    skipped = report.skipped
    if skipped and not allow_partial:
        counts = Counter(r.status.value for r in skipped)
        raise ImportRefusedError(
            f"{len(skipped)} record(s) would be skipped ({dict(sorted(counts.items()))}); "
            "fix the file or pass --allow-partial"
        )
    storable = report.storable
    if not storable:
        raise ImportRefusedError("No storable records")
    current = current_version(db, lineage)
    if current is not None and current.content_sha256 == report.content_sha256:
        return IngestResult("identical_to_current", current.id, None, current.version_no, 0, report)
    if current is not None and not supersede:
        s = report.summary
        raise ImportRefusedError(
            f"{lineage.key} already has version {current.version_no}; this file changes it "
            f"({s['conflicting']} changed, {s['accepted']} new, {len(report.removed_keys)} "
            "removed record(s)). Imports never overwrite: pass --supersede with --reason to "
            "create a new version (the current one is kept)"
        )
    if current is None and supersede:
        raise ImportRefusedError(f"Nothing to supersede: {lineage.key} has no version yet")
    reason = (reason or "").strip() or None
    if supersede and (reason is None or len(reason) < MIN_REASON):
        raise ImportRefusedError(f"--reason of at least {MIN_REASON} characters is required")

    stored = archive.store(raw_root, PROVIDER_CODE, data)
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
        summary={**report.summary, "lineage": report.lineage.as_dict()},
        skipped=[r.as_dict() for r in skipped[:MAX_SKIPPED_STORED]],
        imported_by=user_id,
    )
    db.add(record)
    db.flush()
    dataset = ReferenceDataset(
        id=uuid.uuid4(),
        provider_code=PROVIDER_CODE,
        dataset_kind=lineage.kind.value,
        template_version=report.template,
        edition_date=lineage.edition_date,
        region=lineage.region,
        variant=lineage.variant,
        lineage_key=lineage.key,
        version_no=(current.version_no + 1) if current is not None else 1,
        supersedes_id=current.id if current is not None else None,
        import_id=record.id,
        record_count=len(storable),
        content_sha256=report.content_sha256,
        reason=reason,
        created_by=user_id,
    )
    db.add(dataset)
    db.flush()
    rows = [
        ReferenceValue(
            dataset_id=dataset.id,
            record_key=r.record_key,
            field=name,
            value=value if isinstance(value, Decimal) else None,
            value_text=None if isinstance(value, Decimal) else value,
            unit=unit.value,
            import_row=r.row,
            locator=r.locator,
        )
        for r in storable
        if r.values is not None and r.record_key is not None
        for name, (unit, value) in sorted(r.values.items())
    ]
    db.add_all(rows)
    audit.record(
        db,
        action="reference_dataset_imported",
        entity_type="reference_dataset",
        entity_id=str(dataset.id),
        user_id=user_id,
        before=_version_summary(current) if current is not None else None,
        after={
            "lineage": report.lineage.as_dict(),
            "version_no": dataset.version_no,
            "import_id": str(record.id),
            "file_sha256": report.file_sha256,
            "content_sha256": report.content_sha256,
            "source_name": source_name,
            "summary": report.summary,
            "removed_keys": list(report.removed_keys)[:200],
        },
        reason=reason,
    )
    db.flush()
    return IngestResult(
        "superseded" if current is not None else "created",
        dataset.id,
        record.id,
        dataset.version_no,
        len(storable),
        report,
    )


def list_datasets(db: Session, kind: str | None = None) -> list[ReferenceDataset]:
    query = select(ReferenceDataset).order_by(
        ReferenceDataset.dataset_kind,
        ReferenceDataset.edition_date.desc(),
        ReferenceDataset.lineage_key,
        ReferenceDataset.version_no.desc(),
    )
    if kind is not None:
        query = query.where(ReferenceDataset.dataset_kind == kind)
    return list(db.scalars(query))
