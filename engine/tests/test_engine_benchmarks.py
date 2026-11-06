"""Engine vs. the approved methodology benchmarks (docs/methodology/dcf.md §14).

Tolerances (Blueprint §13.1.1): 1e-12 relative where the benchmark is an exact rational
(integer exponents), 1e-10 where fractional exponents are involved (B2).
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from engine_fixtures import expected, load, rel_err, to_inputs
from erp_engine.api import run_valuation
from erp_engine.dilution import tsm_price
from erp_engine.inputs import Instrument, InstrumentKind, InstrumentTreatment, Method

EXACT = Fraction(1, 10**12)
TRANSCENDENTAL = Fraction(1, 10**10)


def check(name: str, key: str, value: Decimal, target: str, tol: Fraction) -> None:
    err = rel_err(value, expected(target))
    assert err < tol, f"{name}.{key}: {value} vs {target} (rel {float(err):.3e})"


@pytest.mark.parametrize("name", ["B1", "B1-VD"])
def test_fcff_end_of_period_benchmarks(name: str) -> None:
    fx = load(name)
    r = run_valuation(to_inputs(fx))
    exp = fx["expected"]
    values = {
        "tv": r.terminal.value,
        "pv_tv": r.pv_terminal,
        "ev": r.primary.value,
        "equity": r.equity_v.value,
        "price": r.per_share.value,
        "sum_pv_flows": r.sum_pv_flows,
    }
    for key, target in exp.items():
        if key == "fcff":
            for got, t in zip(r.flows, target, strict=True):
                check(name, key, got, t, EXACT)
        else:
            check(name, key, values[key], target, EXACT)
    assert not r.validation.has_errors


def test_b2_mid_period_stub_mode_b_with_roll_forward() -> None:
    fx = load("B2")
    r = run_valuation(to_inputs(fx))
    exp = fx["expected"]
    check("B2", "fcff_stub", r.flows[0], exp["fcff_stub"], EXACT)
    check("B2", "sum_pv_flows", r.sum_pv_flows, exp["sum_pv_flows"], TRANSCENDENTAL)
    check("B2", "tv", r.terminal.value, exp["tv"], EXACT)
    check("B2", "pv_tv", r.pv_terminal, exp["pv_tv"], TRANSCENDENTAL)
    check("B2", "ev_b", r.primary.value, exp["ev_b"], TRANSCENDENTAL)
    check("B2", "equity_b", r.equity_b.value, exp["equity_b"], TRANSCENDENTAL)
    check("B2", "equity_v", r.equity_v.value, exp["equity_v"], TRANSCENDENTAL)
    check("B2", "price", r.per_share.value, exp["price"], TRANSCENDENTAL)
    check("B2", "t_stub", r.times[1] - Decimal("0.5"), exp["t_stub"], EXACT)


def test_b3_fcfe_produces_equity_without_bridge() -> None:
    fx = load("B3")
    r = run_valuation(to_inputs(fx))
    assert r.method is Method.FCFE
    assert r.primary.kind.value == "equity_value"
    assert "bridge.equity_b" not in r.trace  # the bridge never ran for FCFE
    for got, t in zip(r.flows, fx["expected"]["fcfe"], strict=True):
        check("B3", "fcfe", got, t, EXACT)
    for key, value in {
        "tv": r.terminal.value,
        "pv_tv": r.pv_terminal,
        "sum_pv_flows": r.sum_pv_flows,
        "equity": r.equity_v.value,
        "price": r.per_share.value,
    }.items():
        check("B3", key, value, fx["expected"][key], EXACT)


def test_b4_tsm_fixed_point() -> None:
    fx = load("B4")
    inp = fx["inputs"]
    options = [
        Instrument(
            f"T{i + 1}",
            InstrumentKind.OPTION,
            Decimal(t["count"]),
            InstrumentTreatment.TSM,
            strike=Decimal(t["strike"]),
        )
        for i, t in enumerate(inp["option_tranches"])
    ]
    price, diluted, itm = tsm_price(
        Decimal(inp["equity_value"]), Decimal(inp["basic_shares"]), options
    )
    check("B4", "price", price, fx["expected"]["price"], EXACT)
    check("B4", "diluted_shares", diluted, fx["expected"]["diluted_shares"], EXACT)
    assert itm == ("T1",)


def test_mode_a_stub_value_matches_methodology() -> None:
    from dataclasses import replace

    from erp_engine.inputs import StubMode

    fx = load("B2")
    inputs = to_inputs(fx)
    assert inputs.stub is not None
    r = run_valuation(replace(inputs, stub=replace(inputs.stub, mode=StubMode.PRORATED)))
    check("B2", "fcff_stub_mode_a", r.flows[0], fx["expected"]["fcff_stub_mode_a"], EXACT)
    assert "stub_mode_a_prorated" in r.validation.codes()
