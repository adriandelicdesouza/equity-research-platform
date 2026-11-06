"""Discount times (methodology §5.1) — Actual/365.25 from the balance-sheet date B."""

from __future__ import annotations

from datetime import date
from fractions import Fraction

YEAR_DAYS = Fraction(36525, 100)


def years_between(start: str, end: str) -> Fraction:
    return Fraction((date.fromisoformat(end) - date.fromisoformat(start)).days) / YEAR_DAYS


def period_end_times(n: int, t_stub: Fraction | None) -> list[Fraction]:
    """End time of each forecast period k = 1..n."""
    if t_stub is None:
        return [Fraction(k) for k in range(1, n + 1)]
    return [t_stub + (k - 1) for k in range(1, n + 1)]


def flow_times(n: int, t_stub: Fraction | None, convention: str) -> list[Fraction]:
    ends = period_end_times(n, t_stub)
    if convention == "end_of_period":
        return ends
    if convention != "mid_period":
        raise ValueError(convention)
    starts = [Fraction(0), *ends[:-1]]
    # Midpoint of each period: half the stub for k=1 with a stub; otherwise end − ½.
    return [s + (e - s) / 2 for s, e in zip(starts, ends, strict=True)]


def gordon_tv_time(n: int, t_stub: Fraction | None, convention: str) -> Fraction:
    """D27 (amended): Gordon TV timing follows the cash-flow convention."""
    end = period_end_times(n, t_stub)[-1]
    return end - Fraction(1, 2) if convention == "mid_period" else end
