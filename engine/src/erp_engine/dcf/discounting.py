"""Present values with cumulative discount factors (methodology §5.2)."""

from __future__ import annotations

from decimal import Decimal

from erp_engine.core.timeline import discount_factor


def present_values(
    flows: list[Decimal], times: list[Decimal], rates: list[Decimal], ends: list[Decimal]
) -> list[tuple[Decimal, Decimal]]:
    """Returns (discount factor, present value) per flow."""
    out = []
    for flow, t in zip(flows, times, strict=True):
        df = discount_factor(t, rates, ends)
        out.append((df, flow * df))
    return out
