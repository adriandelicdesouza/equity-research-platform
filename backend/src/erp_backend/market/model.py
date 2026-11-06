"""Normalized market-data records and the provider contract (Blueprint §4.1, §4.5; D54).

Provider adapters turn archived raw payloads into these records; nothing downstream sees a
provider's field names. Prices are ``Decimal`` (exact, as delivered), never float.

Price semantics are explicit and never substituted for each other:

* ``close`` is the provider's close as delivered, tagged with its :class:`CloseBasis`.
  ``raw`` = as traded; ``split_adjusted`` = restated for later splits but not dividends;
  ``unverified`` = the provider does not document it and it has not been checked.
* ``adj_close`` is the provider's adjusted close (``AdjCloseBasis``), stored separately.

Corporate actions: a split is ``numerator`` new shares for ``denominator`` old shares
(4-for-1 → 4/1; a 1-for-10 reverse split → 1/10), effective on its ex-date. A cash
dividend is an amount per share in a currency, on its ex-date.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

ISO_CURRENCY_LEN = 3


class CloseBasis(StrEnum):
    RAW = "raw"
    SPLIT_ADJUSTED = "split_adjusted"
    UNVERIFIED = "unverified"


class AdjCloseBasis(StrEnum):
    SPLIT_AND_DIVIDEND_ADJUSTED = "split_and_dividend_adjusted"
    UNVERIFIED = "unverified"


class IssueLevel(StrEnum):
    ERROR = "error"  # the observation was rejected
    WARNING = "warning"  # kept, but suspicious


@dataclass(frozen=True)
class Issue:
    level: IssueLevel
    code: str
    message: str
    trade_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "code": self.code,
            "message": self.message,
            "date": self.trade_date.isoformat() if self.trade_date else None,
        }


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    adj_close: Decimal | None
    volume: int | None


@dataclass(frozen=True)
class Split:
    ex_date: date
    numerator: Decimal
    denominator: Decimal

    @property
    def factor(self) -> Decimal:
        """New shares per old share (4-for-1 → 4; 1-for-10 reverse → 0.1)."""
        return self.numerator / self.denominator


@dataclass(frozen=True)
class CashDividend:
    ex_date: date
    amount: Decimal
    currency: str
    currency_source: str  # "provider" or "assumed_quote_currency"


@dataclass(frozen=True)
class InstrumentMeta:
    provider_symbol: str
    currency: str | None
    exchange_code: str | None
    exchange_name: str | None
    exchange_timezone: str | None
    instrument_type: str | None
    first_trade_date: date | None
    # Provider's latest quote: a point-in-time indication, never used as a dated close.
    quote_price: Decimal | None = None
    quote_time: datetime | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)  # provider fields kept for audit


@dataclass(frozen=True)
class PriceHistory:
    """A parsed provider response: everything normalized, plus what was rejected and why."""

    provider_code: str
    meta: InstrumentMeta
    close_basis: CloseBasis
    adj_close_basis: AdjCloseBasis
    bars: tuple[PriceBar, ...]
    splits: tuple[Split, ...]
    dividends: tuple[CashDividend, ...]
    issues: tuple[Issue, ...] = ()

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.level is IssueLevel.ERROR)


class ParseError(ValueError):
    """The payload as a whole is unusable (malformed, provider error, wrong shape)."""


class MarketDataProvider(Protocol):
    """Provider contract (Blueprint §4.1). Fetching and parsing are separate: every fetch is
    archived before parsing, so parsing can be re-run on historical payloads."""

    code: str
    close_basis: CloseBasis
    adj_close_basis: AdjCloseBasis

    def history_url(self, symbol: str, start: date, end: date) -> tuple[str, dict[str, Any]]: ...

    def parse_history(self, payload: bytes) -> PriceHistory: ...


def validate_bar(bar: PriceBar) -> list[Issue]:
    """Provider-neutral bar validation: hard errors reject the bar, warnings keep it."""
    out: list[Issue] = []
    prices = {
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "adj_close": bar.adj_close,
    }
    for name, value in prices.items():
        if value is not None and value <= 0:
            out.append(
                Issue(IssueLevel.ERROR, "non_positive_price", f"{name} = {value}", bar.trade_date)
            )
    if bar.volume is not None and bar.volume < 0:
        out.append(
            Issue(IssueLevel.ERROR, "negative_volume", f"volume = {bar.volume}", bar.trade_date)
        )
    if bar.high is not None and bar.low is not None and bar.high < bar.low:
        out.append(
            Issue(
                IssueLevel.ERROR,
                "high_below_low",
                f"high {bar.high} < low {bar.low}",
                bar.trade_date,
            )
        )
    elif bar.high is not None and bar.low is not None and not bar.low <= bar.close <= bar.high:
        out.append(
            Issue(
                IssueLevel.WARNING,
                "close_outside_range",
                f"close {bar.close} outside [{bar.low}, {bar.high}]",
                bar.trade_date,
            )
        )
    return out


def validate_currency(code: str | None) -> str | None:
    if code is None:
        return None
    text = code.strip()
    # Some providers quote in minor units (e.g. "GBp" pence); these are not ISO codes.
    if len(text) != ISO_CURRENCY_LEN or not text.isalpha() or not text.isupper():
        raise ParseError(f"Unsupported currency code {code!r} (ISO 4217 major unit expected)")
    return text
