"""Pure construction of a standardized financial dataset (Blueprint §3.3, §3.4, §4.4).

``build`` turns stored reported facts into a deterministic dataset:

1. **Eligible facts:** periodic-report forms only (``DATASET_FORMS``; registration and
   prospectus forms such as F-10 or 424B2 are excluded) filed on or before ``facts_as_of``.
   The cut-off defaults to the latest eligible filing date and is always recorded.
2. **Selection** (D11) and **resolution** onto standard line items (D49).
3. **Fiscal periods** from the reported windows (:mod:`.periods`). Balance-sheet values
   attach to the FY or quarter (Q1–Q3) ending on their date.
4. **Q4 = FY − 9M** for flow items when no quarter is reported for Q4: both inputs must be
   OK values of the *same concept and sign* (fallback is never mixed within one value),
   and the 9M window must start on the fiscal-year start. Per-share and count items are
   never derived (not additive). Derived values carry the rule and both source facts.
5. **Canonical serialization**: sorted JSON with normalized decimals. ``content_sha256``
   identifies the dataset; ``input_sha256`` fingerprints the eligible facts, so a later
   difference can be traced to the inputs, the mapping or the code.

Ambiguous values are stored without a value (status ``ambiguous``); they are never
replaced by a lower-priority concept and they block locking (D13).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from erp_backend.ingestion import coverage
from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import FactKey, Policy, fact_key, select
from erp_backend.standardization.conflicts import Conflict, Resolution
from erp_backend.standardization.conflicts import apply as apply_resolutions
from erp_backend.standardization.conflicts import detect as detect_conflicts
from erp_backend.standardization.conflicts import summarize as summarize_conflicts
from erp_backend.standardization.dataset_types import DatasetValue, decimal_str

__all__ = ["DatasetValue", "decimal_str"]  # re-exported for callers of this module
from erp_backend.standardization.derivations import derive
from erp_backend.standardization.mapping import MappingSet
from erp_backend.standardization.periods import (
    FiscalCalendar,
    FiscalPeriod,
    Window,
    resolve_periods,
)
from erp_backend.standardization.resolve import StandardValue, resolve
from erp_backend.standardization.validation import CheckResult, summarize, validate
from erp_backend.standardization.validation import sha256 as validation_sha256

FORMAT_VERSION = 3  # 2: YTD quarters, formulas and LTM (D51); 3: conflicts (D52)
ANNUAL_REPORT_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
DATASET_FORMS = ANNUAL_REPORT_FORMS | coverage.INTERIM_FORMS
# Which period a balance-sheet date attaches to when several end on it: FY first.
PERIOD_RANK = {"FY": 0, "Q1": 1, "Q2": 1, "Q3": 1, "Q4": 1, "H1": 2, "9M": 2, "LTM": 3}


class DatasetBuildError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetHeader:
    mapping_version: str
    mapping_sha256: str
    taxonomy: str
    currency: str
    selection_policy: str
    facts_as_of: date
    input_sha256: str
    # The serialization format the dataset was built with; stored rows of an older
    # version must re-serialize with their own format, not the code's current one.
    format_version: int = FORMAT_VERSION
    # Highest conflict-resolution id considered by the build (format 3+; D52).
    resolution_cutoff_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        extra = (
            {"resolution_cutoff_id": self.resolution_cutoff_id} if self.format_version >= 3 else {}
        )
        return extra | {
            "format": self.format_version,
            "mapping_version": self.mapping_version,
            "mapping_sha256": self.mapping_sha256,
            "taxonomy": self.taxonomy,
            "currency": self.currency,
            "selection_policy": self.selection_policy,
            "facts_as_of": self.facts_as_of.isoformat(),
            "input_sha256": self.input_sha256,
        }


def canonical_bytes(
    header: DatasetHeader,
    periods: list[FiscalPeriod],
    values: list[DatasetValue],
    conflicts: Iterable[Conflict] = (),
) -> bytes:
    doc: dict[str, Any] = {
        "header": header.as_dict(),
        "periods": [p.as_dict() for p in sorted(periods, key=FiscalPeriod.sort_key)],
        "values": [v.as_dict() for v in sorted(values, key=DatasetValue.sort_key)],
    }
    if header.format_version >= 3:
        doc["conflicts"] = [c.as_dict() for c in sorted(conflicts, key=Conflict.sort_key)]
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def input_sha256(facts: Mapping[RawFact, int]) -> str:
    """Fingerprint of the eligible facts (ids and every field that can affect selection)."""
    digest = hashlib.sha256()
    for f, fid in sorted(facts.items(), key=lambda kv: kv[1]):
        start = f.period_start.isoformat() if f.period_start else ""
        line = "|".join(
            (str(fid), f.taxonomy, f.concept, f.unit, decimal_str(f.value), start,
             f.period_end.isoformat(), f.form, f.filed_date.isoformat(), f.accession_no)
        )  # fmt: skip
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class BuiltDataset:
    header: DatasetHeader
    periods: tuple[FiscalPeriod, ...]
    values: tuple[DatasetValue, ...]
    interim_coverage: str
    summary: Mapping[str, Any]
    checks: tuple[CheckResult, ...] = ()
    conflicts: tuple[Conflict, ...] = ()

    @property
    def validation_sha256(self) -> str:
        return validation_sha256(self.checks)  # module-level function of the same name

    @property
    def canonical(self) -> bytes:
        return canonical_bytes(self.header, list(self.periods), list(self.values), self.conflicts)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()


def _reported(
    v: StandardValue, period: FiscalPeriod, fact_ids: Mapping[RawFact, int]
) -> DatasetValue:
    detail: dict[str, Any] = {"restated": v.restated}
    if v.alternatives:
        detail["alternatives"] = list(v.alternatives)
    if v.source is not None:
        detail.update(
            accession=v.source.accession_no,
            form=v.source.form,
            filed=v.source.filed_date.isoformat(),
        )
    if v.ambiguous_accessions:
        detail["ambiguous_accessions"] = list(v.ambiguous_accessions)
    return DatasetValue(
        period=period,
        line_item=v.line_item,
        status=v.status.value,
        value=v.value,
        unit=v.unit,
        provenance="reported",
        concept=v.concept,
        priority=v.priority,
        sign=v.sign,
        source_fact_ids=(fact_ids[v.source],) if v.source is not None else (),
        detail=detail,
    )


def build(
    facts: Mapping[RawFact, int],
    mapping: MappingSet,
    *,
    taxonomy: str,
    currency: str,
    policy: Policy = Policy.LATEST_REPORTED,
    facts_as_of: date | None = None,
    resolutions: Mapping[str, Resolution] | None = None,
    resolution_cutoff_id: int | None = None,
) -> BuiltDataset:
    """Build a dataset from facts (each mapped to its stored id). Pure and deterministic.

    ``resolutions`` (latest per conflict key, ids ≤ ``resolution_cutoff_id``) are applied
    to the detected conflicts before derivations and integrity checks (D52).
    """
    periodic = {
        f: i for f, i in facts.items() if f.form.upper() in DATASET_FORMS and f.taxonomy == taxonomy
    }
    if facts_as_of is None:
        if not periodic:
            raise DatasetBuildError(f"No {taxonomy} facts from periodic reports")
        facts_as_of = max(f.filed_date for f in periodic)
    eligible = {f: i for f, i in periodic.items() if f.filed_date <= facts_as_of}
    if not eligible:
        raise DatasetBuildError(f"No eligible {taxonomy} facts filed on or before {facts_as_of}")
    header = DatasetHeader(
        mapping.version,
        mapping.sha256,
        taxonomy,
        currency,
        policy.value,
        facts_as_of,
        input_sha256(eligible),
        resolution_cutoff_id=resolution_cutoff_id,
    )
    selections = select(eligible, facts_as_of)
    resolved = resolve(selections, mapping, taxonomy=taxonomy, currency=currency, policy=policy)

    weights: Counter[Window] = Counter()
    for v in resolved:
        if v.period_start is not None:
            weights[(v.period_start, v.period_end)] += 1
    calendar = FiscalCalendar.from_windows(weights)
    assigned, conflicting = resolve_periods(weights.items(), calendar)
    by_end: dict[date, FiscalPeriod] = {}
    ranked = sorted(assigned.values(), key=lambda p: (PERIOD_RANK[p.period_type], p.sort_key()))
    for candidate in ranked:
        if candidate.period_type in ("FY", "Q1", "Q2", "Q3"):
            by_end.setdefault(candidate.end, candidate)

    unassigned: Counter[str] = Counter()
    values: list[DatasetValue] = []
    for v in resolved:
        if v.period_start is None:
            target = by_end.get(v.period_end)
        else:
            target = assigned.get((v.period_start, v.period_end))
        if target is None:
            unassigned["instant" if v.period_start is None else "duration"] += 1
            continue
        values.append(_reported(v, target, facts))

    facts_by_key: dict[FactKey, list[RawFact]] = defaultdict(list)
    for f in eligible:
        facts_by_key[fact_key(f)].append(f)
    detected = detect_conflicts(
        values, taxonomy=taxonomy, selections=selections, facts_by_key=facts_by_key,
        fact_ids=eligible, mapping=mapping, policy=policy,
    )  # fmt: skip
    values, conflicts = apply_resolutions(values, detected, resolutions or {})

    derived, by_rule, skipped = derive(values, calendar.years, mapping)
    values.extend(derived)
    values.sort(key=DatasetValue.sort_key)
    periods = sorted({v.period for v in values}, key=FiscalPeriod.sort_key)

    per_type: dict[str, int] = defaultdict(int)
    for p in periods:
        per_type[p.period_type] += 1
    summary = {
        "eligible_facts": len(eligible),
        "excluded_facts_other_forms": sum(
            1 for f in facts if f.taxonomy == taxonomy and f.form.upper() not in DATASET_FORMS
        ),
        "values": len(values),
        "values_ambiguous": sum(1 for v in values if v.status == "ambiguous"),
        "values_derived": len(derived),
        "values_analyst": sum(1 for v in values if v.provenance == "analyst"),
        "values_restated": sum(1 for v in values if v.detail.get("restated")),
        "periods": dict(sorted(per_type.items())),
        "fiscal_years": [p.fiscal_year for p in calendar.years],
        "unassigned_values": dict(sorted(unassigned.items())),
        "rejected_annual_windows": [[s.isoformat(), e.isoformat()] for s, e in calendar.rejected],
        "conflicting_windows": [[s.isoformat(), e.isoformat()] for s, e in conflicting],
        "derived_by_rule": dict(sorted(by_rule.items())),
        "not_derived": dict(sorted(skipped.items())),
    }
    summary["conflicts"] = summarize_conflicts(conflicts)
    checks = validate(values, mapping, currency)
    summary["validation"] = summarize(checks)
    level = coverage.detect(list(eligible)).level
    return BuiltDataset(
        header, tuple(periods), tuple(values), level, summary, tuple(checks), tuple(conflicts)
    )


def value_key(v: DatasetValue) -> tuple[str, str, str, str]:
    return (v.period.period_type, v.period.start.isoformat(), v.period.end.isoformat(), v.line_item)


def diff(old: list[DatasetValue], new: list[DatasetValue]) -> dict[str, Any]:
    """Changes between two dataset versions (Blueprint §3.4 step 2), keyed by period+item."""
    before = {value_key(v): v for v in old}
    after = {value_key(v): v for v in new}
    changed = sorted(
        k
        for k in before.keys() & after.keys()
        if (before[k].status, before[k].value) != (after[k].status, after[k].value)
    )
    return {
        "added": len(after.keys() - before.keys()),
        "removed": len(before.keys() - after.keys()),
        "changed": len(changed),
        "changed_examples": [list(k) for k in changed[:20]],
    }
