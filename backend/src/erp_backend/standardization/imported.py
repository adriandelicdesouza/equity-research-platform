"""Pure construction of an import dataset (``basis = 'reported'``; Blueprint §4.6; D57).

Inputs are the stored source records of financial-statement imports
(``source.statement_import_values``), already expressed in standard line-item codes, the
line item's sign convention and full units. A build is deterministic:

1. **Eligible records:** every record of the company with ``id ≤ import_cutoff_id``.
2. **Policy value per (period, line item):** ``latest_reported`` takes the most recently
   imported record, ``first_reported`` the earliest. Every other record stays a source.
3. **Conflicts** (§10.3), reusing the SEC materiality rule and conflict types:

   * ``cross_import`` — two imports disagree for one period and line item;
   * ``cross_source`` — an SEC value (latest-reported, as of ``facts_as_of``, computed by
     the unchanged SEC build) exists for the same period window and differs from an
     imported value. The SEC value is then a candidate too.

   Immaterial ones are ``auto_resolved_immaterial`` (the policy value stays); material ones
   are ``open`` and block locking. Analyst resolutions (D52) apply on the next build:
   select an imported or SEC candidate, enter a value with a source, or override.
4. **Derivations** (Q4 = FY − 9M, quarters from YTD, subtotal formulas, LTM) and
   **integrity checks** are the existing D51 functions, run on the resolved values.
5. **Canonical bytes** (sorted JSON): header, periods, values (with import provenance),
   conflicts. ``input_sha256`` fingerprints the eligible records and the SEC reference.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from erp_backend.ingestion.selection import Policy
from erp_backend.standardization.conflicts import (
    Candidate,
    Conflict,
    Resolution,
    materiality,
)
from erp_backend.standardization.conflicts import summarize as summarize_conflicts
from erp_backend.standardization.dataset_types import DatasetValue, decimal_str
from erp_backend.standardization.derivations import derive
from erp_backend.standardization.mapping import MappingSet
from erp_backend.standardization.periods import FiscalPeriod
from erp_backend.standardization.validation import CheckResult, summarize, validate
from erp_backend.standardization.validation import sha256 as validation_sha256

BASIS = "reported"
TAXONOMY = "standard"  # input codes are the platform's standard line items
FORMAT_VERSION = 1  # format versions are per basis; this is import format 1
PROVENANCE = "manual_import"
INTERIM_TYPES = frozenset({"Q1", "Q2", "Q3", "Q4", "H1", "9M"})


class ImportBuildError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedRecord:
    """One stored import value (``source.statement_import_values``)."""

    id: int
    import_id: str
    imported_on: date
    filename: str
    locator: str
    line_item: str
    period: FiscalPeriod
    value: Decimal  # normalized: reported_value × 10^scale, line-item sign convention
    unit: str
    reported_value: Decimal
    scale: int
    source_document: str
    source_page: str | None

    def fingerprint(self) -> str:
        p = self.period
        return "|".join(
            (str(self.id), self.import_id, self.imported_on.isoformat(), self.line_item,
             p.period_type, str(p.fiscal_year), p.start.isoformat(), p.end.isoformat(),
             decimal_str(self.value), self.unit, decimal_str(self.reported_value),
             str(self.scale), self.source_document, self.source_page or "", self.locator)
        )  # fmt: skip

    def source_detail(self) -> dict[str, Any]:
        return {
            "file": self.filename,
            "locator": self.locator,
            "imported_on": self.imported_on.isoformat(),
            "reported_value": decimal_str(self.reported_value),
            "scale": self.scale,
            "source_document": self.source_document,
            "source_page": self.source_page,
        }

    def candidate(self) -> Candidate:
        return Candidate(
            self.id, self.import_id, "import", self.imported_on, self.line_item, self.value,
            "imported", source="import_value",
        )  # fmt: skip


@dataclass(frozen=True)
class SecReference:
    """Latest-reported SEC values used for cross-source comparison (or none)."""

    taxonomy: str
    facts_as_of: date
    input_sha256: str
    values: Mapping[tuple[Any, ...], DatasetValue]

    @classmethod
    def from_values(
        cls,
        values: Iterable[DatasetValue],
        mapping: MappingSet,
        *,
        taxonomy: str,
        facts_as_of: date,
        input_sha256: str,
    ) -> SecReference:
        usable = {
            match_key(mapping, v.period, v.line_item): v
            for v in values
            if v.provenance == "reported" and v.status == "ok" and v.source_fact_ids
        }
        return cls(taxonomy, facts_as_of, input_sha256, usable)


def match_key(mapping: MappingSet, period: FiscalPeriod, code: str) -> tuple[Any, ...]:
    """How an imported value meets an SEC value: same window (durations) or same date
    (balances, which attach to the FY or quarter ending that day)."""
    if mapping.line_items[code].period == "instant":
        return ("instant", period.end, code)
    return (period.period_type, period.start, period.end, code)


@dataclass(frozen=True)
class ImportHeader:
    mapping_version: str
    mapping_sha256: str
    currency: str
    selection_policy: str
    facts_as_of: date
    input_sha256: str
    import_cutoff_id: int
    resolution_cutoff_id: int | None
    sec_taxonomy: str | None
    sec_input_sha256: str | None
    taxonomy: str = TAXONOMY
    format_version: int = FORMAT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis": BASIS,
            "format": self.format_version,
            "mapping_version": self.mapping_version,
            "mapping_sha256": self.mapping_sha256,
            "taxonomy": self.taxonomy,
            "currency": self.currency,
            "selection_policy": self.selection_policy,
            "facts_as_of": self.facts_as_of.isoformat(),
            "input_sha256": self.input_sha256,
            "import_cutoff_id": self.import_cutoff_id,
            "resolution_cutoff_id": self.resolution_cutoff_id,
            "sec_reference": None
            if self.sec_taxonomy is None
            else {"taxonomy": self.sec_taxonomy, "input_sha256": self.sec_input_sha256},
        }


def canonical_bytes(
    header: ImportHeader,
    periods: Iterable[FiscalPeriod],
    values: Iterable[DatasetValue],
    conflicts: Iterable[Conflict],
) -> bytes:
    doc = {
        "header": header.as_dict(),
        "periods": [p.as_dict() for p in sorted(periods, key=FiscalPeriod.sort_key)],
        "values": [v.as_dict() for v in sorted(values, key=DatasetValue.sort_key)],
        "conflicts": [c.as_dict() for c in sorted(conflicts, key=Conflict.sort_key)],
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def input_sha256(records: Iterable[ImportedRecord], sec: SecReference | None) -> str:
    digest = hashlib.sha256()
    for r in sorted(records, key=lambda r: r.id):
        digest.update(r.fingerprint().encode() + b"\n")
    digest.update(f"sec:{sec.input_sha256 if sec else '-'}".encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class BuiltImport:
    header: ImportHeader
    periods: tuple[FiscalPeriod, ...]
    values: tuple[DatasetValue, ...]
    interim_coverage: str
    summary: Mapping[str, Any]
    checks: tuple[CheckResult, ...] = ()
    conflicts: tuple[Conflict, ...] = field(default=())

    @property
    def validation_sha256(self) -> str:
        return validation_sha256(self.checks)

    @property
    def canonical(self) -> bytes:
        return canonical_bytes(self.header, self.periods, self.values, self.conflicts)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()


def _imported_value(r: ImportedRecord, extra: Mapping[str, Any] | None = None) -> DatasetValue:
    return DatasetValue(
        period=r.period,
        line_item=r.line_item,
        status="ok",
        value=r.value,
        unit=r.unit,
        provenance=PROVENANCE,
        concept=r.line_item,
        priority=1,
        sign=1,
        source_fact_ids=(),
        detail={**r.source_detail(), **(extra or {})},
        import_value_id=r.id,
        import_id=r.import_id,
    )


def _sec_candidate(v: DatasetValue) -> Candidate:
    assert v.value is not None
    return Candidate(
        v.source_fact_ids[0], str(v.detail.get("accession")), str(v.detail.get("form")),
        date.fromisoformat(str(v.detail.get("filed"))), v.concept, v.value, "sec_reported",
    )  # fmt: skip


def _key(kind: str, code: str, period: FiscalPeriod, cands: Iterable[Candidate]) -> str:
    """Conflict fingerprint; like D52's, but each candidate carries its id space."""
    doc = [kind, code, period.period_type, period.start.isoformat(), period.end.isoformat(),
           sorted([c.source, c.fact_id, decimal_str(c.value)] for c in cands)]  # fmt: skip
    return hashlib.sha256(json.dumps(doc, separators=(",", ":")).encode()).hexdigest()[:32]


