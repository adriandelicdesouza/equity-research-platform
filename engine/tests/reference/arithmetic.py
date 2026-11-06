"""Exact / high-precision arithmetic helpers (no ``decimal``)."""

from __future__ import annotations

from fractions import Fraction

import mpmath

mpmath.mp.dps = 50

Num = Fraction | mpmath.mpf


def q(text: str | int) -> Fraction:
    """Parse '0.10', '1000', '177/2' exactly."""
    return Fraction(text)


def to_mpf(x: Num) -> mpmath.mpf:
    if isinstance(x, Fraction):
        return mpmath.mpf(x.numerator) / x.denominator
    return mpmath.mpf(x)


def power(base: Fraction, exponent: Fraction) -> Num:
    """base**exponent: exact Fraction for integer exponents, mpmath otherwise."""
    if exponent.denominator == 1:
        return base ** int(exponent)
    return mpmath.power(to_mpf(base), to_mpf(exponent))


def mul(a: Num, b: Num) -> Num:
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        return a * b
    return to_mpf(a) * to_mpf(b)


def div(a: Num, b: Num) -> Num:
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        return a / b
    return to_mpf(a) / to_mpf(b)


def add(a: Num, b: Num) -> Num:
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        return a + b
    return to_mpf(a) + to_mpf(b)
