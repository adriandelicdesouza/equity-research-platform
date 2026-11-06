"""Yahoo Finance chart adapter — unofficial endpoint (Blueprint §0.4, §4.2; D15, D16, D54).

Network access is **gated**: :func:`yahoo_client` refuses unless the ``yahoo_chart``
provider is enabled in ``source.providers`` (seeded disabled; enabling it is an owner
decision, P9). Nothing in this module enables it. Parsing works offline on archived or
fixture payloads only.

Payload shape (``/v8/finance/chart/{symbol}``; verified only to return 200 with CAD prices
and exchange "TOR" for RY.TO on 2026-11-06, §0.4)::

    {"chart": {"result": [{"meta": {...}, "timestamp": [...],
                           "events": {"splits": {...}, "dividends": {...}},
                           "indicators": {"quote": [{"open", "high", "low", "close",
                                                     "volume"}],
                                          "adjclose": [{"adjclose": [...]}]}}],
               "error": null}}

**Not verified, therefore not assumed** (D54): whether ``close`` is raw or split-adjusted
(``CloseBasis.UNVERIFIED``; :func:`erp_backend.market.adjust.infer_close_basis` decides it
from data around a split), how ``adjclose`` is computed (``AdjCloseBasis.UNVERIFIED``) and
the currency of dividends (taken from ``meta.currency`` and marked as assumed).

Trade dates: daily timestamps are UTC seconds; the trade date is the date at the
exchange's ``gmtoffset`` (fixed offset from the payload, so parsing never depends on the
host's time-zone database).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy.orm import Session

from erp_backend.ingestion.http import ProviderClient, ProviderNotConfiguredError
from erp_backend.market.model import (
    AdjCloseBasis,
    CashDividend,
    CloseBasis,
    InstrumentMeta,
    Issue,
    IssueLevel,
    ParseError,
    PriceBar,
    PriceHistory,
    Split,
    validate_bar,
    validate_currency,
)
from erp_backend.models import Provider

CODE = "yahoo_chart"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
RATE_PER_SECOND = 1.0  # source.providers seed (0003)
# Generic client identification: no personal contact data is sent to this provider.
USER_AGENT = "equity-research-platform/0.1 (+self-hosted research tool)"
# D15: provider symbol from ticker + exchange MIC.
MIC_SUFFIX = {"XTSE": ".TO", "XTSX": ".V", "XNYS": "", "XNAS": "", "XASE": "", "ARCX": ""}
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,19}$")
_RATIO = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)\s*$")


class ProviderNotEnabledError(ProviderNotConfiguredError):
    pass


def provider_symbol(ticker: str, exchange_mic: str | None) -> str:
    """Yahoo symbol for a listing (D15). Share-class dots become dashes (BRK.B → BRK-B)."""
    if exchange_mic not in MIC_SUFFIX:
        raise ValueError(f"No Yahoo symbol rule for exchange {exchange_mic!r}; enter it manually")
    base = ticker.strip().upper().replace(".", "-")
    symbol = base + MIC_SUFFIX[exchange_mic]
    if not _SYMBOL.match(symbol):
        raise ValueError(f"Invalid ticker {ticker!r}")
    return symbol


class YahooChart:
    code = CODE
    close_basis = CloseBasis.UNVERIFIED
    adj_close_basis = AdjCloseBasis.UNVERIFIED

    def history_url(self, symbol: str, start: date, end: date) -> tuple[str, dict[str, Any]]:
        if not _SYMBOL.match(symbol):
            raise ValueError(f"Invalid provider symbol {symbol!r}")
        if end < start:
            raise ValueError("end before start")
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        period1 = int(
            (datetime(start.year, start.month, start.day, tzinfo=UTC) - epoch).total_seconds()
        )
        period2 = (
            int((datetime(end.year, end.month, end.day, tzinfo=UTC) - epoch).total_seconds())
            + 86400
        )
        params = {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
        return BASE + symbol, params

    def parse_history(self, payload: bytes) -> PriceHistory:
        return parse_chart(payload)


def yahoo_client(db: Session, transport: httpx.BaseTransport | None = None) -> ProviderClient:
    """The only way to obtain a Yahoo client; refuses unless the owner enabled the provider."""
    row = db.get(Provider, CODE)
    if row is None or not row.enabled:
        raise ProviderNotEnabledError(
            "The yahoo_chart provider is disabled. Live requests need the owner's approval "
            "(P9); enable it in source.providers only after that approval."
        )
    return ProviderClient(
        provider_code=CODE, user_agent=USER_AGENT, per_second=RATE_PER_SECOND, transport=transport
    )


# ---- Parsing ---------------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ParseError(f"Boolean where a number was expected: {value!r}")
    try:
        result = Decimal(str(value)) if not isinstance(value, Decimal) else value
    except InvalidOperation as exc:
        raise ParseError(f"Not a number: {value!r}") from exc
    if not result.is_finite():
        return None  # NaN/inf are treated as missing
    return result


def _day(ts: Any, offset: int) -> date:
    if not isinstance(ts, (int, Decimal)) or isinstance(ts, bool):
        raise ParseError(f"Invalid timestamp {ts!r}")
    return (datetime.fromtimestamp(int(ts), tz=UTC) + timedelta(seconds=offset)).date()


def _series(block: Any, name: str, n: int) -> list[Any]:
    values = (block or {}).get(name)
    if values is None:
        return [None] * n
    if not isinstance(values, list) or len(values) != n:
        raise ParseError(
            f"Series {name!r} has {len(values) if isinstance(values, list) else '?'} "
            f"values for {n} timestamps"
        )
    return values


def _meta(meta: dict[str, Any]) -> InstrumentMeta:
    symbol = meta.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ParseError("meta.symbol missing")
    offset = int(meta.get("gmtoffset") or 0)
    first = meta.get("firstTradeDate")
    quote_time = meta.get("regularMarketTime")
    keep = (
        "currency",
        "exchangeName",
        "fullExchangeName",
        "instrumentType",
        "exchangeTimezoneName",
        "timezone",
        "gmtoffset",
        "priceHint",
        "dataGranularity",
    )
    return InstrumentMeta(
        provider_symbol=symbol,
        currency=validate_currency(meta.get("currency")),
        exchange_code=meta.get("exchangeName"),
        exchange_name=meta.get("fullExchangeName"),
        exchange_timezone=meta.get("exchangeTimezoneName"),
        instrument_type=meta.get("instrumentType"),
        first_trade_date=_day(first, offset) if first is not None else None,
        quote_price=_decimal(meta.get("regularMarketPrice")),
        quote_time=datetime.fromtimestamp(int(quote_time), tz=UTC) if quote_time else None,
        raw={k: meta[k] for k in keep if k in meta},
    )


def _split(raw: dict[str, Any], offset: int, issues: list[Issue]) -> Split | None:
    day = _day(raw.get("date"), offset)
    num, den = _decimal(raw.get("numerator")), _decimal(raw.get("denominator"))
    text = raw.get("splitRatio")
    match = _RATIO.match(text) if isinstance(text, str) else None
    if num is None or den is None or num <= 0 or den <= 0:
        issues.append(Issue(IssueLevel.ERROR, "invalid_split", f"split {raw!r}", day))
        return None
    if match and (Decimal(match.group(1)), Decimal(match.group(2))) != (num, den):
        issues.append(
            Issue(
                IssueLevel.ERROR,
                "split_ratio_mismatch",
                f"numerator/denominator {num}/{den} vs splitRatio {text!r}",
                day,
            )
        )
        return None
    return Split(day, num, den)


def parse_chart(payload: bytes) -> PriceHistory:
    """Parse a chart payload. Bars with a missing close are skipped (reported as warnings);
    invalid bars are rejected (errors); the payload as a whole is never partially trusted
    when its structure is wrong (:class:`ParseError`)."""
    try:
        doc = json.loads(payload, parse_float=Decimal, parse_int=int)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseError(f"Invalid JSON: {exc}") from exc
    chart = doc.get("chart") if isinstance(doc, dict) else None
    if not isinstance(chart, dict):
        raise ParseError("Missing 'chart' object")
    if chart.get("error"):
        err = chart["error"]
        raise ParseError(f"Provider error: {err.get('code')}: {err.get('description')}")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ParseError("Expected exactly one chart result")
    result = results[0]
    meta = _meta(result.get("meta") or {})
    offset = int(meta.raw.get("gmtoffset") or 0)
    stamps = result.get("timestamp") or []
    if not isinstance(stamps, list):
        raise ParseError("timestamp is not a list")
    n = len(stamps)
    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or [{}]
    adj = (indicators.get("adjclose") or [{}])[0]
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise ParseError("Expected exactly one quote block")
    q = quotes[0]
    cols = {k: _series(q, k, n) for k in ("open", "high", "low", "close", "volume")}
    adjclose = _series(adj, "adjclose", n)

    issues: list[Issue] = []
    bars: dict[date, PriceBar] = {}
    for i, ts in enumerate(stamps):
        day = _day(ts, offset)
        close = _decimal(cols["close"][i])
        if close is None:
            issues.append(Issue(IssueLevel.WARNING, "missing_close", "no close; bar skipped", day))
            continue
        volume = cols["volume"][i]
        if volume is not None and (not isinstance(volume, int) or isinstance(volume, bool)):
            issues.append(Issue(IssueLevel.ERROR, "invalid_volume", f"volume {volume!r}", day))
            continue
        bar = PriceBar(
            day,
            _decimal(cols["open"][i]),
            _decimal(cols["high"][i]),
            _decimal(cols["low"][i]),
            close,
            _decimal(adjclose[i]),
            volume,
        )
        problems = validate_bar(bar)
        issues.extend(problems)
        if any(p.level is IssueLevel.ERROR for p in problems):
            continue
        if day in bars:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "duplicate_date",
                    "two bars for one trade date; both rejected",
                    day,
                )
            )
            bars.pop(day)
            continue
        bars[day] = bar

    events = result.get("events") or {}
    splits: list[Split] = []
    for raw in (events.get("splits") or {}).values():
        if (s := _split(raw, offset, issues)) is not None:
            splits.append(s)
    dividends: list[CashDividend] = []
    for raw in (events.get("dividends") or {}).values():
        day = _day(raw.get("date"), offset)
        amount = _decimal(raw.get("amount"))
        if amount is None or amount <= 0:
            issues.append(Issue(IssueLevel.ERROR, "invalid_dividend", f"dividend {raw!r}", day))
            continue
        if meta.currency is None:
            issues.append(
                Issue(IssueLevel.ERROR, "dividend_without_currency", "no currency in meta", day)
            )
            continue
        dividends.append(CashDividend(day, amount, meta.currency, "assumed_quote_currency"))
    if len({s.ex_date for s in splits}) != len(splits):
        raise ParseError("Two splits on one ex-date")
    return PriceHistory(
        provider_code=CODE,
        meta=meta,
        close_basis=YahooChart.close_basis,
        adj_close_basis=YahooChart.adj_close_basis,
        bars=tuple(sorted(bars.values(), key=lambda b: b.trade_date)),
        splits=tuple(sorted(splits, key=lambda s: s.ex_date)),
        dividends=tuple(sorted(dividends, key=lambda d: (d.ex_date, d.amount))),
        issues=tuple(sorted(issues, key=lambda i: (i.trade_date or date.min, i.code))),
    )
