"""Accounting integrity checks (§5.3) and derived values (Q2/Q3/Q4, formulas, LTM). Pure."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.standardization import mapping as m
from erp_backend.standardization.dataset_types import DatasetValue
from erp_backend.standardization.derivations import derive
from erp_backend.standardization.periods import FiscalPeriod
from erp_backend.standardization.validation import CheckResult, sha256, summarize, validate

ROOT = Path(__file__).resolve().parents[2] / "mappings"
FY24 = FiscalPeriod(2024, "FY", date(2024, 1, 1), date(2024, 12, 31))
FY23 = FiscalPeriod(2023, "FY", date(2023, 1, 1), date(2023, 12, 31))
Q1 = FiscalPeriod(2024, "Q1", date(2024, 1, 1), date(2024, 3, 31))
H1 = FiscalPeriod(2024, "H1", date(2024, 1, 1), date(2024, 6, 30))
NINE = FiscalPeriod(2024, "9M", date(2024, 1, 1), date(2024, 9, 30))
UNIT = {"per_share": "USD/shares", "count": "shares"}


@pytest.fixture(scope="module")
def v2() -> m.MappingSet:
    return m.load(ROOT, "v2")


_ids = iter(range(1, 10**6))


def val(
    period: FiscalPeriod,
    code: str,
    value: str | None,
    *,
    concept: str | None = None,
    unit: str = "USD",
    provenance: str = "reported",
) -> DatasetValue:
    return DatasetValue(
        period=period,
        line_item=code,
        status="ok" if value is not None else "ambiguous",
        value=Decimal(value) if value is not None else None,
        unit=unit,
        provenance=provenance,
        concept=concept or code.split(".")[1],
        priority=1,
        sign=1,
        source_fact_ids=(next(_ids),),
        detail={"accession": "0000000001-25-000001"} if provenance == "reported" else {},
    )


def results(values: list[DatasetValue], mapping: m.MappingSet, check: str) -> list[CheckResult]:
    return [r for r in validate(values, mapping, "USD") if r.check_code == check]


# ---- Balance and tolerance -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("liabilities", "status"),
    [("600000000", "passed"), ("600999000", "passed"), ("601001000", "failed")],
)
def test_balance_uses_point_one_percent_of_assets(
    v2: m.MappingSet, liabilities: str, status: str
) -> None:
    values = [
        val(FY24, "BS.TOTAL_ASSETS", "1000000000"),
        val(FY24, "BS.TOTAL_LIABILITIES", liabilities),
        val(FY24, "BS.TOTAL_EQUITY", "400000000"),
    ]
    (r,) = results(values, v2, "BALANCE")
    assert r.status == status and r.tolerance == Decimal("1000000")
    assert r.expected == Decimal("1000000000")
    assert r.difference == Decimal(liabilities) + Decimal("400000000") - Decimal("1000000000")
    assert set(r.inputs["values"]) == {"BS.TOTAL_ASSETS", "BS.TOTAL_LIABILITIES", "BS.TOTAL_EQUITY"}
    assert r.inputs["values"]["BS.TOTAL_ASSETS"]["provenance"] == "reported"


def test_reporting_precision_dominates_for_small_rounded_values(v2: m.MappingSet) -> None:
    # Values reported in millions: one unit of precision (1,000,000) > 0.1% of 50,000,000.
    values = [
        val(FY24, "BS.TOTAL_ASSETS", "50000000"),
        val(FY24, "BS.TOTAL_LIABILITIES", "31000000"),
        val(FY24, "BS.TOTAL_EQUITY", "20000000"),
    ]
    (r,) = results(values, v2, "BALANCE")
    assert r.tolerance == Decimal("1000000") and r.status == "passed"
    assert "reporting precision 1000000" in r.message


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"BS.TOTAL_EQUITY": None}, "missing input: BS.TOTAL_EQUITY"),
        ({"BS.TOTAL_EQUITY": "ambiguous"}, "ambiguous input: BS.TOTAL_EQUITY"),
        ({"BS.TOTAL_EQUITY": "EUR"}, "unexpected unit: BS.TOTAL_EQUITY"),
    ],
)
def test_missing_ambiguous_or_foreign_inputs_are_not_evaluable(
    v2: m.MappingSet, change: dict[str, str | None], reason: str
) -> None:
    values = [val(FY24, "BS.TOTAL_ASSETS", "100"), val(FY24, "BS.TOTAL_LIABILITIES", "60")]
    mode = change["BS.TOTAL_EQUITY"]
    if mode == "ambiguous":
        values.append(val(FY24, "BS.TOTAL_EQUITY", None))
    elif mode == "EUR":
        values.append(val(FY24, "BS.TOTAL_EQUITY", "40", unit="EUR"))
    (r,) = results(values, v2, "BALANCE")
    assert (r.status, r.message) == ("not_evaluable", reason)
    assert r.difference is None and r.tolerance is None


# ---- Subtotals and cash ----------------------------------------------------------------------


def test_reported_subtotals_are_checked_and_derived_ones_are_not(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "IS.REVENUE", "1000"),
        val(FY24, "IS.COGS", "600"),
        val(FY24, "IS.GROSS_PROFIT", "450"),  # wrong by 50
        val(FY23, "IS.REVENUE", "900"),
        val(FY23, "IS.COGS", "500"),
        val(FY23, "IS.GROSS_PROFIT", "400", provenance="derived"),
    ]
    rows = [r for r in results(values, v2, "SUBTOTAL") if r.subject == "IS.GROSS_PROFIT"]
    assert [(r.period.fiscal_year, r.status) for r in rows] == [(2024, "failed")]  # type: ignore[union-attr]
    assert rows[0].inputs["expected"] == ["+IS.GROSS_PROFIT"]
    assert rows[0].inputs["actual"] == ["+IS.REVENUE", "-IS.COGS"]


def test_subtotal_with_missing_child_is_not_evaluable(v2: m.MappingSet) -> None:
    values = [val(FY24, "IS.NET_INCOME", "100"), val(FY24, "IS.NET_INCOME_COMMON", "100")]
    rows = [r for r in results(values, v2, "SUBTOTAL") if r.subject == "IS.NET_INCOME"]
    assert [(r.status, r.message) for r in rows] == [
        ("not_evaluable", "missing input: IS.NCI_SHARE")
    ]


def test_cash_rollforward_and_reconciliation(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "CF.CFO", "500"),
        val(FY24, "CF.CFI", "-200"),
        val(FY24, "CF.CFF", "-250"),
        val(FY24, "CF.FX_EFFECT", "-10"),
        val(FY24, "CF.NET_CHANGE_CASH", "40"),
        val(FY24, "BS.CASH", "140"),
        val(FY23, "BS.CASH", "70"),  # BS change 70 vs CF 40: restricted cash difference
    ]
    (roll,) = results(values, v2, "CASH_ROLLFORWARD")
    assert roll.status == "passed" and roll.severity == "error"
    (rec,) = [r for r in results(values, v2, "CASH_RECONCILIATION") if r.period == FY24]
    assert (rec.status, rec.severity, rec.difference) == ("failed", "warning", Decimal("30"))
    assert rec.inputs["values"]["BS.CASH@prior"]["period"][2] == "2023-12-31"
    # First year: no prior balance → not evaluable, never passed.
    assert (
        results([v for v in values if v.period == FY24], v2, "CASH_RECONCILIATION")[0].status
        == "not_evaluable"
    )


def test_cross_statement_checks(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "IS.NET_INCOME", "100"),
        val(FY24, "CF.NET_INCOME", "100"),
        val(FY24, "IS.DA", "30"),
        val(FY23, "CF.DA", "25"),  # only one D&A side reported in FY23 → no D&A row
        val(FY23, "IS.NET_INCOME", "90"),
    ]
    ni = {r.period.fiscal_year: r.status for r in results(values, v2, "CROSS_NET_INCOME")}  # type: ignore[union-attr]
    assert ni == {2024: "passed", 2023: "not_evaluable"}
    assert results(values, v2, "CROSS_DA") == []


# ---- Shares and EPS --------------------------------------------------------------------------


def test_shares_and_eps(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "IS.SHARES_BASIC_WA", "1000000", unit="shares"),
        val(FY24, "IS.SHARES_DILUTED_WA", "995000", unit="shares"),  # within 1%
        val(FY24, "IS.EPS_BASIC", "2.01", unit="USD/shares"),  # cents rounding
        val(FY24, "IS.EPS_DILUTED", "0.29", unit="USD/shares"),  # split-basis mismatch
        val(FY24, "IS.NET_INCOME_COMMON", "2005000"),
    ]
    (shares,) = results(values, v2, "SHARES_DILUTED_GE_BASIC")
    assert shares.status == "passed" and shares.tolerance == Decimal("10000.00")
    (basic,) = results(values, v2, "EPS_BASIC")
    assert basic.status == "passed" and basic.actual == Decimal("2010000.00")
    (diluted,) = results(values, v2, "EPS_DILUTED")
    assert (diluted.status, diluted.severity) == ("failed", "warning")
    values[1] = val(FY24, "IS.SHARES_DILUTED_WA", "980000", unit="shares")
    assert results(values, v2, "SHARES_DILUTED_GE_BASIC")[0].status == "failed"


def test_completeness_requires_five_fiscal_years(v2: m.MappingSet) -> None:
    years = [FiscalPeriod(y, "FY", date(y, 1, 1), date(y, 12, 31)) for y in range(2020, 2025)]
    values = [val(p, "IS.REVENUE", "1") for p in years]
    values += [val(p, "CF.CAPEX", "-1") for p in years[1:]]
    rows = {r.subject: r for r in results(values, v2, "COMPLETENESS")}
    assert rows["IS.REVENUE"].status == "passed"
    assert rows["CF.CAPEX"].status == "failed" and rows["CF.CAPEX"].inputs["missing_years"] == [
        2020
    ]
    assert rows["BS.CASH"].status == "failed"
    short = {r.subject: r for r in results(values[2:5], v2, "COMPLETENESS")}
    assert short["IS.REVENUE"].status == "failed" and "only 3" in short["IS.REVENUE"].message


def test_validation_is_deterministic(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "BS.TOTAL_ASSETS", "100"),
        val(FY24, "BS.TOTAL_LIABILITIES", "60"),
        val(FY24, "BS.TOTAL_EQUITY", "40"),
        val(FY24, "IS.REVENUE", "10"),
        val(FY24, "IS.COGS", "4"),
        val(FY24, "IS.GROSS_PROFIT", "6"),
    ]
    reference = sha256(validate(values, v2, "USD"))
    for seed in range(5):
        shuffled = values[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - deterministic test shuffle
        assert sha256(validate(shuffled, v2, "USD")) == reference
    summary = summarize(validate(values, v2, "USD"))
    assert summary["by_check"]["BALANCE"] == {"passed": 1, "failed": 0, "not_evaluable": 0}


# ---- Derivations -----------------------------------------------------------------------------


def _derived(values: list[DatasetValue], mapping: m.MappingSet):  # type: ignore[no-untyped-def]
    years = sorted({v.period for v in values if v.period.period_type == "FY"}, key=lambda p: p.end)
    out, by_rule, skipped = derive(values, years, mapping)
    return {(v.period.period_type, v.line_item): v for v in out}, by_rule, skipped


def test_quarterly_cash_flows_from_year_to_date(v2: m.MappingSet) -> None:
    capex = "PaymentsToAcquirePropertyPlantAndEquipment"
    values = [
        val(Q1, "CF.CAPEX", "-10", concept=capex),
        val(H1, "CF.CAPEX", "-25", concept=capex),
        val(NINE, "CF.CAPEX", "-45", concept=capex),
        val(FY24, "CF.CAPEX", "-70", concept=capex),
    ]
    out, by_rule, _ = _derived(values, v2)
    q2, q3, q4 = out[("Q2", "CF.CAPEX")], out[("Q3", "CF.CAPEX")], out[("Q4", "CF.CAPEX")]
    assert (q2.value, q3.value, q4.value) == (Decimal("-15"), Decimal("-20"), Decimal("-25"))
    assert (q2.period.start, q2.period.end) == (date(2024, 4, 1), date(2024, 6, 30))
    assert (q3.period.start, q4.period.start) == (date(2024, 7, 1), date(2024, 10, 1))
    assert q2.value is not None and q3.value is not None and q4.value is not None
    assert Decimal("-10") + q2.value + q3.value + q4.value == Decimal("-70")  # adds back to FY
    assert q2.detail["rule"] == "Q2=H1-Q1"
    assert q2.detail["inputs"][0] == ["H1", "-25", "reported", "0000000001-25-000001"]
    assert len(q2.source_fact_ids) == 2 and q2.provenance == "derived"
    assert by_rule == {"Q2": 1, "Q3": 1, "Q4": 1}


def test_reported_quarter_wins_and_misaligned_windows_are_skipped(v2: m.MappingSet) -> None:
    q2 = FiscalPeriod(2024, "Q2", date(2024, 4, 1), date(2024, 6, 30))
    odd_q3 = FiscalPeriod(2024, "Q3", date(2024, 7, 8), date(2024, 9, 30))
    values = [
        val(FY23, "IS.REVENUE", "400"),  # known year-end → 2024 starts on Jan 1 (open year)
        val(Q1, "IS.REVENUE", "100"),
        val(H1, "IS.REVENUE", "210"),
        val(q2, "IS.REVENUE", "111"),  # reported Q2 (differs from H1 − Q1 = 110)
        val(NINE, "IS.REVENUE", "330"),
        val(odd_q3, "IS.EBIT", "9"),  # another item reported a Q3 with a different window
        val(H1, "IS.EBIT", "20"),
        val(NINE, "IS.EBIT", "29"),
    ]
    out, _, skipped = _derived(values, v2)
    assert ("Q2", "IS.REVENUE") not in out  # the reported Q2 is kept as reported
    # Revenue's Q3 would start on Jul 1, but the dataset's Q3 period (reported for EBIT)
    # starts on Jul 8: deriving would create a second, overlapping Q3 → skipped.
    assert ("Q3", "IS.REVENUE") not in out
    assert skipped["Q3=9M-H1:window_mismatch"] == 1
    assert ("Q3", "IS.EBIT") not in out  # reported Q3 EBIT wins over 9M − H1
    # Without any known fiscal year, no quarter can be placed: nothing is derived.
    assert derive(values[1:], [], v2)[0] == []


def test_formulas_gross_profit_ebitda_fcf(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "IS.REVENUE", "1000"),
        val(FY24, "IS.COGS", "600"),
        val(FY24, "IS.EBIT", "250"),
        val(FY24, "CF.DA", "40"),
        val(FY24, "CF.CFO", "300"),
        val(FY24, "CF.CAPEX", "-80"),
        val(FY23, "IS.REVENUE", "900"),
        val(FY23, "IS.COGS", "500"),
        val(FY23, "IS.GROSS_PROFIT", "410"),  # reported: never replaced by the formula
        val(FY23, "IS.EBIT", "200"),  # D&A missing → no EBITDA, never EBIT + 0
    ]
    out, by_rule, skipped = _derived(values, v2)
    gp = out[("FY", "IS.GROSS_PROFIT")]
    assert gp.value == Decimal("400") and gp.period == FY24
    assert gp.concept == "formula:IS.GROSS_PROFIT" and gp.detail["rule"] == (
        "IS.GROSS_PROFIT=+IS.REVENUE-IS.COGS"
    )
    assert [i[0] for i in gp.detail["inputs"]] == ["IS.REVENUE", "IS.COGS"]
    assert out[("FY", "IS.EBITDA")].value == Decimal("290")
    assert out[("FY", "CF.FCF")].value == Decimal("220")
    assert sum(1 for k in out if k[1] == "IS.GROSS_PROFIT") == 1
    assert skipped["IS.EBITDA:missing_input"] == 1
    assert by_rule["IS.GROSS_PROFIT"] == 1


def test_formula_skips_ambiguous_and_unit_mismatch(v2: m.MappingSet) -> None:
    values = [
        val(FY24, "IS.REVENUE", None),
        val(FY24, "IS.COGS", "600"),
        val(FY24, "CF.CFO", "300"),
        val(FY24, "CF.CAPEX", "-80", unit="EUR"),
    ]
    out, _, skipped = _derived(values, v2)
    assert ("FY", "IS.GROSS_PROFIT") not in out and ("FY", "CF.FCF") not in out
    assert skipped["IS.GROSS_PROFIT:ambiguous_input"] == 1
    assert skipped["CF.FCF:unit_mismatch"] == 1


def test_ltm_from_previous_year_and_ytd(v2: m.MappingSet) -> None:
    nine23 = FiscalPeriod(2023, "9M", date(2023, 1, 1), date(2023, 9, 30))
    values = [
        val(FY23, "IS.REVENUE", "1000"),
        val(nine23, "IS.REVENUE", "700"),
        val(NINE, "IS.REVENUE", "800"),
        val(FY23, "IS.EBIT", "100", concept="A"),
        val(nine23, "IS.EBIT", "70", concept="A"),
        val(NINE, "IS.EBIT", "80", concept="B"),  # concept changed → no LTM
    ]
    out, _, skipped = _derived(values, v2)
    ltm = out[("LTM", "IS.REVENUE")]
    assert ltm.value == Decimal("1100")
    assert (ltm.period.start, ltm.period.end) == (date(2023, 10, 1), date(2024, 9, 30))
    assert ltm.detail["rule"] == "LTM=FY(prev)+YTD-YTD(prev)" and len(ltm.source_fact_ids) == 3
    assert ("LTM", "IS.EBIT") not in out and skipped["LTM:concept_mismatch"] == 1
    # LTM values are never validated as fiscal periods.
    checked = validate([*values, ltm], v2, "USD")
    assert all(r.period is None or r.period.period_type != "LTM" for r in checked)
