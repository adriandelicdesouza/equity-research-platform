"""Decimal numerics (Blueprint §6.3, D9).

All authoritative arithmetic runs in one fixed context: precision 34, ROUND_HALF_EVEN,
with invalid operations, division by zero and overflow trapped. Public engine entry points
are wrapped with :func:`calc` so results never depend on the caller's ambient context.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from erp_engine.core.errors import EngineError

PRECISION = 34
ZERO = Decimal(0)
ONE = Decimal(1)


def make_context() -> Context:
    return Context(
        prec=PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def calc[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Run ``fn`` inside the engine's decimal context."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext(make_context()):
            return fn(*args, **kwargs)

    return wrapper


def dec(value: str | int | Decimal) -> Decimal:
    """Strict conversion: floats are rejected to avoid binary rounding artefacts."""
    if isinstance(value, float):  # pragma: no cover - guarded by typing too
        raise TypeError("floats are not accepted; pass str, int or Decimal")
    return Decimal(value)


def dpow(base: Decimal, exponent: Decimal) -> Decimal:
    """base ** exponent; integral exponents use exact repeated multiplication semantics,
    fractional exponents use exp(exponent × ln(base)) (correctly rounded operations)."""
    if exponent == exponent.to_integral_value():
        return base ** int(exponent)
    if base <= 0:
        raise EngineError("invalid_power", f"Cannot raise {base} to non-integer power {exponent}.")
    return (exponent * base.ln()).exp()
