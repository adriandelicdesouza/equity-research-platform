"""SEC parsers, deterministic fact selection (D11) and interim coverage (D12) — pure logic."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest
from erp_backend.ingestion import coverage
from erp_backend.ingestion.sec_parse import (
    RawFact,
    SecParseError,
    parse_companyfacts,
    parse_submissions,
    parse_ticker_map,
)
from erp_backend.ingestion.selection import Policy, Status, fact_key, restatements, select
from hypothesis import given, settings
from hypothesis import strategies as st

from . import sec_payloads as p

# ---- Parsers --------------------------------------------------------------------------------


def test_ticker_map_parses_and_normalizes() -> None:
    rows = parse_ticker_map(
        p.ticker_map(
            [(1, "One Co", "one", "Nasdaq"), (2, "Two", "", None), (3, "Three", "THR", None)]
        )
    )
    assert [(r.cik, r.ticker, r.exchange) for r in rows] == [(1, "ONE", "Nasdaq"), (3, "THR", None)]


@pytest.mark.parametrize("payload", [b"not json", b'{"data": []}', p.ticker_map([])])
def test_ticker_map_rejects_malformed(payload: bytes) -> None:
    with pytest.raises(SecParseError):
        parse_ticker_map(payload)


def test_submissions_profile_and_country_inference() -> None:
    sub = parse_submissions(
        p.submissions(p.MAPLE_CIK, "Maple Ltd", ["MPL"], ["NYSE"], state="A6", fye="1031")
    )
    assert (sub.cik, sub.country, sub.fiscal_year_end_mmdd) == (p.MAPLE_CIK, "CA", "1031")
    assert sub.filings[0].form == "10-K" and sub.filings[0].filed_date == date(2024, 2, 1)
    us = parse_submissions(p.submissions(p.ACME_CIK, "Acme", ["ACME"], ["Nasdaq"], state="CA"))
    assert us.country == "US"  # "CA" is California in SEC state codes


def test_submissions_missing_fields_rejected() -> None:
    with pytest.raises(SecParseError):
        parse_submissions(b'{"cik": "1"}')


def test_companyfacts_values_are_exact_decimals() -> None:
    payload = (
        b'{"cik": 7, "facts": {"us-gaap": {"X": {"units": {"USD": [{"end": "2024-12-31",'
        b' "val": 0.1, "accn": "a", "form": "10-K", "filed": "2025-02-01"}, {"end": '
        b'"2024-12-31", "val": 123456789012345678901234567890, "accn": "b", "form": '
        b'"10-K", "filed": "2025-02-02"}]}}}}}'
    )
    cik, facts = parse_companyfacts(payload)
    assert cik == 7
    assert facts[0].value == Decimal("0.1") and isinstance(facts[0].value, Decimal)
    assert facts[1].value == Decimal("123456789012345678901234567890")


@pytest.mark.parametrize(
    "obs",
    [
        '{"val": 1, "accn": "a", "form": "10-K", "filed": "2025-01-01"}',  # no end
        '{"end": "2024-12-31", "val": "1", "accn": "a", "form": "10-K", "filed": "2025-01-01"}',
        '{"end": "2024-13-31", "val": 1, "accn": "a", "form": "10-K", "filed": "2025-01-01"}',
    ],
)
def test_companyfacts_incomplete_or_invalid_observations_rejected(obs: str) -> None:
    payload = (
        '{"cik": 7, "facts": {"us-gaap": {"X": {"units": {"USD": [' + obs + "]}}}}}"
    ).encode()
    with pytest.raises(SecParseError):
        parse_companyfacts(payload)


# ---- Selection (D11) ------------------------------------------------------------------------


def fact(
    value: str,
    filed: str,
    accn: str,
    form: str = "10-K",
    *,
    concept: str = "Revenues",
    start: str | None = "2023-01-01",
    end: str = "2023-12-31",
) -> RawFact:
    return RawFact(
        "us-gaap",
        concept,
        "USD",
        Decimal(value),
        date.fromisoformat(start) if start else None,
        date.fromisoformat(end),
        2023,
        "FY",
        form,
        date.fromisoformat(filed),
        accn,
        None,
    )


ORIGINAL = fact("1000", "2024-02-01", "0001-24-000010")
RESTATED = fact("1010", "2025-02-01", "0001-25-000010")


def test_first_and_latest_reported_with_restatement() -> None:
    sel = select([RESTATED, ORIGINAL])[fact_key(ORIGINAL)]
    assert sel.status is Status.OK and sel.candidates == 2
    assert sel.value(Policy.FIRST_REPORTED) == Decimal("1000")
    assert sel.value(Policy.LATEST_REPORTED) == Decimal("1010")
    assert sel.restated and restatements({sel.key: sel}) == [sel]


def test_amendment_beats_original_filed_same_day_regardless_of_accession() -> None:
    original = fact("100", "2024-02-01", "0001-24-000009", "10-K")
    amendment = fact("105", "2024-02-01", "0001-24-000001", "10-K/A")  # smaller accession
    sel = select([amendment, original])[fact_key(original)]
    assert sel.value(Policy.LATEST_REPORTED) == Decimal("105")
    assert sel.value(Policy.FIRST_REPORTED) == Decimal("100")


def test_accession_breaks_remaining_ties() -> None:
    a = fact("1", "2024-02-01", "0001-24-000001")
    b = fact("2", "2024-02-01", "0001-24-000002")
    sel = select([b, a])[fact_key(a)]
    assert (sel.value(Policy.FIRST_REPORTED), sel.value(Policy.LATEST_REPORTED)) == (1, 2)


def test_facts_as_of_cutoff_reconstructs_point_in_time() -> None:
    sel = select([ORIGINAL, RESTATED], facts_as_of=date(2024, 12, 31))[fact_key(ORIGINAL)]
    assert sel.value(Policy.LATEST_REPORTED) == Decimal("1000") and not sel.restated
    assert select([RESTATED], facts_as_of=date(2024, 12, 31)) == {}


def test_conflicting_values_within_one_filing_are_ambiguous() -> None:
    a = fact("5000", "2024-02-01", "0001-24-000010", concept="Assets", start=None)
    b = fact("5001", "2024-02-01", "0001-24-000010", concept="Assets", start=None)
    dup = fact("5000", "2024-02-01", "0001-24-000010", concept="Assets", start=None)
    sel = select([a, b, dup])[fact_key(a)]
    assert sel.status is Status.AMBIGUOUS and sel.ambiguous_accessions == ("0001-24-000010",)
    assert sel.chosen(Policy.LATEST_REPORTED) is None
    assert select([a, dup])[fact_key(a)].status is Status.OK  # identical repeats are fine


def test_keys_distinguish_periods_units_and_concepts() -> None:
    q = fact("1", "2024-02-01", "x", start="2023-10-01")
    y = fact("1", "2024-02-01", "x")
    assert len(select([q, y])) == 2


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, 3), st.booleans(), st.integers(1, 9)),
        min_size=1,
        max_size=12,
        unique_by=lambda t: t[:3],
    ),
    st.randoms(),
)
def test_selection_is_independent_of_input_order(specs, rnd: random.Random) -> None:  # type: ignore[no-untyped-def]
    facts = [
        fact(str(v), f"2024-0{1 + d}-01", f"0001-24-00000{a}", "10-K/A" if amend else "10-K")
        for d, a, amend, v in specs
    ]
    baseline = select(facts)
    shuffled = facts[:]
    rnd.shuffle(shuffled)
    assert select(shuffled) == baseline


# ---- Interim coverage (D12) ------------------------------------------------------------------


def _facts(payload_facts: dict) -> list[RawFact]:  # type: ignore[type-arg]
    return parse_companyfacts(p.companyfacts(1, payload_facts))[1]


QUARTERS = p.quarter_ends(date(2024, 9, 30), 10)


def test_full_coverage_when_last_eight_quarters_complete() -> None:
    cov = coverage.detect(_facts(p.interim_facts(QUARTERS)))
    assert cov.level == "full" and len(cov.periods) == 8
    assert all(per["complete"] and not per["from_6k"] for per in cov.periods)


def test_partial_coverage_lists_missing_items() -> None:
    cov = coverage.detect(_facts(p.interim_facts(QUARTERS, skip={9: {"operating_income"}})))
    assert cov.level == "partial"
    assert cov.periods[-1]["missing"] == ["operating_income"]


def test_no_interim_forms_means_none() -> None:
    cov = coverage.detect(_facts(p.interim_facts(QUARTERS, form="10-K")))
    assert cov.level == "none" and cov.periods == ()


def test_ifrs_6k_interim_data_detected_and_flagged() -> None:
    cov = coverage.detect(
        _facts(p.interim_facts(QUARTERS, tax="ifrs-full", form="6-K", unit="CAD"))
    )
    assert cov.level == "full" and all(per["from_6k"] for per in cov.periods)


# ---- Regressions found with live SEC data (Session 3) ----------------------------------------


def test_fiscal_year_end_comparatives_in_10q_are_not_interim_periods() -> None:
    """10-Qs restate the prior fiscal year-end balance sheet; that date is not a quarter."""
    facts = p.interim_facts(QUARTERS)
    fye = "2023-12-31"
    facts["us-gaap"]["Assets"]["USD"].append(
        p.obs(9999, fye, accn="0009990001-24-000777", form="10-Q", filed="2024-05-01")
    )
    facts["us-gaap"]["CashAndCashEquivalentsAtCarryingValue"]["USD"].append(
        p.obs(9999, fye, accn="0009990001-24-000777", form="10-Q", filed="2024-05-01")
    )
    cov = coverage.detect(_facts(facts))
    assert fye not in [per["period_end"] for per in cov.periods]
    assert cov.level == "full"


@pytest.mark.parametrize(
    ("incorporation", "expected"), [("Z4", "CA"), ("A0", "CA"), ("DE", "US"), ("X9", None)]
)
def test_country_falls_back_to_incorporation_when_no_business_address(
    incorporation: str, expected: str | None
) -> None:
    import json

    doc = json.loads(p.submissions(1, "Co", ["CO"], ["NYSE"], state=incorporation))
    doc["addresses"] = {"business": {"stateOrCountry": None}}
    assert parse_submissions(json.dumps(doc).encode()).country == expected


def test_reporting_currency_from_interim_filings_when_no_annual_xbrl() -> None:
    from erp_backend.ingestion.sec_ingest import infer_currency

    facts = _facts(p.interim_facts(QUARTERS, tax="ifrs-full", form="6-K", unit="CAD"))
    registration = fact("1", "2030-01-01", "reg-1", "F-10")  # newer, but not periodic
    assert infer_currency([*facts, registration]) == "CAD"


def test_stale_interim_history_does_not_count_as_current_coverage() -> None:
    """CNI-like case: complete quarters long ago, recent results only in annual filings."""
    old = p.interim_facts(p.quarter_ends(date(2009, 9, 30), 4))
    recent_annual = p.obs(
        5000,
        "2025-12-31",
        start="2025-01-01",
        accn="0009990001-26-000001",
        form="10-K",
        filed="2026-02-01",
    )
    old["us-gaap"]["Revenues"]["USD"].append(recent_annual)
    cov = coverage.detect(_facts(old))
    assert cov.level == "none" and cov.periods == ()
