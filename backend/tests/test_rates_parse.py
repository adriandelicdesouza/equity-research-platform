"""US Treasury and Bank of Canada adapters: offline parsing and normalization (D58).

Synthetic payloads only (``rates_payloads``); no database, no network.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from erp_backend.rates import bank_of_canada, treasury
from erp_backend.rates.model import (
    FX_SERIES,
    RATE_SERIES,
    IssueLevel,
    ParseError,
    RateType,
)

from tests.rates_payloads import TREASURY_GOOD, TREASURY_HEADER, VALET_GOOD, treasury_csv, valet

TODAY = date(2024, 12, 31)


def codes(parsed: object) -> list[str]:
    return [i.code for i in parsed.issues]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- registry


def test_series_identity_is_explicit_and_unique() -> None:
    assert len({s.code for s in RATE_SERIES}) == len(RATE_SERIES)
    assert len({(s.provider_code, s.provider_series_id) for s in RATE_SERIES}) == len(RATE_SERIES)
    ust10 = next(s for s in RATE_SERIES if s.code == "UST_PAR_10Y")
    goc10 = next(s for s in RATE_SERIES if s.code == "GOC_BMK_10Y")
    # Same tenor, different series: never interchangeable.
    assert (ust10.tenor, goc10.tenor) == ("10Y", "10Y")
    assert ust10.rate_type is RateType.PAR_YIELD
    assert goc10.rate_type is RateType.BENCHMARK_BOND_YIELD
    assert (ust10.currency, goc10.currency) == ("USD", "CAD")
    assert next(s for s in RATE_SERIES if s.code == "GOC_BMK_LONG").tenor_months is None


def test_fx_registry_states_direction() -> None:
    usd = next(s for s in FX_SERIES if s.provider_series_id == "FXUSDCAD")
    assert (usd.base, usd.quote, usd.expected_label) == ("USD", "CAD", "USD/CAD")
    assert all(s.quote == "CAD" and s.base != "CAD" for s in FX_SERIES)


# --------------------------------------------------------------------------- Treasury


def test_treasury_parses_every_tenor_as_its_own_series() -> None:
    parsed = treasury.parse_par_yield_csv(TREASURY_GOOD, TODAY)
    assert parsed.errors == ()
    assert parsed.missing == 0
    assert len(parsed.rates) == 3 * 13
    ten = [o for o in parsed.rates if o.series_code == "UST_PAR_10Y"]
    assert [(o.obs_date, o.value) for o in ten] == [
        (date(2024, 3, 1), Decimal("4.13")),
        (date(2024, 3, 4), Decimal("4.14")),
        (date(2024, 3, 5), Decimal("4.15")),
    ]
    one_month = next(o for o in parsed.rates if o.series_code == "UST_PAR_1M")
    assert one_month.value == Decimal("5.48")  # percentage points, as published


def test_treasury_missing_values_are_reported_not_filled() -> None:
    data = treasury_csv(
        "03/05/2024,,5.51,5.48,N/A,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29",
        "03/04/2024,5.49,5.50,5.47,5.44,5.37,5.01,4.54,4.36,4.16,4.16,4.14,4.41,4.28",
    )
    parsed = treasury.parse_par_yield_csv(data, TODAY)
    assert parsed.missing == 2
    assert parsed.errors == ()
    dates_1m = [o.obs_date for o in parsed.rates if o.series_code == "UST_PAR_1M"]
    assert dates_1m == [date(2024, 3, 4)]  # no carried-forward value for 03/05


def test_treasury_older_layout_without_some_columns() -> None:
    header = 'Date,"3 Mo","6 Mo","1 Yr","2 Yr","5 Yr","10 Yr","30 Yr"'
    parsed = treasury.parse_par_yield_csv(
        treasury_csv("01/02/2004,0.93,1.01,1.24,1.83,3.14,4.38,5.17", header=header), TODAY
    )
    assert parsed.series_seen == (
        "UST_PAR_3M",
        "UST_PAR_6M",
        "UST_PAR_1Y",
        "UST_PAR_2Y",
        "UST_PAR_5Y",
        "UST_PAR_10Y",
        "UST_PAR_30Y",
    )
    assert len(parsed.rates) == 7


def test_unregistered_tenor_column_is_reported_and_ignored() -> None:
    header = TREASURY_HEADER.replace('"2 Mo",', '"2 Mo","1.5 Mo",')
    row = "03/05/2024,5.50,5.51,5.52,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29"
    parsed = treasury.parse_par_yield_csv(treasury_csv(row, header=header), TODAY)
    assert "unregistered_column" in codes(parsed)
    assert len(parsed.rates) == 13
    assert next(o for o in parsed.rates if o.series_code == "UST_PAR_3M").value == Decimal("5.48")


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (
            "2024-03-05,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29",
            "invalid_date",
        ),
        (
            "13/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29",
            "invalid_date",
        ),
        (
            "03/05/2025,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29",
            "future_date",
        ),
        ("03/05/2024,5.50,5.51", "field_count"),
    ],
)
def test_treasury_bad_rows_are_rejected_whole(row: str, code: str) -> None:
    parsed = treasury.parse_par_yield_csv(treasury_csv(row), TODAY)
    assert code in codes(parsed)
    assert parsed.rates == ()


@pytest.mark.parametrize(
    ("cell", "code", "kept"),
    [
        ("4.1e0", "invalid_number", False),
        ("4,15", "field_count", False),
        ("415", "implausible_rate", False),  # basis points or a parsing slip, not a level
        ("-0.05", "unusual_rate", True),
        ("4.15%", "invalid_number", False),
    ],
)
def test_treasury_value_validation(cell: str, code: str, kept: bool) -> None:
    row = f"03/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,{cell},4.42,4.29"
    parsed = treasury.parse_par_yield_csv(treasury_csv(row), TODAY)
    assert code in codes(parsed)
    assert any(o.series_code == "UST_PAR_10Y" for o in parsed.rates) is kept


def test_treasury_duplicate_dates_collapse_and_conflicts_are_dropped() -> None:
    same = "03/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29"
    differs = "03/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.16,4.42,4.29"
    parsed = treasury.parse_par_yield_csv(treasury_csv(same, same), TODAY)
    assert "duplicate_in_payload" in codes(parsed)
    assert len(parsed.rates) == 13
    parsed = treasury.parse_par_yield_csv(treasury_csv(same, differs), TODAY)
    conflict = [i for i in parsed.issues if i.code == "conflict_in_payload"]
    assert len(conflict) == 1 and conflict[0].level is IssueLevel.ERROR
    assert not any(o.series_code == "UST_PAR_10Y" for o in parsed.rates)
    assert len(parsed.rates) == 12  # the other tenors agree and are kept


def test_weekend_date_is_a_warning() -> None:
    row = "03/02/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29"
    parsed = treasury.parse_par_yield_csv(treasury_csv(row), TODAY)
    assert "weekend_date" in codes(parsed) and len(parsed.rates) == 13


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "Empty"),
        (b"<html><body>Service unavailable</body></html>", "not a CSV"),
        (b"Day,10 Yr\n03/05/2024,4.15\n", "First column"),
        (b'Date,"10 Yr","10 Yr"\n03/05/2024,4.15,4.15\n', "Duplicate"),
        (b"Date,Foo\n03/05/2024,1\n", "No registered tenor"),
        (b"\xff\xfe", "UTF-8"),
    ],
)
def test_treasury_unusable_payloads(payload: bytes, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        treasury.parse_par_yield_csv(payload, TODAY)


def test_treasury_url_is_per_year_and_bounded() -> None:
    url, params = treasury.year_url(2024, TODAY)
    assert url.endswith("/daily-treasury-rates.csv/2024/all")
    assert params["type"] == "daily_treasury_yield_curve"
    with pytest.raises(ValueError, match="year"):
        treasury.year_url(2025, TODAY)
    with pytest.raises(ValueError, match="year"):
        treasury.year_url(1989, TODAY)


# --------------------------------------------------------------------------- Bank of Canada


def test_valet_parses_rates_and_fx_with_explicit_direction() -> None:
    parsed = bank_of_canada.parse_valet(VALET_GOOD, TODAY)
    assert parsed.errors == ()
    first = parsed.rates[0]
    assert (first.series_code, first.obs_date, first.value) == (
        "GOC_BMK_10Y",
        date(2024, 3, 1),
        Decimal("3.41"),
    )
    fx = parsed.fx[0]
    assert (fx.base, fx.quote, fx.rate, fx.provider_series_id) == (
        "USD",
        "CAD",
        Decimal("1.3550"),  # 1 USD = 1.3550 CAD; never inverted
        "FXUSDCAD",
    )
    assert len(parsed.fx) == 3 and len(parsed.rates) == 3


@pytest.mark.parametrize("cell", [None, {"v": ""}, {"v": None}, "absent"])
def test_valet_missing_observations_are_not_substituted(cell: object) -> None:
    obs: dict[str, object] = {"d": "2024-03-05", "BD.CDN.10YR.DQ.YLD": {"v": "3.47"}}
    if cell != "absent":
        obs["FXUSDCAD"] = cell
    parsed = bank_of_canada.parse_valet(
        valet([{"d": "2024-03-04", "FXUSDCAD": {"v": "1.3562"}}, obs]), TODAY
    )
    assert [o.obs_date for o in parsed.fx] == [date(2024, 3, 4)]
    assert parsed.missing == 2  # the 10Y on 03-04 and the FX on 03-05
    assert parsed.errors == ()


def test_fx_label_contradicting_direction_rejects_the_series() -> None:
    details = {"FXUSDCAD": {"label": "CAD/USD", "description": "inverted"}}
    parsed = bank_of_canada.parse_valet(
        valet([{"d": "2024-03-04", "FXUSDCAD": {"v": "0.7373"}}], details), TODAY
    )
    assert "fx_direction_mismatch" in codes(parsed)
    assert parsed.fx == ()


def test_fx_without_label_uses_registry_direction_with_warning() -> None:
    parsed = bank_of_canada.parse_valet(
        valet([{"d": "2024-03-04", "FXUSDCAD": {"v": "1.3562"}}], {"FXUSDCAD": {}}), TODAY
    )
    assert "fx_direction_from_registry" in codes(parsed)
    assert (parsed.fx[0].base, parsed.fx[0].quote) == ("USD", "CAD")


def test_unregistered_series_is_reported_not_stored() -> None:
    details = {"FXCNYCAD": {"label": "CNY/CAD"}, "V39079": {"label": "Policy rate"}}
    parsed = bank_of_canada.parse_valet(
        valet([{"d": "2024-03-04", "FXCNYCAD": {"v": "0.19"}, "V39079": {"v": "5.00"}}], details),
        TODAY,
    )
    assert codes(parsed).count("unregistered_series") == 2
    assert parsed.fx == () and parsed.rates == ()


@pytest.mark.parametrize(
    ("obs", "code"),
    [
        ({"d": "04/03/2024", "FXUSDCAD": {"v": "1.35"}}, "invalid_date"),
        ({"d": "2024-02-30", "FXUSDCAD": {"v": "1.35"}}, "invalid_date"),
        ({"d": "2025-01-02", "FXUSDCAD": {"v": "1.35"}}, "future_date"),
        ({"d": "2024-03-04", "FXUSDCAD": {"v": "abc"}}, "invalid_number"),
        ({"d": "2024-03-04", "FXUSDCAD": {"v": "0"}}, "invalid_fx_rate"),
        ({"d": "2024-03-04", "FXUSDCAD": {"v": "-1.35"}}, "invalid_fx_rate"),
        ({"d": "2024-03-04", "FXUSDCAD": {"v": True}}, "invalid_number"),
        ({"d": "2024-03-04", "BD.CDN.10YR.DQ.YLD": {"v": "347"}}, "implausible_rate"),
        ("not an object", "invalid_observation"),
    ],
)
def test_valet_invalid_observations(obs: object, code: str) -> None:
    parsed = bank_of_canada.parse_valet(valet([obs]), TODAY)  # type: ignore[list-item]
    assert code in codes(parsed)
    assert parsed.fx == () and parsed.rates == ()


def test_valet_duplicate_and_conflicting_dates() -> None:
    a = {"d": "2024-03-04", "FXUSDCAD": {"v": "1.3562"}}
    b = {"d": "2024-03-04", "FXUSDCAD": {"v": "1.3563"}}
    assert len(bank_of_canada.parse_valet(valet([a, a]), TODAY).fx) == 1
    parsed = bank_of_canada.parse_valet(valet([a, b]), TODAY)
    assert parsed.fx == () and "conflict_in_payload" in codes(parsed)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not json", "not JSON"),
        (b"[]", "not a JSON object"),
        (b'{"message": "Series FXXXXCAD not found."}', "Series FXXXXCAD not found"),
        (b'{"seriesDetail": [], "observations": {}}', "must be an object"),
    ],
)
def test_valet_unusable_payloads(payload: bytes, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        bank_of_canada.parse_valet(payload, TODAY)


def test_valet_url_accepts_registered_series_only() -> None:
    url, params = bank_of_canada.observations_url(
        ["FXUSDCAD", "BD.CDN.10YR.DQ.YLD"], date(2024, 1, 1), date(2024, 3, 31)
    )
    assert url.endswith("/valet/observations/BD.CDN.10YR.DQ.YLD,FXUSDCAD/json")
    assert params == {"start_date": "2024-01-01", "end_date": "2024-03-31"}
    with pytest.raises(ValueError, match="registered"):
        bank_of_canada.observations_url(["FXCNYCAD"], date(2024, 1, 1), date(2024, 1, 2))
    with pytest.raises(ValueError, match="end before start"):
        bank_of_canada.observations_url(["FXUSDCAD"], date(2024, 1, 2), date(2024, 1, 1))