def _base(
    index: Mapping[tuple[FiscalPeriod, str], DatasetValue], p: FiscalPeriod
) -> Decimal | None:
    for code in ("BS.TOTAL_ASSETS", "IS.REVENUE"):
        v = index.get((p, code))
        if v is not None and v.value is not None:
            return v.value
    return None


def detect(
    groups: Mapping[tuple[FiscalPeriod, str], Sequence[ImportedRecord]],
    chosen: Mapping[tuple[FiscalPeriod, str], ImportedRecord],
    index: Mapping[tuple[FiscalPeriod, str], DatasetValue],
    mapping: MappingSet,
    sec: SecReference | None,
    policy: Policy,
) -> tuple[list[Conflict], dict[tuple[FiscalPeriod, str], DatasetValue], dict[str, int]]:
    """Conflicts per (period, item); returns conflicts, SEC confirmations and counts."""
    conflicts: list[Conflict] = []
    confirmed: dict[tuple[FiscalPeriod, str], DatasetValue] = {}
    counts = {"compared": 0, "confirmed": 0, "differing": 0}
    ordered = sorted(groups.items(), key=lambda kv: (kv[0][0].sort_key(), kv[0][1]))
    for (period, code), recs in ordered:
        pick = chosen[(period, code)]
        imports_disagree = len({r.value for r in recs}) > 1
        sec_v = sec.values.get(match_key(mapping, period, code)) if sec else None
        if sec_v is not None:
            counts["compared"] += 1
            if sec_v.value == pick.value and not imports_disagree:
                counts["confirmed"] += 1
                confirmed[(period, code)] = sec_v
                continue
            if sec_v.value != pick.value:
                counts["differing"] += 1
        if not imports_disagree and (sec_v is None or sec_v.value == pick.value):
            continue
        cands = [r.candidate() for r in recs] if imports_disagree else [pick.candidate()]
        if sec_v is not None:
            cands.append(_sec_candidate(sec_v))
        kind = "cross_source" if sec_v is not None else "cross_import"
        m = materiality(code, (c.value for c in cands), _base(index, period))
        detail: dict[str, Any] = {
            "policy": policy.value,
            "imports": {str(r.id): r.source_detail() for r in recs},
        }
        if sec_v is not None:
            detail["sec_taxonomy"] = sec.taxonomy if sec else None
        conflicts.append(
            Conflict(
                _key(kind, code, period, cands), kind, code, period, tuple(cands), pick.id, m,
                "open" if m.is_material else "auto_resolved_immaterial", detail=detail,
            )
        )  # fmt: skip
    return conflicts, confirmed, counts


