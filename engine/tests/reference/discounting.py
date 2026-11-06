"""Backward-recursion valuation of dated cash events (no cumulative discount factors).

Value is carried backwards event by event: V ← V / growth(t_i, t_{i+1}) + CF_i, then
from the first event back to t = 0. ``growth(a, b)`` compounds the per-period rates over
the overlap of [a, b] with each period, so a per-period WACC vector is handled exactly.
"""

from __future__ import annotations

from fractions import Fraction

from .arithmetic import Num, add, div, mul, power


def growth(a: Fraction, b: Fraction, rates: list[Fraction], ends: list[Fraction]) -> Num:
    """Π (1 + r_k)^(|[a, b] ∩ period k|); periods (end_{k−1}, end_k], end_0 = 0."""
    factor: Num = Fraction(1)
    start = Fraction(0)
    for rate, end in zip(rates, ends, strict=True):
        overlap = min(b, end) - max(a, start)
        if overlap > 0:
            factor = mul(factor, power(1 + rate, overlap))
        start = end
    if b > ends[-1]:  # beyond the horizon: continue at the final rate
        tail = b - max(a, ends[-1])
        if tail > 0:
            factor = mul(factor, power(1 + rates[-1], tail))
    return factor


def present_value(
    events: list[tuple[Fraction, Num]], rates: list[Fraction], ends: list[Fraction]
) -> Num:
    """PV at t=0 of (time, amount) events, by backward recursion."""
    merged: dict[Fraction, Num] = {}
    for t, amount in events:
        merged[t] = add(merged.get(t, Fraction(0)), amount)
    times = sorted(merged)
    value: Num = merged[times[-1]]
    for later, earlier in zip(reversed(times), list(reversed(times))[1:], strict=False):
        value = add(div(value, growth(earlier, later, rates, ends)), merged[earlier])
    return div(value, growth(Fraction(0), times[0], rates, ends))
