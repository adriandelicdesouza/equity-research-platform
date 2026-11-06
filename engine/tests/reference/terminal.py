"""Terminal value forms (methodology §6.1)."""

from __future__ import annotations

from fractions import Fraction


def gordon_simple(final_flow: Fraction, rate: Fraction, g: Fraction) -> Fraction:
    if g >= rate:
        raise ValueError("g must be below the discount rate")
    return final_flow * (1 + g) / (rate - g)


def gordon_value_driver(
    final_nopat: Fraction, rate: Fraction, g: Fraction, ronic: Fraction
) -> Fraction:
    if g >= rate:
        raise ValueError("g must be below the discount rate")
    return final_nopat * (1 + g) * (1 - g / ronic) / (rate - g)