def apply(
    values: Iterable[DatasetValue],
    conflicts: Iterable[Conflict],
    resolutions: Mapping[str, Resolution],
    records: Mapping[int, ImportedRecord],
) -> tuple[list[DatasetValue], list[Conflict]]:
    """Apply the latest resolution per conflict key (D52 semantics, import id spaces)."""
    index = {(v.period, v.line_item): v for v in values}
    out: list[Conflict] = []
    for c in conflicts:
        res = resolutions.get(c.key)
        if res is None or res.action == "revoke":
            out.append(c)
            continue
        v = index[(c.period, c.line_item)]
        marker = {"conflict": c.key, **res.as_dict()}
        if res.action == "select_candidate":
            matches = [
                x
                for x in c.candidates
                if x.fact_id == res.candidate_fact_id
                and (res.candidate_source is None or x.source == res.candidate_source)
            ]
            if len(matches) != 1:
                out.append(replace(c, detail={**c.detail, "unapplied_resolution": res.id}))
                continue
            cand = matches[0]
            if cand.source == "import_value":
                new = _imported_value(records[cand.fact_id], {"resolution": marker})
            else:
                new = replace(
                    v, value=cand.value, provenance="reported", concept=cand.concept,
                    source_fact_ids=(cand.fact_id,), import_value_id=None, import_id=None,
                    detail={"accession": cand.accession, "form": cand.form,
                            "filed": cand.filed.isoformat(), "resolution": marker},
                )  # fmt: skip
            index[(c.period, c.line_item)] = new
            out.append(replace(c, status="resolved", resolution=res))
        elif res.action == "enter_value":
            assert res.value is not None
            index[(c.period, c.line_item)] = replace(
                v, value=res.value, provenance="analyst", concept=c.line_item,
                source_fact_ids=(), import_value_id=None, import_id=None,
                detail={"resolution": {**marker, "source_reference": res.source_reference},
                        "candidates": [[x.source, x.fact_id] for x in c.candidates]},
            )  # fmt: skip
            out.append(replace(c, status="resolved", resolution=res))
        elif res.action == "override":
            index[(c.period, c.line_item)] = replace(v, detail={**v.detail, "resolution": marker})
            out.append(replace(c, status="overridden", resolution=res))
        else:  # pragma: no cover - ACTIONS is closed
            out.append(c)
    return list(index.values()), out


