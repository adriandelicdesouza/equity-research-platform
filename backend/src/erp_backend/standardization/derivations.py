"""Derived standardized values (Blueprint §4.4 step 5; D50, D51). Pure and deterministic.

Three passes, in this order, each over the values available so far:

1. **Quarters from year-to-date differences** (flow items only): ``Q2 = H1 − Q1``,
   ``Q3 = 9M − H1``, ``Q4 = FY − 9M``. This yields quarterly cash flows, which 10-Qs report
   only year-to-date. Both inputs must be OK values of the same concept and sign, both
   windows must start on the fiscal-year start, and a reported quarter always wins.
2. **Formulas** from the mapping's declarative ``subtotal`` definitions, for a period where
   the parent is not reported: e.g. gross profit = revenue − COGS, EBITDA = EBIT + D&A,
   FCF = CFO + capex. Every child must be an OK value of the same period and unit.
3. **LTM** (last twelve months) at each interim year-to-date end:
   ``LTM = FY(previous) + YTD(current) − YTD(same period previous year)`` for flow items.
   The three inputs must share concept and sign, the previous FY must end the day before
   the current YTD starts, and the two YTD windows must have (nearly) equal length.

A derivation that cannot be made is skipped and counted by reason; values are never
invented and missing inputs are never treated as zero. Derived values are marked
``derived`` with the rule, input values and the union of source fact ids.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from erp_backend.standardization.dataset_types import DatasetValue, decimal_str
from erp_backend.standardization.mapping import MappingSet
from erp_backend.standardization.periods import FiscalPeriod

QUARTER_RULES = (
    ("Q2", "H1", "Q1", "Q2=H1-Q1"),
    ("Q3", "9M", "H1", "Q3=9M-H1"),
    ("Q4", "FY", "9M", "Q4=FY-9M"),
)
YTD_TYPES = ("Q1", "H1", "9M")  # year-to-date windows starting on the fiscal-year start
LTM_RULE = "LTM=FY(prev)+YTD-YTD(prev)"
LTM_LENGTH_TOLERANCE = 7  # days; 52/53-week years shift YTD windows by up to a week


def _flow_items(mapping: MappingSet) -> list[str]:
    return [
        code
        for code, item in mapping.line_items.items()
        if item.value_type == "flow" and item.period == "duration"
    ]


def _ids(*values: DatasetValue) -> tuple[int, ...]:
    return tuple(sorted({i for v in values for i in v.source_fact_ids}))


def _input(label: str, v: DatasetValue) -> list[object]:
    """[label, value, provenance, accession of a reported input (None when derived)]."""
    assert v.value is not None
    return [label, decimal_str(v.value), v.provenance, v.detail.get("accession")]


def _same_basis(*values: DatasetValue) -> bool:
    return len({(v.concept, v.sign, v.unit) for v in values}) == 1


class _Index:
    def __init__(self, values: Iterable[DatasetValue]) -> None:
        self.by_key: dict[tuple[int, str, str], DatasetValue] = {}
        self.periods: dict[tuple[int, str], FiscalPeriod] = {}
        for v in values:
            self.add(v)

    def add(self, v: DatasetValue) -> None:
        if v.period.period_type != "LTM":
            self.by_key[(v.period.fiscal_year, v.period.period_type, v.line_item)] = v
            self.periods.setdefault((v.period.fiscal_year, v.period.period_type), v.period)

    def get(self, fy: int, ptype: str, code: str) -> DatasetValue | None:
        return self.by_key.get((fy, ptype, code))


def _quarters(
    index: _Index, mapping: MappingSet, fy_starts: dict[int, date], skipped: Counter[str]
) -> list[DatasetValue]:
    out = []
    flows = _flow_items(mapping)
    for fy in sorted(fy_starts):
        for target, minuend_type, subtrahend_type, rule in QUARTER_RULES:
            for code in flows:
                big = index.get(fy, minuend_type, code)
                small = index.get(fy, subtrahend_type, code)
                if big is None or small is None or index.get(fy, target, code) is not None:
                    continue
                if big.status != "ok" or small.status != "ok":
                    skipped[f"{rule}:ambiguous_input"] += 1
                    continue
                if not _same_basis(big, small):
                    skipped[f"{rule}:concept_mismatch"] += 1
                    continue
                start = small.period.end + timedelta(days=1)
                window_ok = (
                    big.period.start == small.period.start == fy_starts[fy]
                    and small.period.end < big.period.end
                )
                existing = index.periods.get((fy, target))
                if not window_ok or (
                    existing is not None
                    and (existing.start, existing.end) != (start, big.period.end)
                ):
                    skipped[f"{rule}:window_mismatch"] += 1
                    continue
                period = existing or FiscalPeriod(fy, target, start, big.period.end)
                assert big.value is not None and small.value is not None
                value = replace(
                    big,
                    period=period,
                    value=big.value - small.value,
                    provenance="derived",
                    source_fact_ids=_ids(big, small),
                    detail={
                        "rule": rule,
                        "inputs": [
                            _input(minuend_type, big),
                            _input(subtrahend_type, small),
                        ],
                    },
                )
                index.add(value)
                out.append(value)
    return out


def _formulas(index: _Index, mapping: MappingSet, skipped: Counter[str]) -> list[DatasetValue]:
    out = []
    formulas = [i for i in mapping.line_items.values() if i.subtotal]
    for fy, ptype in sorted(index.periods, key=lambda k: index.periods[k].sort_key()):
        period = index.periods[(fy, ptype)]
        for item in formulas:  # mapping order: a parent derived here can feed a later one
            if index.get(fy, ptype, item.code) is not None:
                continue
            children = [(s, index.get(fy, ptype, c)) for s, c in item.subtotal]
            present = [v for _, v in children if v is not None]
            if not present:
                continue
            rule = (
                item.code + "=" + "".join(f"{'+' if s > 0 else '-'}{c}" for s, c in item.subtotal)
            )
            if len(present) < len(children):
                skipped[f"{item.code}:missing_input"] += 1
                continue
            if any(v.status != "ok" for v in present):
                skipped[f"{item.code}:ambiguous_input"] += 1
                continue
            if len({v.unit for v in present}) != 1:
                skipped[f"{item.code}:unit_mismatch"] += 1
                continue
            total = Decimal(0)
            for s, v in children:
                assert v is not None and v.value is not None
                total += s * v.value
            value = DatasetValue(
                period=period,
                line_item=item.code,
                status="ok",
                value=total,
                unit=present[0].unit,
                provenance="derived",
                concept=f"formula:{item.code}",
                priority=0,
                sign=1,
                source_fact_ids=_ids(*present),
                detail={
                    "rule": rule,
                    "inputs": [
                        _input(c, v) for (_, c), v in zip(item.subtotal, present, strict=True)
                    ],
                },
            )
            index.add(value)
            out.append(value)
    return out


def _ltm(index: _Index, mapping: MappingSet, skipped: Counter[str]) -> list[DatasetValue]:
    out = []
    flows = _flow_items(mapping)
    for fy, ptype in sorted(index.periods, key=lambda k: index.periods[k].sort_key()):
        if ptype not in YTD_TYPES:
            continue
        for code in flows:
            cur = index.get(fy, ptype, code)
            prev_fy = index.get(fy - 1, "FY", code)
            prev = index.get(fy - 1, ptype, code)
            if cur is None or prev_fy is None or prev is None:
                continue
            if any(v.status != "ok" for v in (cur, prev_fy, prev)):
                skipped["LTM:ambiguous_input"] += 1
                continue
            if not _same_basis(cur, prev_fy, prev):
                skipped["LTM:concept_mismatch"] += 1
                continue
            contiguous = prev_fy.period.end + timedelta(days=1) == cur.period.start
            similar = abs(cur.period.days - prev.period.days) <= LTM_LENGTH_TOLERANCE
            if not (contiguous and similar and prev.period.start == prev_fy.period.start):
                skipped["LTM:window_mismatch"] += 1
                continue
            assert cur.value is not None and prev_fy.value is not None and prev.value is not None
            ltm = FiscalPeriod(fy, "LTM", prev.period.end + timedelta(days=1), cur.period.end)
            out.append(
                replace(
                    cur,
                    period=ltm,
                    value=prev_fy.value + cur.value - prev.value,
                    provenance="derived",
                    source_fact_ids=_ids(cur, prev_fy, prev),
                    detail={
                        "rule": LTM_RULE,
                        "inputs": [
                            _input(f"FY {fy - 1}", prev_fy),
                            _input(f"{ptype} {fy}", cur),
                            _input(f"{ptype} {fy - 1}", prev),
                        ],
                    },
                )
            )
    return out


def rule_name(rule: str) -> str:
    """Short label for counting: 'Q4=FY-9M' → 'Q4', 'IS.EBITDA=+…' → 'IS.EBITDA'."""
    return "LTM" if rule == LTM_RULE else rule.split("=", 1)[0]


def derive(
    values: Iterable[DatasetValue], fiscal_years: Iterable[FiscalPeriod], mapping: MappingSet
) -> tuple[list[DatasetValue], Counter[str], Counter[str]]:
    """Derived values, counts by rule, and skipped derivations by reason."""
    index = _Index(values)
    fy_starts = {p.fiscal_year: p.start for p in fiscal_years}
    # The open year (after the last full year) starts the day after the last year-end.
    if fiscal_years := sorted(fiscal_years, key=lambda p: p.end):
        last = fiscal_years[-1]
        fy_starts.setdefault(last.fiscal_year + 1, last.end + timedelta(days=1))
    skipped: Counter[str] = Counter()
    derived = _quarters(index, mapping, fy_starts, skipped)
    derived += _formulas(index, mapping, skipped)
    derived += _ltm(index, mapping, skipped)
    by_rule: Counter[str] = Counter(rule_name(str(v.detail["rule"])) for v in derived)
    return derived, by_rule, skipped
