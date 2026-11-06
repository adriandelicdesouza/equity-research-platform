"""Per-period concept resolution onto standard line items (Blueprint §4.4 steps 3, 4, 6, 8).

Input is the deterministic fact selection (:mod:`erp_backend.ingestion.selection`, D11).
For each line item and each period window — (start, end) for durations, end date for
instants — the highest-priority mapped concept that has a selection in that window wins.
Fallback is evaluated per window and never mixes concepts within one value. If the winning
concept's selection is AMBIGUOUS, the value is withheld (status ``ambiguous``) rather than
silently falling back to a lower-priority concept. The reported value is multiplied by the
candidate's sign to reach the line item's sign convention.

Units: monetary items use the reporting currency (``USD``), per-share items
``<currency>/shares`` and count items ``shares``; facts in other units are ignored, so a
company that tags a concept in two currencies cannot mix them.

This module is pure. Fiscal-period labelling and dataset persistence come later.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import FactKey, Policy, Selection, Status
from erp_backend.standardization.mapping import Candidate, LineItem, MappingSet

ANNUAL_DAYS = range(350, 381)  # 52/53-week fiscal years (Blueprint §4.4 step 1)

Window = tuple[date | None, date]


class ValueStatus(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class StandardValue:
    line_item: str
    period_start: date | None
    period_end: date
    status: ValueStatus
    value: Decimal | None  # sign-normalized; None when ambiguous
    unit: str
    taxonomy: str
    concept: str
    priority: int
    sign: int
    source: RawFact | None  # the chosen reported fact (provenance)
    restated: bool
    # Lower-priority concepts that also had a selection in this window (not used).
    alternatives: tuple[str, ...] = ()
    # Filings that reported conflicting values for the winning concept (ambiguous only).
    ambiguous_accessions: tuple[str, ...] = ()

    @property
    def duration_days(self) -> int | None:
        if self.period_start is None:
            return None
        return (self.period_end - self.period_start).days + 1


def expected_unit(item: LineItem, currency: str) -> str:
    if item.value_type == "per_share":
        return f"{currency}/shares"
    if item.value_type == "count":
        return "shares"
    return currency


def _by_concept(
    selections: Mapping[FactKey, Selection], taxonomy: str
) -> dict[tuple[str, str], dict[Window, Selection]]:
    index: dict[tuple[str, str], dict[Window, Selection]] = defaultdict(dict)
    for (tax, concept, unit, start, end), sel in selections.items():
        if tax == taxonomy:
            index[(concept, unit)][(start, end)] = sel
    return index


def _resolve_item(
    item: LineItem,
    candidates: tuple[Candidate, ...],
    index: Mapping[tuple[str, str], Mapping[Window, Selection]],
    unit: str,
    policy: Policy,
) -> list[StandardValue]:
    instant = item.period == "instant"
    per_window: dict[Window, list[tuple[Candidate, Selection]]] = defaultdict(list)
    for cand in candidates:
        for window, sel in index.get((cand.concept, unit), {}).items():
            if (window[0] is None) == instant:
                per_window[window].append((cand, sel))
    out = []
    for (start, end), found in per_window.items():
        found.sort(key=lambda cs: cs[0].priority)
        cand, sel = found[0]
        chosen = sel.chosen(policy)
        ok = sel.status is Status.OK and chosen is not None
        out.append(
            StandardValue(
                line_item=item.code,
                period_start=start,
                period_end=end,
                status=ValueStatus.OK if ok else ValueStatus.AMBIGUOUS,
                value=chosen.value * cand.sign if ok and chosen is not None else None,
                unit=unit,
                taxonomy=cand.taxonomy,
                concept=cand.concept,
                priority=cand.priority,
                sign=cand.sign,
                source=chosen if ok else None,
                restated=sel.restated,
                alternatives=tuple(c.concept for c, _ in found[1:]),
                ambiguous_accessions=sel.ambiguous_accessions,
            )
        )
    return out


def resolve(
    selections: Mapping[FactKey, Selection],
    mapping: MappingSet,
    *,
    taxonomy: str,
    currency: str,
    policy: Policy = Policy.LATEST_REPORTED,
) -> list[StandardValue]:
    """Resolve every mapped line item for every period window present in the selections."""
    index = _by_concept(selections, taxonomy)
    values: list[StandardValue] = []
    for code, item in mapping.line_items.items():
        candidates = mapping.for_line_item(taxonomy, code)
        if candidates:
            unit = expected_unit(item, currency)
            values.extend(_resolve_item(item, candidates, index, unit, policy))
    order = {code: item.sort_order for code, item in mapping.line_items.items()}
    values.sort(key=lambda v: (order[v.line_item], v.period_end, v.period_start or date.min))
    return values


def taxonomy_for_standard(accounting_standard: str | None) -> str | None:
    return {"US_GAAP": "us-gaap", "IFRS": "ifrs-full"}.get(accounting_standard or "")


@dataclass(frozen=True)
class AnnualCoverage:
    """Which line items have a value for each fiscal-year window (Blueprint §4.4 step 8)."""

    year_ends: tuple[date, ...]
    present: Mapping[str, frozenset[date]]  # code → year-ends with an OK value
    ambiguous: Mapping[str, frozenset[date]]
    mapped: frozenset[str]  # codes with at least one candidate for the taxonomy

    def missing(self, year_end: date) -> list[str]:
        return sorted(c for c in self.mapped if year_end not in self.present.get(c, frozenset()))


def annual_coverage(
    values: Iterable[StandardValue], mapping: MappingSet, taxonomy: str, *, years: int
) -> AnnualCoverage:
    """Coverage of the latest ``years`` fiscal years.

    A fiscal year is a 350–380-day window of a mapped duration value; its end date is the
    year-end. Duration items count for a year when that exact annual window has a value;
    instant items when there is a value at the year-end date.
    """
    values = list(values)
    ends = sorted(
        {
            v.period_end
            for v in values
            if v.duration_days in ANNUAL_DAYS and v.line_item in ("IS.REVENUE", "IS.NET_INCOME")
        }
    )[-years:]
    present: dict[str, set[date]] = defaultdict(set)
    ambiguous: dict[str, set[date]] = defaultdict(set)
    for v in values:
        item = mapping.line_items[v.line_item]
        if v.period_end not in ends:
            continue
        if item.period == "duration" and v.duration_days not in ANNUAL_DAYS:
            continue
        (present if v.status is ValueStatus.OK else ambiguous)[v.line_item].add(v.period_end)
    mapped = frozenset(code for (tax, code) in mapping.candidates if tax == taxonomy)
    return AnnualCoverage(
        tuple(ends),
        {k: frozenset(v) for k, v in present.items()},
        {k: frozenset(v) for k, v in ambiguous.items()},
        mapped,
    )
