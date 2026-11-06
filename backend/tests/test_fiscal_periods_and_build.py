"""Fiscal periods, Q4 derivation and deterministic dataset construction (pure logic)."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import Policy
from erp_backend.standardization import mapping as m
from erp_backend.standardization.dataset import (
    DatasetBuildError,
    build,
    decimal_str,
    diff,
)
from erp_backend.standardization.periods import FiscalCalendar, FiscalPeriod, fiscal_year_label

ROOT = Path(__file__).resolve().parents[2] / "mappings"


@pytest.fixture(scope="module")
def v1() -> m.MappingSet:
    return m.load(ROOT, "v1")


# ---- Fiscal periods --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("end", "label"),
    [
        (date(2024, 12, 31), 2024),
        (date(2024, 9, 28), 2024),  # Apple, 52-week year
        (date(2021, 1, 2), 2020),  # 52/53-week year slipping into January
        (date(2025, 2, 1), 2025),  # retail year ending February
        (date(2025, 1, 31), 2025),
        (date(2024, 6, 30), 2024),  # June year-end
    ],
)
def test_fiscal_year_label(end: date, label: int) -> None:
    assert fiscal_year_label(end) == label


def _cal(*windows: tuple[date, date], weight: int = 1) -> FiscalCalendar:
    return FiscalCalendar.from_windows({w: weight for w in windows})


APPLE_24 = (date(2023, 10, 1), date(2024, 9, 28))  # 364 days
APPLE_23 = (date(2022, 9, 25), date(2023, 9, 30))  # 371 days


def test_52_53_week_years_and_quarters() -> None:
    cal = _cal(APPLE_23, APPLE_24)
    assert [(p.fiscal_year, p.weeks) for p in cal.years] == [(2023, 53), (2024, 52)]
    q1 = cal.classify(date(2023, 10, 1), date(2023, 12, 30))
    q3 = cal.classify(date(2024, 3, 31), date(2024, 6, 29))
    nine = cal.classify(date(2023, 10, 1), date(2024, 6, 29))
    six = cal.classify(date(2023, 10, 1), date(2024, 3, 30))
    assert q1 == FiscalPeriod(2024, "Q1", date(2023, 10, 1), date(2023, 12, 30))
    assert q3 is not None and q3.period_type == "Q3"
    assert nine is not None and nine.period_type == "9M"
    assert six is not None and six.period_type == "H1"
    assert cal.classify(*APPLE_24) == cal.years[1]


def test_non_december_and_open_fiscal_year() -> None:
    cal = _cal((date(2023, 7, 1), date(2024, 6, 30)))  # June year-end
    q1_next = cal.classify(date(2024, 7, 1), date(2024, 9, 30))
    assert q1_next == FiscalPeriod(2025, "Q1", date(2024, 7, 1), date(2024, 9, 30))
    assert cal.classify(date(2023, 7, 1), date(2023, 9, 30)) == FiscalPeriod(
        2024, "Q1", date(2023, 7, 1), date(2023, 9, 30)
    )
    # A quarter that does not start on the fiscal-year start is not Q1.
    assert cal.classify(date(2023, 7, 15), date(2023, 10, 14)) is None
    # Before the first known fiscal year: unassigned, never guessed.
    assert cal.classify(date(2023, 1, 1), date(2023, 3, 31)) is None
    # Trailing-twelve-month windows are not fiscal years.
    assert cal.classify(date(2023, 10, 1), date(2024, 9, 30)) is None


def test_overlapping_annual_windows_keep_the_better_supported_one() -> None:
    a, b = (date(2023, 1, 1), date(2023, 12, 31)), (date(2023, 4, 1), date(2024, 3, 31))
    cal = FiscalCalendar.from_windows({a: 40, b: 3})
    assert [(p.start, p.end) for p in cal.years] == [a]
    assert cal.rejected == (b,)


# ---- Dataset build ---------------------------------------------------------------------------

FY = (date(2024, 1, 1), date(2024, 12, 31))
FY_PRIOR = (date(2023, 1, 1), date(2023, 12, 31))
NINE = (date(2024, 1, 1), date(2024, 9, 30))
Q3 = (date(2024, 7, 1), date(2024, 9, 30))
Q4 = (date(2024, 10, 1), date(2024, 12, 31))
K = dict(accn="0000000001-25-000001", form="10-K", filed=date(2025, 2, 1))
Q = dict(accn="0000000001-24-000003", form="10-Q", filed=date(2024, 11, 1))


def f(
    concept: str,
    value: str,
    period: tuple[date | None, date],
    *,
    accn: str,
    form: str,
    filed: date,
    unit: str = "USD",
) -> RawFact:
    start, end = period
    return RawFact(
        "us-gaap", concept, unit, Decimal(value), start, end, None, None, form, filed, accn, None
    )


def facts(*items: RawFact) -> dict[RawFact, int]:
    return {fact: i for i, fact in enumerate(items, start=1)}


def _values(built):  # type: ignore[no-untyped-def]
    return {(v.period.period_type, v.line_item): v for v in built.values}


def test_q4_is_derived_from_fy_minus_nine_months(v1: m.MappingSet) -> None:
    built = build(
        facts(
            f("Revenues", "1000", FY, **K),
            f("Revenues", "700", NINE, **Q),
            f("Revenues", "250", Q3, **Q),
        ),
        v1,
        taxonomy="us-gaap",
        currency="USD",
    )
    vals = _values(built)
    q4 = vals[("Q4", "IS.REVENUE")]
    assert q4.value == Decimal("300") and q4.provenance == "derived"
    assert (q4.period.start, q4.period.end) == Q4
    assert q4.source_fact_ids == (1, 2)
    assert q4.detail["rule"] == "Q4=FY-9M"
    assert q4.detail["accessions"] == [K["accn"], Q["accn"]]
    assert vals[("FY", "IS.REVENUE")].provenance == "reported"
    assert built.summary["values_derived"] == 1


def test_q4_is_not_derived_when_unsafe(v1: m.MappingSet) -> None:
    built = build(
        facts(
            # Concept mismatch between FY and 9M: deriving would mix concepts.
            f("RevenueFromContractWithCustomerExcludingAssessedTax", "1000", FY, **K),
            f("Revenues", "700", NINE, **Q),
            # Per-share and count items are not additive.
            f("EarningsPerShareDiluted", "4", FY, unit="USD/shares", **K),
            f("EarningsPerShareDiluted", "3", NINE, unit="USD/shares", **Q),
            # A reported Q4 is used as reported, not overwritten by a derivation.
            f("OperatingIncomeLoss", "100", FY, **K),
            f("OperatingIncomeLoss", "70", NINE, **Q),
            f("OperatingIncomeLoss", "31", Q4, **K),
            # 9M ambiguous within one filing: no derivation and no silent fallback.
            f("NetIncomeLoss", "50", FY, **K),
            f("NetIncomeLoss", "30", NINE, **Q),
            f("NetIncomeLoss", "31", NINE, **Q),
        ),
        v1,
        taxonomy="us-gaap",
        currency="USD",
    )
    vals = _values(built)
    assert ("Q4", "IS.REVENUE") not in vals
    assert ("Q4", "IS.EPS_DILUTED") not in vals
    assert vals[("Q4", "IS.EBIT")].value == Decimal("31")
    assert vals[("Q4", "IS.EBIT")].provenance == "reported"
    assert ("Q4", "IS.NET_INCOME") not in vals
    nine_ni = vals[("9M", "IS.NET_INCOME")]
    assert nine_ni.status == "ambiguous" and nine_ni.value is None
    assert nine_ni.detail["ambiguous_accessions"] == [Q["accn"]]
    assert built.summary["q4_not_derived"] == {
        # NetIncomeLoss feeds IS.NET_INCOME, IS.NET_INCOME_COMMON and CF.NET_INCOME.
        "ambiguous_input": 3,
        "concept_mismatch": 1,
    }
    assert built.summary["values_ambiguous"] == 3


def test_balance_sheet_values_attach_to_fy_and_quarter_ends(v1: m.MappingSet) -> None:
    built = build(
        facts(
            f("Revenues", "1000", FY, **K),
            f("Revenues", "250", Q3, **Q),
            f("Assets", "500", (None, FY[1]), **K),
            f("Assets", "450", (None, Q3[1]), **Q),
            f("Assets", "1", (None, date(2024, 5, 17)), **Q),  # not a period end
        ),
        v1,
        taxonomy="us-gaap",
        currency="USD",
    )
    vals = _values(built)
    assert vals[("FY", "BS.TOTAL_ASSETS")].value == Decimal("500")
    assert vals[("Q3", "BS.TOTAL_ASSETS")].value == Decimal("450")
    assert built.summary["unassigned_values"] == {"instant": 1}


def test_cutoff_forms_and_policy(v1: m.MappingSet) -> None:
    original = f("Revenues", "1000", FY, **K)
    restated = f(
        "Revenues", "990", FY, accn="0000000001-26-000001", form="10-K", filed=date(2026, 2, 1)
    )
    prospectus = f(
        "Revenues", "5", FY_PRIOR, accn="0000000001-25-000009", form="424B2", filed=date(2025, 3, 1)
    )
    inputs = facts(original, restated, prospectus)
    latest = build(inputs, v1, taxonomy="us-gaap", currency="USD")
    assert latest.header.facts_as_of == date(2026, 2, 1)
    rev = _values(latest)[("FY", "IS.REVENUE")]
    assert rev.value == Decimal("990") and rev.detail["restated"] is True
    assert latest.summary["excluded_facts_other_forms"] == 1
    assert all(v.period.fiscal_year == 2024 for v in latest.values)  # 424B2 fact ignored
    first = build(inputs, v1, taxonomy="us-gaap", currency="USD", policy=Policy.FIRST_REPORTED)
    assert _values(first)[("FY", "IS.REVENUE")].value == Decimal("1000")
    as_of = build(inputs, v1, taxonomy="us-gaap", currency="USD", facts_as_of=date(2025, 6, 1))
    assert _values(as_of)[("FY", "IS.REVENUE")].value == Decimal("1000")
    with pytest.raises(DatasetBuildError, match="No eligible"):
        build(inputs, v1, taxonomy="us-gaap", currency="USD", facts_as_of=date(2020, 1, 1))
    with pytest.raises(DatasetBuildError, match="No ifrs-full facts"):
        build(inputs, v1, taxonomy="ifrs-full", currency="USD")


def _sample() -> list[RawFact]:
    out = []
    for year in range(2019, 2025):
        fy = (date(year, 1, 1), date(year, 12, 31))
        nine = (date(year, 1, 1), date(year, 9, 30))
        k = dict(
            accn=f"0000000001-{year - 1999:02d}-000001", form="10-K", filed=date(year + 1, 2, 1)
        )
        q = dict(accn=f"0000000001-{year - 2000:02d}-000003", form="10-Q", filed=date(year, 11, 1))
        out += [
            f("Revenues", str(1000 + year), fy, **k),
            f("Revenues", str(700 + year), nine, **q),
            f("PaymentsToAcquirePropertyPlantAndEquipment", "40.50", fy, **k),
            f("PaymentsToAcquirePropertyPlantAndEquipment", "30.25", nine, **q),
            f("Assets", str(5000 + year), (None, fy[1]), **k),
        ]  # fmt: skip
    return out


def test_build_is_byte_identical_regardless_of_input_order(v1: m.MappingSet) -> None:
    base = _sample()
    reference = build({x: i for i, x in enumerate(base, 1)}, v1, taxonomy="us-gaap", currency="USD")
    ids = {x: i for i, x in enumerate(base, 1)}
    for seed in range(5):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - deterministic test shuffle
        again = build({x: ids[x] for x in shuffled}, v1, taxonomy="us-gaap", currency="USD")
        assert again.canonical == reference.canonical
    capex_q4 = _values(reference)[("Q4", "CF.CAPEX")]
    assert capex_q4.value == Decimal("-10.25")  # sign-normalized outflow
    assert b'"value":"-10.25"' in reference.canonical


def test_hashes_track_inputs_values_and_mapping(v1: m.MappingSet) -> None:
    base = facts(*_sample())
    ref = build(base, v1, taxonomy="us-gaap", currency="USD")
    # An unmapped fact changes the inputs fingerprint but not a single value.
    extra = dict(base)
    extra[f("SomethingUnmapped", "1", FY, **K)] = 999
    more = build(extra, v1, taxonomy="us-gaap", currency="USD")
    assert more.header.input_sha256 != ref.header.input_sha256
    assert diff(list(ref.values), list(more.values))["changed"] == 0
    assert more.content_sha256 != ref.content_sha256
    # A changed value is visible in the diff.
    changed = {
        (fact if fact.concept != "Assets" or fact.period_end.year != 2024
         else f("Assets", "1", (None, FY[1]), **K)): i
        for fact, i in base.items()
    }  # fmt: skip
    d = diff(list(ref.values), list(build(changed, v1, taxonomy="us-gaap", currency="USD").values))
    assert d["changed"] == 1 and d["changed_examples"][0][-1] == "BS.TOTAL_ASSETS"
    # Mapping identity is part of the header.
    assert ref.header.mapping_sha256 == v1.sha256
    assert b'"mapping_version":"v1"' in ref.canonical


@pytest.mark.parametrize(
    ("value", "text"),
    [("100", "100"), ("1E+2", "100"), ("6.080", "6.08"), ("-0", "0"), ("0.000", "0"),
     ("-10.25", "-10.25"), ("391035000000", "391035000000")],
)  # fmt: skip
def test_decimal_canonicalization(value: str, text: str) -> None:
    assert decimal_str(Decimal(value)) == text


def test_period_ordering_is_stable() -> None:
    periods = [
        FiscalPeriod(2024, t, date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=d))
        for t, d in (("Q4", 90), ("FY", 365), ("9M", 270), ("Q1", 90), ("H1", 180))
    ]
    ordered = sorted(periods, key=FiscalPeriod.sort_key)
    assert [p.period_type for p in ordered] == ["FY", "Q1", "H1", "9M", "Q4"]
