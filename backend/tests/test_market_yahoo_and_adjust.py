"""Yahoo adapter parsing and provider-neutral adjustments — offline, synthetic data only."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from erp_backend.market.adjust import (
    adjust_per_share,
    adjust_shares,
    infer_close_basis,
    latest_close,
    recompute_adjusted_close,
    split_factor,
)
from erp_backend.market.model import (
    AdjCloseBasis,
    CashDividend,
    CloseBasis,
    IssueLevel,
    MarketDataProvider,
    ParseError,
    PriceBar,
    Split,
)
from erp_backend.market.yahoo import YahooChart, parse_chart, provider_symbol

from . import yahoo_payloads as y

D = date


def codes(history) -> list[str]:  # type: ignore[no-untyped-def]
    return [i.code for i in history.issues]


# ---- Symbols and requests (no network) -------------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "mic", "symbol"),
    [("RY", "XTSE", "RY.TO"), ("ABC", "XTSX", "ABC.V"), ("aapl", "XNAS", "AAPL"),
     ("BRK.B", "XNYS", "BRK-B"), ("RCI.B", "XTSE", "RCI-B.TO")],
)  # fmt: skip
def test_provider_symbol_rule(ticker: str, mic: str, symbol: str) -> None:
    assert provider_symbol(ticker, mic) == symbol


def test_provider_symbol_refuses_unknown_exchanges() -> None:
    with pytest.raises(ValueError, match="enter it manually"):
        provider_symbol("VOD", "XLON")


def test_history_request_is_built_not_sent() -> None:
    provider: MarketDataProvider = YahooChart()
    url, params = provider.history_url("RY.TO", D(2024, 1, 2), D(2024, 1, 31))
    assert url == "https://query1.finance.yahoo.com/v8/finance/chart/RY.TO"
    assert params["period1"] == 1704153600 and params["period2"] == 1706745600
    assert (params["interval"], params["events"], params["includeAdjustedClose"]) == (
        "1d", "div,splits", "true",
    )  # fmt: skip
    assert (provider.close_basis, provider.adj_close_basis) == (
        CloseBasis.UNVERIFIED,
        AdjCloseBasis.UNVERIFIED,
    )
    with pytest.raises(ValueError):
        provider.history_url("RY.TO; DROP", D(2024, 1, 2), D(2024, 1, 3))


# ---- Parsing ---------------------------------------------------------------------------------


def test_parse_keeps_raw_and_adjusted_close_separate() -> None:
    days = y.weekdays(D(2024, 3, 4), 3)
    payload = y.chart(
        days, [100.5, 101.25, 99.75], adj=[98.1, None, 97.4],
        splits=[(days[1], 2, 1)], dividends=[(days[2], 1.38)],
    )  # fmt: skip
    h = parse_chart(payload)
    assert [b.trade_date for b in h.bars] == days
    bar = h.bars[0]
    assert (bar.close, bar.adj_close, bar.volume) == (Decimal("100.5"), Decimal("98.1"), 1000)
    assert (bar.open, bar.high, bar.low) == (Decimal("100.5"), Decimal("101.5"), Decimal("99.5"))
    assert h.bars[1].adj_close is None  # missing adjusted close is not filled from close
    assert h.splits == (Split(days[1], Decimal(2), Decimal(1)),)
    assert h.dividends == (CashDividend(days[2], Decimal("1.38"), "CAD", "assumed_quote_currency"),)
    assert (h.close_basis, h.adj_close_basis) == (CloseBasis.UNVERIFIED, AdjCloseBasis.UNVERIFIED)
    assert h.issues == ()


def test_metadata_and_quote_are_kept_apart_from_closes() -> None:
    h = parse_chart(y.chart([D(2024, 3, 8)], [135.0]))
    m = h.meta
    assert (m.provider_symbol, m.currency, m.exchange_code, m.exchange_name) == (
        "RY.TO", "CAD", "TOR", "Toronto",
    )  # fmt: skip
    assert (m.exchange_timezone, m.instrument_type, m.first_trade_date) == (
        "America/Toronto", "EQUITY", D(1995, 1, 3),
    )  # fmt: skip
    assert m.quote_price == Decimal("135.21") and m.quote_time is not None
    assert h.bars[0].close == Decimal("135.0")  # the quote never replaces a close
    assert m.raw["gmtoffset"] == y.ET_OFFSET


def test_trade_date_uses_the_exchange_offset() -> None:
    # 02:00 UTC on Mar 9 is 21:00 on Mar 8 in New York (offset −5 h).
    h = parse_chart(y.chart([D(2024, 3, 9)], [10.0], stamps=[y.ts(D(2024, 3, 9), 2, 0)]))
    assert h.bars[0].trade_date == D(2024, 3, 8)


def test_missing_and_invalid_observations() -> None:
    days = y.weekdays(D(2024, 3, 4), 6)
    payload = y.chart(
        days,
        [10.0, None, 12.0, -1.0, 14.0, 15.0],
        highs=[11.0, None, 13.0, 1.0, 13.0, 16.0],
        lows=[9.0, None, 11.0, -2.0, 15.0, 14.0],
        volumes=[1, 2, -3, 4, 5, None],
    )
    h = parse_chart(payload)
    assert [b.trade_date for b in h.bars] == [days[0], days[5]]
    assert h.bars[1].volume is None
    assert sorted(codes(h)) == sorted(
        # The bad bar's open, low, close and adjusted close are all non-positive.
        ["missing_close", "negative_volume", "non_positive_price", "non_positive_price",
         "non_positive_price", "non_positive_price", "high_below_low"]
    )  # fmt: skip
    assert all(i.level is IssueLevel.ERROR for i in h.errors)


def test_close_outside_range_is_kept_with_a_warning() -> None:
    h = parse_chart(y.chart([D(2024, 3, 4)], [20.0], highs=[19.0], lows=[18.0]))
    assert len(h.bars) == 1 and codes(h) == ["close_outside_range"]
    assert h.issues[0].level is IssueLevel.WARNING


def test_duplicate_trade_dates_are_rejected_not_merged() -> None:
    day = D(2024, 3, 4)
    h = parse_chart(y.chart([day, day], [10.0, 10.5], stamps=[y.ts(day), y.ts(day, 15, 0)]))
    assert h.bars == () and codes(h) == ["duplicate_date"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"<html>", "Invalid JSON"),
        (b"{}", "Missing 'chart'"),
        (y.error(), "Provider error: Not Found"),
        (json.dumps({"chart": {"result": [], "error": None}}).encode(), "exactly one"),
        (y.chart([D(2024, 3, 4), D(2024, 3, 5)], [1.0, 2.0], adj=[1.0]), "adjclose"),
        (y.chart([D(2024, 3, 4)], [1.0], meta_overrides={"currency": "GBp"}), "currency"),
        (y.chart([D(2024, 3, 4)], [1.0], meta_overrides={"symbol": None}), "symbol"),
    ],
)
def test_malformed_payloads_fail_as_a_whole(payload: bytes, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse_chart(payload)


def test_invalid_events() -> None:
    day = D(2024, 3, 4)
    doc = json.loads(y.chart([day], [10.0], splits=[(day, 4, 1)], dividends=[(day, -0.5)]))
    split = next(iter(doc["chart"]["result"][0]["events"]["splits"].values()))
    split["splitRatio"] = "3:1"
    h = parse_chart(json.dumps(doc).encode())
    assert h.splits == () and h.dividends == ()
    assert sorted(codes(h)) == ["invalid_dividend", "split_ratio_mismatch"]
    no_ccy = parse_chart(y.chart([day], [10.0], dividends=[(day, 0.5)],
                                 meta_overrides={"currency": None}))  # fmt: skip
    assert codes(no_ccy) == ["dividend_without_currency"]


def test_parsing_is_deterministic() -> None:
    days = y.weekdays(D(2024, 3, 4), 4)
    a = y.chart(days, [1.0, 2.0, 3.0, 4.0], dividends=[(days[3], 0.1), (days[1], 0.2)])
    doc = json.loads(a)
    events = doc["chart"]["result"][0]["events"]["dividends"]
    doc["chart"]["result"][0]["events"]["dividends"] = dict(reversed(list(events.items())))
    assert parse_chart(a) == parse_chart(json.dumps(doc).encode())


# ---- Splits and share counts -----------------------------------------------------------------

# Apple's publicly announced splits (7-for-1 effective 2014-06-09, 4-for-1 effective
# 2020-08-31), used as a TEST FIXTURE. They are not provider data and resolve nothing in the
# stored datasets until corporate actions are retrieved and independently validated.
AAPL_SPLITS = (Split(D(2014, 6, 9), Decimal(7), Decimal(1)),
               Split(D(2020, 8, 31), Decimal(4), Decimal(1)))  # fmt: skip


def test_split_factors_compose_chronologically_and_in_both_directions() -> None:
    assert split_factor(AAPL_SPLITS, D(2013, 1, 24), D(2024, 1, 1)) == 28
    assert split_factor(AAPL_SPLITS, D(2013, 1, 24), D(2015, 1, 1)) == 7
    assert split_factor(AAPL_SPLITS, D(2024, 1, 1), D(2013, 1, 24)) == Decimal(1) / 28
    assert split_factor(reversed(AAPL_SPLITS), D(2013, 1, 24), D(2024, 1, 1)) == 28
    # A filing issued on the ex-date already reflects the split (basis_date < ex_date).
    assert split_factor(AAPL_SPLITS, D(2014, 6, 9), D(2015, 1, 1)) == 1
    reverse = Split(D(2022, 5, 2), Decimal(1), Decimal(10))
    assert split_factor([reverse], D(2022, 1, 1), D(2023, 1, 1)) == Decimal("0.1")


def test_reported_share_counts_are_restated_not_modified() -> None:
    shares = Decimal("938916000")
    a = adjust_shares(shares, D(2013, 1, 24), D(2024, 1, 1), AAPL_SPLITS)
    assert (a.reported, a.adjusted, a.factor) == (shares, shares * 28, 28)
    assert a.splits_applied == AAPL_SPLITS and a.changed and a.kind == "shares"
    eps = adjust_per_share(Decimal("13.81"), D(2013, 1, 24), D(2015, 1, 1), AAPL_SPLITS)
    assert eps.adjusted == Decimal("13.81") / 7 and eps.reported == Decimal("13.81")


@pytest.mark.parametrize(
    ("label", "eps", "eps_basis", "shares", "shares_basis", "net_income"),
    [
        # Values as found by the EPS checks on the stored AAPL facts (KNOWN_LIMITATIONS §4f):
        # EPS from a later (split-restated) filing, shares from the original 10-Q.
        ("Q1 FY2013", "1.99", D(2014, 10, 27), "938916000", D(2013, 1, 24), "13078000000"),
        ("Q1 FY2019", "1.05", D(2020, 10, 30), "4735820000", D(2019, 1, 30), "19965000000"),
    ],
)
def test_apple_eps_mismatch_regression(
    label: str, eps: str, eps_basis: date, shares: str, shares_basis: date, net_income: str
) -> None:
    """Restating shares to the EPS filing's basis closes the gap; unadjusted it does not.

    Fixture-level regression only: the dataset EPS checks are unchanged until split data is
    retrieved from a provider and validated (D54)."""
    ni, e = Decimal(net_income), Decimal(eps)
    tolerance = Decimal("0.01") * ni
    assert abs(e * Decimal(shares) - ni) > tolerance, f"{label}: unadjusted should not tie"
    adjusted = adjust_shares(Decimal(shares), shares_basis, eps_basis, AAPL_SPLITS)
    assert abs(e * adjusted.adjusted - ni) <= tolerance, f"{label}: adjusted should tie"


# ---- Close basis, adjusted close, dated price ------------------------------------------------


def bars(closes: list[str], adj: list[str | None] | None = None) -> list[PriceBar]:
    days = y.weekdays(D(2024, 1, 2), len(closes))
    adj = adj or [None] * len(closes)
    return [PriceBar(d, None, None, None, Decimal(c), Decimal(a) if a else None, 100)
            for d, c, a in zip(days, closes, adj, strict=True)]  # fmt: skip


def test_close_basis_is_inferred_from_the_split_day_move() -> None:
    raw = bars(["400", "404", "101", "102"])  # 4-for-1 on day 3: price drops ~4x
    split = Split(raw[2].trade_date, Decimal(4), Decimal(1))
    basis, evidence = infer_close_basis(raw, [split])
    assert basis is CloseBasis.RAW and evidence[0].reading == "raw"
    adjusted = bars(["100", "101", "101", "102"])
    assert infer_close_basis(adjusted, [split])[0] is CloseBasis.SPLIT_ADJUSTED
    # A 1.67x move on a 4-for-1 split day matches neither reading.
    assert infer_close_basis(bars(["100", "100", "60"]), [split])[0] is CloseBasis.UNVERIFIED
    tiny = Split(raw[2].trade_date, Decimal(11), Decimal(10))  # 10% stock split: uninformative
    assert infer_close_basis(raw, [tiny]) == (CloseBasis.UNVERIFIED, ())


def test_recomputed_adjusted_close_and_mismatch_flags() -> None:
    series = bars(["100", "102", "51", "50"])
    split = Split(series[2].trade_date, Decimal(2), Decimal(1))
    dividend = CashDividend(series[3].trade_date, Decimal("1.02"), "USD", "provider")
    result = recompute_adjusted_close(series, [split], [dividend], CloseBasis.RAW)
    # Dividend factor 1 − 1.02/51 = 0.98 before day 4; split ÷2 before day 3.
    assert [r.ours for r in result] == [Decimal("49.00000000"), Decimal("49.98000000"),
                                        Decimal("49.98000000"), Decimal("50.00000000")]  # fmt: skip
    provider = bars(["100", "102", "51", "50"], adj=["49.01", "50.5", "49.98", "50"])
    flagged = recompute_adjusted_close(provider, [split], [dividend], CloseBasis.RAW)
    assert [r.flagged for r in flagged] == [False, True, False, False]
    with pytest.raises(ValueError, match="Close basis"):
        recompute_adjusted_close(series, [split], [dividend], CloseBasis.UNVERIFIED)


def test_latest_close_is_dated_and_never_a_quote() -> None:
    series = bars(["10", "11", "12"])
    got = latest_close(series, series[1].trade_date, CloseBasis.UNVERIFIED)
    assert got is not None and (got.trade_date, got.close) == (series[1].trade_date, Decimal("11"))
    assert latest_close(series, D(2000, 1, 1), CloseBasis.RAW) is None
