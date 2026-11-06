"""SYNTHETIC Yahoo chart payloads for offline tests (no data was downloaded).

The shape follows the adapter's documented payload structure; every number is invented.
Nothing here is evidence about Yahoo's real field semantics (e.g. whether ``close`` is
raw or split-adjusted), which remains unverified (D54).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

ET_OFFSET = -18000  # seconds; standard-time Eastern offset as carried in meta.gmtoffset


def ts(day: date, hour_utc: int = 14, minute: int = 30) -> int:
    """Daily bar timestamp: 09:30 Eastern (= 14:30 UTC in standard time)."""
    return int(datetime(day.year, day.month, day.day, hour_utc, minute, tzinfo=UTC).timestamp())


def meta(symbol: str = "RY.TO", currency: str | None = "CAD", **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "currency": currency,
        "symbol": symbol,
        "exchangeName": "TOR",
        "fullExchangeName": "Toronto",
        "instrumentType": "EQUITY",
        "firstTradeDate": ts(date(1995, 1, 3)),
        "regularMarketTime": ts(date(2024, 3, 8), 21, 0),
        "gmtoffset": ET_OFFSET,
        "timezone": "EST",
        "exchangeTimezoneName": "America/Toronto",
        "regularMarketPrice": 135.21,
        "priceHint": 2,
        "dataGranularity": "1d",
    }
    out.update(extra)
    return out


def chart(
    days: list[date],
    closes: list[Any],
    *,
    adj: list[Any] | None = None,
    opens: list[Any] | None = None,
    highs: list[Any] | None = None,
    lows: list[Any] | None = None,
    volumes: list[Any] | None = None,
    splits: list[tuple[date, int, int]] | None = None,
    dividends: list[tuple[date, Any]] | None = None,
    meta_overrides: dict[str, Any] | None = None,
    stamps: list[int] | None = None,
) -> bytes:
    n = len(days)
    quote = {
        "open": opens if opens is not None else closes,
        "high": highs if highs is not None else [c if c is None else c + 1 for c in closes],
        "low": lows if lows is not None else [c if c is None else c - 1 for c in closes],
        "close": closes,
        "volume": volumes if volumes is not None else [1000 + i for i in range(n)],
    }
    events: dict[str, Any] = {}
    if splits:
        events["splits"] = {
            str(ts(d)): {"date": ts(d), "numerator": a, "denominator": b, "splitRatio": f"{a}:{b}"}
            for d, a, b in splits
        }
    if dividends:
        events["dividends"] = {str(ts(d)): {"amount": amt, "date": ts(d)} for d, amt in dividends}
    result = {
        "meta": meta(**(meta_overrides or {})),
        "timestamp": stamps if stamps is not None else [ts(d) for d in days],
        "events": events,
        "indicators": {
            "quote": [quote],
            "adjclose": [{"adjclose": adj if adj is not None else closes}],
        },
    }
    return json.dumps({"chart": {"result": [result], "error": None}}).encode()


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def error(code: str = "Not Found", description: str = "No data found") -> bytes:
    return json.dumps(
        {"chart": {"result": None, "error": {"code": code, "description": description}}}
    ).encode()
