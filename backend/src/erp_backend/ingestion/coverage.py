"""Interim (quarterly) coverage detection (Blueprint §0.5, §4.4; D12).

Quarterly XBRL is conditional — 40-F filers furnish quarterly results on 6-K, often without
XBRL. For the most recent 8 interim period-ends found in interim forms (10-Q, 6-K and
amendments), a period is *complete* when all six items are present from an interim form:
quarterly revenue, operating income and net income (84–98-day durations), cash and total
assets (instants), and year-to-date operating cash flow. Only period-ends within three years
of the company's latest reported period count, so stale history cannot mask a current gap.
Coverage is ``full`` when all 8 are complete, ``none`` when none are, otherwise ``partial``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from erp_backend.ingestion.sec_parse import RawFact

INTERIM_FORMS = frozenset({"10-Q", "10-Q/A", "6-K", "6-K/A"})
QUARTER_DAYS = range(84, 99)
YTD_DAYS = range(84, 281)
WINDOW = 8
# Eight interim period-ends (Q4 is never interim) span ~2.75 years; older periods say
# nothing about current interim availability.
RECENCY = timedelta(days=3 * 366)

ITEMS: dict[str, tuple[str, frozenset[str]]] = {
    "revenue": ("quarter", frozenset({
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet", "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"})),
    "operating_income": ("quarter", frozenset({
        "us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"})),
    "net_income": ("quarter", frozenset({
        "us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss", "ifrs-full:ProfitLoss",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent"})),
    "cash": ("instant", frozenset({
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "ifrs-full:CashAndCashEquivalents"})),
    "total_assets": ("instant", frozenset({"us-gaap:Assets", "ifrs-full:Assets"})),
    "operating_cash_flow_ytd": ("ytd", frozenset({
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities"})),
}  # fmt: skip


@dataclass(frozen=True)
class InterimCoverage:
    level: str
    periods: tuple[dict[str, Any], ...]

    def details(self) -> dict[str, Any]:
        return {"window": WINDOW, "periods": list(self.periods)}


def _matches(fact: RawFact, shape: str) -> bool:
    days = fact.duration_days
    if shape == "instant":
        return fact.period_start is None
    if days is None:
        return False
    return days in (QUARTER_DAYS if shape == "quarter" else YTD_DAYS)


def detect(facts: list[RawFact]) -> InterimCoverage:
    interim = [f for f in facts if f.form.upper() in INTERIM_FORMS]
    present: dict[date, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for f in interim:
        name = f"{f.taxonomy}:{f.concept}"
        for item, (shape, concepts) in ITEMS.items():
            if name in concepts and _matches(f, shape):
                present[f.period_end][item].add(f.form.upper())
    # A period-end counts only if an interim form reports a quarterly flow ending there.
    # Balance-sheet comparatives (e.g. the prior fiscal year-end shown in a 10-Q) do not.
    reported = [f.period_end for f in facts if f.taxonomy != "dei"]
    cutoff = max(reported) - RECENCY if reported else date.min
    ends = sorted(
        e
        for e, items in present.items()
        if (items.get("revenue") or items.get("net_income")) and e > cutoff
    )
    periods = []
    for end in ends[-WINDOW:]:
        items = present[end]
        missing = sorted(i for i in ITEMS if not items.get(i))
        forms = sorted({form for fs in items.values() for form in fs})
        periods.append(
            {
                "period_end": end.isoformat(),
                "complete": not missing,
                "missing": missing,
                "forms": forms,
                "from_6k": any(f.startswith("6-K") for f in forms),
            }
        )
    complete = sum(1 for p in periods if p["complete"])
    if complete == 0:
        level = "none"
    elif complete == WINDOW:
        level = "full"
    else:
        level = "partial"
    return InterimCoverage(level, tuple(periods))
