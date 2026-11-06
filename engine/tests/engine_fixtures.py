"""Test helper: benchmark fixture JSON → engine inputs, and tolerance comparison.

Fixtures are shared with the reference (read-only data); this helper never imports it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from erp_engine.core.timeline import Convention
from erp_engine.inputs import (
    Bridge,
    Dividend,
    Financing,
    Method,
    Operating,
    Shares,
    Stub,
    StubMode,
    Terminal,
    TerminalForm,
    ValuationInputs,
    YearDrivers,
    YtdActuals,
)

FIXTURES = Path(__file__).parent / "fixtures" / "benchmarks"
LAST_FYE = date(2024, 12, 31)


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def D(x: str) -> Decimal:  # noqa: N802
    return Decimal(x)


def operating(inp: dict[str, Any]) -> Operating:
    years = tuple(
        YearDrivers(
            D(y["growth"]),
            D(y["ebit_margin"]),
            D(y["tax_rate"]),
            D(y["da_pct"]),
            D(y["capex_pct"]),
            D(y["nwc_pct"]),
        )
        for y in inp["years"]
    )
    return Operating(D(inp["revenue_0"]), D(inp["nwc_0"]), years)


def terminal(spec: dict[str, Any]) -> Terminal:
    form = TerminalForm(spec["form"])
    return Terminal(form, D(spec["g"]), ronic=D(spec["ronic"]) if "ronic" in spec else None)


def to_inputs(fx: dict[str, Any]) -> ValuationInputs:
    inp = fx["inputs"]
    bridge = Bridge(cash=D(inp["bridge"]["cash"]), debt=D(inp["bridge"]["debt"]))
    conv = Convention(fx["convention"])
    if fx["method"] == "fcfe":
        b = LAST_FYE
        return ValuationInputs(
            method=Method.FCFE,
            currency="USD",
            balance_sheet_date=b,
            valuation_date=b,
            convention=conv,
            operating=operating(inp),
            terminal=terminal(inp["terminal"]),
            shares=Shares(D(inp["shares"]["basic"]), b),
            cost_of_equity=D(inp["cost_of_equity"]),
            bridge=bridge,
            financing=Financing(
                tuple(map(D, inp["debt_path"])),
                tuple(map(D, inp["cash_path"])),
                D(inp["cost_of_debt"]),
                D(inp["cash_yield"]),
            ),
        )
    stub = None
    b = v = LAST_FYE
    ke = None
    dividends: tuple[Dividend, ...] = ()
    if "stub" in inp:
        s = inp["stub"]
        fye = date.fromisoformat(s["fiscal_year_end"])
        y = s["ytd"]
        stub = Stub(
            StubMode(s["mode"]),
            fye.replace(year=fye.year - 1) + timedelta(days=1),
            fye,
            YtdActuals(D(y["revenue"]), D(y["ebit"]), D(y["da"]), D(y["capex"])),
            D(s["nwc_at_b"]),
        )
        b = date.fromisoformat(s["balance_sheet_date"])
        v = date.fromisoformat(inp["valuation_date"])
        ke = D(inp["cost_of_equity"])
        dividends = tuple(
            Dividend(date.fromisoformat(d["ex_date"]), D(d["per_share"])) for d in inp["dividends"]
        )
    return ValuationInputs(
        method=Method.FCFF,
        currency="USD",
        balance_sheet_date=b,
        valuation_date=v,
        convention=conv,
        operating=operating(inp),
        terminal=terminal(inp["terminal"]),
        shares=Shares(D(inp["shares"]["basic"]), b),
        discount_rates=(D(inp["wacc"]),),
        cost_of_equity=ke,
        bridge=bridge,
        stub=stub,
        dividends=dividends,
    )


def expected(value: str) -> Fraction:
    if "/" in value or "e" not in value.lower():
        return Fraction(value)
    mantissa, exponent = value.lower().split("e")
    return Fraction(mantissa) * Fraction(10) ** int(exponent)


def rel_err(computed: Decimal, target: Fraction) -> Fraction:
    got = Fraction(computed)
    return abs(got - target) / max(abs(target), Fraction(1))
