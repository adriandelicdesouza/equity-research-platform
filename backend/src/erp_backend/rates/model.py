"""Normalized rate and FX observations, and the series registry (Blueprint §3.3, §7.5.1; D58).

Rate-series identity is explicit and never collapsed: a series is one provider series with
one rate type, currency, tenor and quotation convention. A US Treasury 10-year **par
yield** (``UST_PAR_10Y``) and a Government of Canada 10-year **benchmark bond yield**
(``GOC_BMK_10Y``) are different series even though both are "10-year rates"; other
Treasury rate families (real yields, bill discount rates) would be further series, never
aliases of these.

Units: rates are stored **in percentage points exactly as published** (``4.84`` means
4.84 %); conversion to a decimal fraction belongs to the valuation input boundary, not to
storage. FX rates are stored as **units of ``quote`` currency per one unit of ``base``**
(``base=USD, quote=CAD, rate=1.3650`` means 1 USD = 1.3650 CAD), in the direction the
provider publishes, and are never inverted or cross-calculated here.

Missing observations (blank, ``N/A``, absent) are reported and **not stored**; nothing is
interpolated, carried forward or substituted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from erp_backend.market.model import Issue, IssueLevel, ParseError

__all__ = [
    "FX_SERIES",
    "RATE_SERIES",
    "FxObservation",
    "FxSeriesSpec",
    "Issue",
    "IssueLevel",
    "ParseError",
    "ParsedSeries",
    "RateObservation",
    "RateSeriesSpec",
    "RateType",
]

RATE_UNIT = "percent"  # percentage points, as published
# Plausibility bounds for a published yield, in percentage points. Outside the hard bounds
# the value is rejected (a unit or parsing error, not a market level); outside the soft
# bounds it is kept with a warning.
RATE_HARD_BOUNDS = (Decimal("-10"), Decimal("50"))
RATE_SOFT_BOUNDS = (Decimal("0"), Decimal("20"))

_PLAIN_DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")


class RateType(StrEnum):
    PAR_YIELD = "par_yield"  # US Treasury par yield curve (CMT), bond-equivalent basis
    BENCHMARK_BOND_YIELD = "benchmark_bond_yield"  # GoC benchmark bond yield


@dataclass(frozen=True)
class RateSeriesSpec:
    code: str  # platform series code (market.rate_series.code)
    provider_code: str
    provider_series_id: str  # the provider's own identifier (column header / series name)
    currency: str
    rate_type: RateType
    tenor: str  # "1M", "10Y", "LONG"
    tenor_months: int | None  # None when the provider does not define a fixed maturity
    quote_convention: str
    description: str


@dataclass(frozen=True)
class FxSeriesSpec:
    provider_code: str
    provider_series_id: str
    base: str  # one unit of base ...
    quote: str  # ... is worth `rate` units of quote
    expected_label: str  # provider's label stating the direction, e.g. "USD/CAD"
    description: str


_UST_CONVENTION = (
    "Par yield (constant maturity), bond-equivalent basis, semi-annual compounding; "
    "percent per annum as published by the US Treasury"
)
_GOC_CONVENTION = (
    "Benchmark bond yield, Government of Canada, as published by the Bank of Canada "
    "(Valet); percent per annum"
)


def _ust(header: str, tenor: str, months: int) -> RateSeriesSpec:
    return RateSeriesSpec(
        f"UST_PAR_{tenor}",
        "us_treasury",
        header,
        "USD",
        RateType.PAR_YIELD,
        tenor,
        months,
        _UST_CONVENTION,
        f"US Treasury par yield curve rate, {tenor}",
    )


def _goc(series_id: str, tenor: str, months: int | None, label: str) -> RateSeriesSpec:
    return RateSeriesSpec(
        f"GOC_BMK_{tenor}",
        "bank_of_canada",
        series_id,
        "CAD",
        RateType.BENCHMARK_BOND_YIELD,
        tenor,
        months,
        _GOC_CONVENTION,
        f"Government of Canada benchmark bond yield, {label}",
    )


# Registry (seeded into market.rate_series by migration 0014; the two must agree, which a
# test checks). Only series the Blueprint needs (§0.4, §4.2, §7.5.1) and their curve
# neighbours from the same provider table are registered; anything else in a payload is
# reported as unregistered and not stored.
RATE_SERIES: tuple[RateSeriesSpec, ...] = (
    _ust("1 Mo", "1M", 1),
    _ust("2 Mo", "2M", 2),
    _ust("3 Mo", "3M", 3),
    _ust("4 Mo", "4M", 4),
    _ust("6 Mo", "6M", 6),
    _ust("1 Yr", "1Y", 12),
    _ust("2 Yr", "2Y", 24),
    _ust("3 Yr", "3Y", 36),
    _ust("5 Yr", "5Y", 60),
    _ust("7 Yr", "7Y", 84),
    _ust("10 Yr", "10Y", 120),
    _ust("20 Yr", "20Y", 240),
    _ust("30 Yr", "30Y", 360),
    _goc("BD.CDN.2YR.DQ.YLD", "2Y", 24, "2 year"),
    _goc("BD.CDN.3YR.DQ.YLD", "3Y", 36, "3 year"),
    _goc("BD.CDN.5YR.DQ.YLD", "5Y", 60, "5 year"),
    _goc("BD.CDN.7YR.DQ.YLD", "7Y", 84, "7 year"),
    _goc("BD.CDN.10YR.DQ.YLD", "10Y", 120, "10 year"),
    _goc("BD.CDN.LONG.DQ.YLD", "LONG", None, "long-term (no fixed maturity)"),
)

# Bank of Canada daily exchange rates: the series name FX<base><quote> is CAD per unit of
# the foreign currency; the label "<base>/<quote>" in the payload is checked against this.
FX_SERIES: tuple[FxSeriesSpec, ...] = tuple(
    FxSeriesSpec(
        "bank_of_canada",
        f"FX{ccy}CAD",
        ccy,
        "CAD",
        f"{ccy}/CAD",
        f"{name} to Canadian dollar daily exchange rate (CAD per 1 {ccy})",
    )
    for ccy, name in (("USD", "US dollar"), ("EUR", "Euro"), ("GBP", "UK pound sterling"))
)


def rate_series_for(provider_code: str) -> dict[str, RateSeriesSpec]:
    """Provider series id → spec, for one provider."""
    return {s.provider_series_id: s for s in RATE_SERIES if s.provider_code == provider_code}


def fx_series_for(provider_code: str) -> dict[str, FxSeriesSpec]:
    return {s.provider_series_id: s for s in FX_SERIES if s.provider_code == provider_code}


@dataclass(frozen=True)
class RateObservation:
    series_code: str
    obs_date: date
    value: Decimal  # percentage points


@dataclass(frozen=True)
class FxObservation:
    provider_series_id: str
    base: str
    quote: str
    obs_date: date
    rate: Decimal  # quote per 1 base


@dataclass(frozen=True)
class ParsedSeries:
    """One parsed provider payload: valid observations plus every issue found.

    ``missing`` counts observations the provider published as absent (never stored).
    Observations involved in an in-payload conflict are excluded from ``rates``/``fx``."""

    provider_code: str
    rates: tuple[RateObservation, ...]
    fx: tuple[FxObservation, ...]
    issues: tuple[Issue, ...]
    missing: int
    series_seen: tuple[str, ...]

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.level is IssueLevel.ERROR)

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider_code,
            "rate_observations": len(self.rates),
            "fx_observations": len(self.fx),
            "missing": self.missing,
            "errors": len(self.errors),
            "warnings": len(self.issues) - len(self.errors),
            "series": list(self.series_seen),
        }


# --------------------------------------------------------------------------- shared checks


def parse_published_decimal(text: str) -> Decimal:
    """A provider's numeric cell: plain decimal text only (no exponent, separators, %)."""
    cleaned = text.strip()
    if not _PLAIN_DECIMAL.fullmatch(cleaned):
        raise ValueError(f"{text!r} is not a plain decimal")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:  # pragma: no cover - excluded by the pattern
        raise ValueError(f"{text!r} is not a number") from exc