def build(
    records: Iterable[ImportedRecord],
    mapping: MappingSet,
    *,
    currency: str,
    import_cutoff_id: int,
    facts_as_of: date,
    sec: SecReference | None,
    policy: Policy = Policy.LATEST_REPORTED,
    resolutions: Mapping[str, Resolution] | None = None,
    resolution_cutoff_id: int | None = None,
) -> BuiltImport:
    """Build an import dataset. Pure and deterministic."""
    eligible = sorted((r for r in records if r.id <= import_cutoff_id), key=lambda r: r.id)
    if not eligible:
        raise ImportBuildError("No imported statement values for this company")
    unknown = sorted({r.line_item for r in eligible if r.line_item not in mapping.line_items})
    if unknown:
        raise ImportBuildError(
            f"Imported codes not in mapping {mapping.version}: {', '.join(unknown)}"
        )
    header = ImportHeader(
        mapping.version, mapping.sha256, currency, policy.value, facts_as_of,
        input_sha256(eligible, sec), import_cutoff_id, resolution_cutoff_id,
        sec.taxonomy if sec else None, sec.input_sha256 if sec else None,
    )  # fmt: skip

    groups: dict[tuple[FiscalPeriod, str], list[ImportedRecord]] = defaultdict(list)
    for r in eligible:
        groups[(r.period, r.line_item)].append(r)
    chosen = {
        k: (recs[-1] if policy is Policy.LATEST_REPORTED else recs[0]) for k, recs in groups.items()
    }
    index: dict[tuple[FiscalPeriod, str], DatasetValue] = {}
    for k, pick in chosen.items():
        others = [r.id for r in groups[k] if r.id != pick.id]
        index[k] = _imported_value(pick, {"other_import_values": others} if others else None)

    detected, confirmed, sec_counts = detect(groups, chosen, index, mapping, sec, policy)
    for k, sec_v in confirmed.items():
        index[k] = replace(
            index[k],
            detail={**index[k].detail, "sec_confirmed": [sec_v.source_fact_ids[0],
                                                         sec_v.detail.get("accession")]},
        )  # fmt: skip
    by_id = {r.id: r for r in eligible}
    values, conflicts = apply(list(index.values()), detected, resolutions or {}, by_id)

    years = sorted({v.period for v in values if v.period.period_type == "FY"}, key=lambda p: p.end)
    derived, by_rule, skipped = derive(values, years, mapping)
    values.extend(derived)
    values.sort(key=DatasetValue.sort_key)
    periods = sorted({v.period for v in values}, key=FiscalPeriod.sort_key)
    per_type: dict[str, int] = defaultdict(int)
    for p in periods:
        per_type[p.period_type] += 1
    checks = validate(values, mapping, currency)
    summary: dict[str, Any] = {
        "basis": BASIS,
        "eligible_import_values": len(eligible),
        "imports": sorted({r.import_id for r in eligible}),
        "values": len(values),
        "values_imported": sum(1 for v in values if v.provenance == PROVENANCE),
        "values_reported_sec": sum(1 for v in values if v.provenance == "reported"),
        "values_analyst": sum(1 for v in values if v.provenance == "analyst"),
        "values_derived": len(derived),
        "values_ambiguous": 0,
        "periods": dict(sorted(per_type.items())),
        "fiscal_years": [p.fiscal_year for p in years],
        "derived_by_rule": dict(sorted(by_rule.items())),
        "not_derived": dict(sorted(skipped.items())),
        "sec_reference": None
        if sec is None
        else {"taxonomy": sec.taxonomy, "facts_as_of": sec.facts_as_of.isoformat(), **sec_counts},
        "conflicts": summarize_conflicts(conflicts),
        "validation": summarize(checks),
    }
    coverage = "partial" if any(p.period_type in INTERIM_TYPES for p in periods) else "none"
    return BuiltImport(
        header, tuple(periods), tuple(values), coverage, summary, tuple(checks),
        tuple(sorted(conflicts, key=Conflict.sort_key)),
    )  # fmt: skip
