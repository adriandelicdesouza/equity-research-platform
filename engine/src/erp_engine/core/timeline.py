"""Time and discount schedules (methodology §5.1–§5.2). Day count Actual/365.25.

Discounting uses cumulative discount factors built forward period by period:
DF(t) = Π_i (1 + r_i)^(−|[0, t] ∩ period_i|).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ONE, ZERO, dpow

YEAR_DAYS = Decimal("365.25")


class Convention(StrEnum):
    END_OF_PERIOD = "end_of_period"
    MID_PERIOD = "mid_period"


def year_fraction(start: date, end: date) -> Decimal:
    return Decimal((end - start).days) / YEAR_DAYS


def period_ends(n: int, t_stub: Decimal | None) -> list[Decimal]:
    if n < 1 or n > 20:
        raise EngineError("invalid_horizon", "Forecast horizon must be 1 to 20 periods.")
    first = t_stub if t_stub is not None else ONE
    return [first + Decimal(k) for k in range(n)]


def cash_flow_times(n: int, t_stub: Decimal | None, convention: Convention) -> list[Decimal]:
    ends = period_ends(n, t_stub)
    if convention is Convention.END_OF_PERIOD:
        return ends
    starts = [ZERO, *ends[:-1]]
    return [s + (e - s) / 2 for s, e in zip(starts, ends, strict=True)]


def discount_factor(t: Decimal, rates: list[Decimal], ends: list[Decimal]) -> Decimal:
    """Cumulative DF to time t with per-period rates (beyond the horizon: final rate)."""
    df = ONE
    start = ZERO
    for rate, end in zip(rates, ends, strict=True):
        span = min(t, end) - start
        if span > 0:
            df /= dpow(ONE + rate, span)
        start = end
        if t <= end:
            return df
    return df / dpow(ONE + rates[-1], t - ends[-1])