def check_rate(value: Decimal, series: str, on: date, issues: list[Issue]) -> bool:
    """Plausibility of a yield in percentage points; False rejects the observation."""
    low, high = RATE_HARD_BOUNDS
    if not low <= value <= high:
        issues.append(
            Issue(
                IssueLevel.ERROR,
                "implausible_rate",
                f"{series}: {value} is outside [{low}, {high}] percentage points "
                "(unit or parsing error?)",
                on,
            )
        )
        return False
    soft_low, soft_high = RATE_SOFT_BOUNDS
    if not soft_low <= value <= soft_high:
        issues.append(
            Issue(
                IssueLevel.WARNING,
                "unusual_rate",
                f"{series}: {value} is outside [{soft_low}, {soft_high}] percentage points",
                on,
            )
        )
    return True


def check_date(on: date, today: date, series: str, issues: list[Issue]) -> bool:
    if on > today:
        issues.append(
            Issue(IssueLevel.ERROR, "future_date", f"{series}: {on} is after {today}", on)
        )
        return False
    if on.weekday() >= 5:
        issues.append(
            Issue(IssueLevel.WARNING, "weekend_date", f"{series}: {on} is a weekend day", on)
        )
    return True


def dedupe[T](
    items: list[tuple[tuple[Any, ...], Any, T]], issues: list[Issue], label: str
) -> list[T]:
    """Within one payload: identical repeats collapse to one (warning); the same key with
    different values is an in-payload conflict and **neither** observation is kept."""
    by_key: dict[tuple[Any, ...], list[tuple[Any, T]]] = {}
    for key, value, item in items:
        by_key.setdefault(key, []).append((value, item))
    out: list[T] = []
    for key, group in by_key.items():
        values = {v for v, _ in group}
        on = next((k for k in key if isinstance(k, date)), None)
        if len(values) > 1:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "conflict_in_payload",
                    f"{label} {key}: different values in one payload "
                    f"({', '.join(sorted(str(v) for v in values))}); none kept",
                    on,
                )
            )
            continue
        if len(group) > 1:
            issues.append(
                Issue(
                    IssueLevel.WARNING,
                    "duplicate_in_payload",
                    f"{label} {key}: repeated {len(group)} times with the same value",
                    on,
                )
            )
        out.append(group[0][1])
    return out
