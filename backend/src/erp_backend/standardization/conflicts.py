"""Data-conflict detection, materiality and resolution application (Blueprint §10.3; D13, D52).

Pure. Conflicts are detected on the reported values of a dataset build, before
derivations, so derived values and integrity checks see resolved inputs.

Two kinds of genuine conflict (competing values for one company, line item and period):

* ``within_filing``: one filing reports the same fact key with different values
  (selection status AMBIGUOUS, D11). No value is used. Always material (§4.4).
* ``mixed_vintage``: the item was **materially or immaterially restated** (first-reported ≠
  latest-reported), and the value the selection policy chose comes from a newer filing
  than the values of items it is tied to by an accounting identity in the same period
  (balance sheet, a subtotal, the cash roll-forward, IS/CF net income). Those siblings
  were not re-reported by the restating filing, so the period mixes filing vintages.
  Candidates: the first-reported and latest-reported facts.

Not conflicts (legitimate differences, kept visible elsewhere): other durations or units
(different fact keys), other concepts (different XBRL definitions = different scope;
recorded as ``alternatives`` on the value), and restatements whose related items were
re-reported by the same filing (consistent vintage; flagged ``restated`` on the value).

**Materiality** (§10.3): any sign disagreement is material. Otherwise material when
``|a − b| > max(r × |larger|, 0.05% × base)`` with r = 0.1% for DCF-critical items and 0.5%
for others; base = the period's total assets, else its revenue, else none.
Immaterial conflicts are ``auto_resolved_immaterial`` (the policy's value is kept).

**Resolutions** are analyst decisions recorded outside the dataset, keyed by the
conflict's fingerprint (``key``: kind, item, period and every candidate's fact id and
value). They apply to the next build: ``select_candidate`` (use a candidate's reported
fact), ``enter_value`` (analyst value with a source), ``override`` (keep the policy value,
documented; not possible for ``within_filing``, which has no value), ``revoke``. A
changed candidate set changes the key, so an old resolution never applies to new facts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import FactKey, Policy, Selection, order_key
from erp_backend.standardization.dataset_types import DatasetValue, decimal_str
from erp_backend.standardization.mapping import MappingSet
from erp_backend.standardization.periods import FiscalPeriod

DEFAULT_RELATIVE = Decimal("0.005")
CRITICAL_RELATIVE = Decimal("0.001")
BASE_RELATIVE = Decimal("0.0005")
# Items that feed the DCF directly (§10.3): revenue, EBIT, D&A, capex, NWC items, cash,
# debt, lease liabilities, share counts.
DCF_CRITICAL = frozenset({
    "IS.REVENUE", "IS.EBIT", "IS.DA", "CF.DA", "CF.CAPEX", "CF.CHANGE_WORKING_CAPITAL",
    "BS.RECEIVABLES", "BS.INVENTORY", "BS.PAYABLES", "BS.ACCRUED_LIABILITIES", "BS.CASH",
    "BS.ST_DEBT", "BS.SHORT_TERM_BORROWINGS", "BS.CURRENT_LTD", "BS.LT_DEBT",
    "BS.OPERATING_LEASE_LIAB_CURRENT", "BS.OPERATING_LEASE_LIAB_NONCURRENT",
    "BS.FINANCE_LEASE_LIAB_CURRENT", "BS.FINANCE_LEASE_LIAB_NONCURRENT",
    "BS.LEASE_LIAB_CURRENT", "BS.LEASE_LIAB_NONCURRENT", "IS.SHARES_BASIC_WA",
    "IS.SHARES_DILUTED_WA", "BS.SHARES_OUTSTANDING",
})  # fmt: skip
ACTIONS = ("select_candidate", "enter_value", "override", "revoke")
CANDIDATE_SOURCES = ("reported_fact", "import_value")


@dataclass(frozen=True)
class Candidate:
    fact_id: int
    accession: str
    form: str
    filed: date
    concept: str
    value: Decimal  # in the line item's sign convention
    role: str  # first_reported | latest_reported | same_filing | sec_reported | imported
    # Id space of ``fact_id`` (D57): source.reported_facts or source.statement_import_values.
    # Serialized only for imports, so SEC conflicts keep their exact bytes.
    source: str = "reported_fact"

    def as_dict(self) -> dict[str, Any]:
        extra = {} if self.source == "reported_fact" else {"source": self.source}
        return extra | {
            "fact_id": self.fact_id,
            "accession": self.accession,
            "form": self.form,
            "filed": self.filed.isoformat(),
            "concept": self.concept,
            "value": decimal_str(self.value),
            "role": self.role,
        }


@dataclass(frozen=True)
class Resolution:
    id: int
    conflict_key: str
    action: str
    candidate_fact_id: int | None = None
    value: Decimal | None = None
    source_reference: str | None = None
    candidate_source: str | None = None  # D57: set when selecting an imported candidate

    def as_dict(self) -> dict[str, Any]:
        extra = {} if self.candidate_source is None else {"candidate_source": self.candidate_source}
        return extra | {
            "id": self.id,
            "action": self.action,
            "candidate_fact_id": self.candidate_fact_id,
            "value": None if self.value is None else decimal_str(self.value),
        }


@dataclass(frozen=True)
class Materiality:
    is_material: bool
    absolute_difference: Decimal
    relative_difference: Decimal
    threshold: Decimal
    basis: str


@dataclass(frozen=True)
class Conflict:
    key: str
    kind: str  # within_filing | mixed_vintage | cross_source | cross_import (D57)
    line_item: str
    period: FiscalPeriod
    candidates: tuple[Candidate, ...]
    policy_fact_id: int | None  # what the selection policy chose (None: no value)
    materiality: Materiality
    status: str  # open | resolved | overridden | auto_resolved_immaterial
    resolution: Resolution | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def sort_key(self) -> tuple[Any, ...]:
        return (*self.period.sort_key(), self.line_item, self.kind)

    def as_dict(self) -> dict[str, Any]:
        m = self.materiality
        return {
            "key": self.key,
            "kind": self.kind,
            "line_item": self.line_item,
            "period": [self.period.period_type, self.period.start.isoformat(),
                       self.period.end.isoformat()],
            "candidates": [c.as_dict() for c in self.candidates],
            "policy_fact_id": self.policy_fact_id,
            "is_material": m.is_material,
            "absolute_difference": decimal_str(m.absolute_difference),
            "relative_difference": decimal_str(m.relative_difference),
            "threshold": decimal_str(m.threshold),
            "materiality_basis": m.basis,
            "status": self.status,
            "resolution": None if self.resolution is None else self.resolution.as_dict(),
            "detail": dict(self.detail),
        }  # fmt: skip


def materiality(code: str, amounts: Iterable[Decimal], base: Decimal | None) -> Materiality:
    amounts = list(amounts)
    larger = max(abs(a) for a in amounts)
    diff = max(amounts) - min(amounts)
    relative = (diff / larger).quantize(Decimal("1e-8")) if larger else Decimal(0)
    if any(a > 0 for a in amounts) and any(a < 0 for a in amounts):
        return Materiality(True, diff, relative, Decimal(0), "sign disagreement")
    rate = CRITICAL_RELATIVE if code in DCF_CRITICAL else DEFAULT_RELATIVE
    floor = BASE_RELATIVE * abs(base) if base is not None else Decimal(0)
    threshold = max(rate * larger, floor)
    basis = (
        f"max({decimal_str(rate * 100)}% of larger value"
        + (", 0.05% of period base" if base is not None else "")
        + ")"
        + (" (DCF-critical item)" if code in DCF_CRITICAL else "")
    )
    return Materiality(diff > threshold, diff, relative, threshold, basis)


def conflict_key(kind: str, code: str, period: FiscalPeriod, cands: Iterable[Candidate]) -> str:
    doc = [kind, code, period.period_type, period.start.isoformat(), period.end.isoformat(),
           sorted([c.fact_id, decimal_str(c.value)] for c in cands)]  # fmt: skip
    return hashlib.sha256(json.dumps(doc, separators=(",", ":")).encode()).hexdigest()[:32]


def related_items(mapping: MappingSet) -> dict[str, frozenset[str]]:
    """Items tied by an accounting identity checked in validation (D51)."""
    groups: list[set[str]] = [
        {"BS.TOTAL_ASSETS", "BS.TOTAL_LIABILITIES", "BS.TOTAL_EQUITY"},
        {"IS.NET_INCOME", "CF.NET_INCOME"},
    ]
    for item in mapping.line_items.values():
        if item.subtotal and not item.derived_only:
            groups.append({item.code, *(c for _, c in item.subtotal)})
    related: dict[str, set[str]] = {}
    for group in groups:
        for code in group:
            related.setdefault(code, set()).update(group - {code})
    return {k: frozenset(v) for k, v in related.items()}


def _candidate(fact: RawFact, fact_id: int, sign: int, role: str) -> Candidate:
    return Candidate(fact_id, fact.accession_no, fact.form, fact.filed_date, fact.concept,
                     fact.value * sign, role)  # fmt: skip


def _base(
    index: Mapping[tuple[FiscalPeriod, str], DatasetValue], period: FiscalPeriod
) -> Decimal | None:
    for code in ("BS.TOTAL_ASSETS", "IS.REVENUE"):
        v = index.get((period, code))
        if v is not None and v.status == "ok" and v.value is not None:
            return v.value
    return None


def detect(
    values: Iterable[DatasetValue],
    *,
    taxonomy: str,
    selections: Mapping[FactKey, Selection],
    facts_by_key: Mapping[FactKey, list[RawFact]],
    fact_ids: Mapping[RawFact, int],
    mapping: MappingSet,
    policy: Policy,
) -> list[Conflict]:
    values = [v for v in values if v.provenance == "reported"]
    index = {(v.period, v.line_item): v for v in values}
    by_id = {i: f for f, i in fact_ids.items()}
    related = related_items(mapping)
    out: list[Conflict] = []
    for v in values:
        # Instants are stored on the period ending that day; their fact key has no start.
        start = None if mapping.line_items[v.line_item].period == "instant" else v.period.start
        key: FactKey = (taxonomy, v.concept, v.unit, start, v.period.end)
        base = _base(index, v.period)
        if v.status == "ambiguous":
            accessions = set(v.detail.get("ambiguous_accessions", ()))
            facts = sorted(
                (f for f in facts_by_key.get(key, []) if f.accession_no in accessions),
                key=lambda f: (order_key(f), f.value),
            )
            cands = tuple(_candidate(f, fact_ids[f], v.sign, "same_filing") for f in facts)
            if len(cands) < 2:
                continue
            m = materiality(v.line_item, (c.value for c in cands), base)
            m = replace(m, is_material=True, basis="different values within one filing (§4.4)")
            key_hex = conflict_key("within_filing", v.line_item, v.period, cands)
            out.append(
                Conflict(key_hex, "within_filing", v.line_item, v.period, cands, None, m, "open")
            )
            continue
        sel = selections.get(key)
        if sel is None or not sel.restated or not v.source_fact_ids:
            continue
        chosen = by_id[v.source_fact_ids[0]]
        older = sorted(
            (code, s.detail.get("accession"))
            for code in related.get(v.line_item, ())
            if (s := index.get((v.period, code))) is not None
            and s.status == "ok"
            and s.source_fact_ids
            and order_key(by_id[s.source_fact_ids[0]]) < order_key(chosen)
        )
        if not older:
            continue  # restatement with a consistent vintage: not a conflict
        assert sel.first is not None and sel.latest is not None
        cands = (
            _candidate(sel.first, fact_ids[sel.first], v.sign, "first_reported"),
            _candidate(sel.latest, fact_ids[sel.latest], v.sign, "latest_reported"),
        )
        m = materiality(v.line_item, (c.value for c in cands), base)
        out.append(
            Conflict(
                conflict_key("mixed_vintage", v.line_item, v.period, cands),
                "mixed_vintage", v.line_item, v.period, cands, v.source_fact_ids[0], m,
                "open" if m.is_material else "auto_resolved_immaterial",
                detail={"older_related_items": [[c, a] for c, a in older],
                        "policy": policy.value},
            )
        )  # fmt: skip
    return sorted(out, key=Conflict.sort_key)


def apply(
    values: Iterable[DatasetValue],
    conflicts: Iterable[Conflict],
    resolutions: Mapping[str, Resolution],
) -> tuple[list[DatasetValue], list[Conflict]]:
    """Apply the latest resolution per conflict key. Returns new values and conflicts."""
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
            cand = next((x for x in c.candidates if x.fact_id == res.candidate_fact_id), None)
            if cand is None:
                out.append(replace(c, detail={**c.detail, "unapplied_resolution": res.id}))
                continue
            index[(c.period, c.line_item)] = replace(
                v, status="ok", value=cand.value, source_fact_ids=(cand.fact_id,),
                detail={**{k: x for k, x in v.detail.items() if k != "ambiguous_accessions"},
                        "accession": cand.accession, "form": cand.form,
                        "filed": cand.filed.isoformat(), "resolution": marker},
            )  # fmt: skip
            out.append(replace(c, status="resolved", resolution=res))
        elif res.action == "enter_value":
            assert res.value is not None
            index[(c.period, c.line_item)] = replace(
                v, status="ok", value=res.value, provenance="analyst", source_fact_ids=(),
                detail={"resolution": {**marker, "source_reference": res.source_reference},
                        "candidates": [x.accession for x in c.candidates]},
            )  # fmt: skip
            out.append(replace(c, status="resolved", resolution=res))
        elif res.action == "override" and v.status == "ok":
            index[(c.period, c.line_item)] = replace(v, detail={**v.detail, "resolution": marker})
            out.append(replace(c, status="overridden", resolution=res))
        else:  # override of a value-less (within-filing) conflict cannot apply
            out.append(replace(c, detail={**c.detail, "unapplied_resolution": res.id}))
    return list(index.values()), out


def summarize(conflicts: Iterable[Conflict]) -> dict[str, Any]:
    by: dict[str, dict[str, int]] = {}
    open_material = 0
    for c in conflicts:
        row = by.setdefault(c.kind, {})
        row[c.status] = row.get(c.status, 0) + 1
        open_material += c.status == "open" and c.materiality.is_material
    return {"by_kind": {k: dict(sorted(v.items())) for k, v in sorted(by.items())},
            "open_material": open_material}  # fmt: skip
