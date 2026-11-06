"""Reference valuation vs. hand-worked benchmarks (docs/methodology/dcf.md §14)."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import mpmath
import pytest
from reference import economic_profit, tsm
from reference.arithmetic import Num, q, to_mpf
from reference.forecast import operating_forecast
from reference.valuation import value_fcfe, value_fcff

FIXTURES = Path(__file__).parent / "fixtures" / "benchmarks"
REL_TOL_TRANSCENDENTAL = mpmath.mpf("1e-10")  # Blueprint §13.1.1


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def assert_matches(name: str, key: str, computed: Num, expected: str) -> None:
    if "/" in expected or "e" not in expected.lower():
        exp_q = Fraction(expected)
        if isinstance(computed, Fraction):
            assert computed == exp_q, f"{name}.{key}: {computed} != {exp_q} (exact)"
            return
        expected_mp = to_mpf(exp_q)
    else:
        expected_mp = mpmath.mpf(expected)
    got = to_mpf(computed)
    rel = abs(got - expected_mp) / max(abs(expected_mp), mpmath.mpf(1))
    assert rel < REL_TOL_TRANSCENDENTAL, f"{name}.{key}: {got} vs {expected_mp} (rel {rel})"


@pytest.mark.parametrize("name", ["B1", "B1-VD", "B2", "B3", "B4"])
def test_doc_rounded_values_match_fixture(name: str) -> None:
    """Guards against drift between the methodology document and the fixtures."""
    fx = load(name)
    for key, shown in fx["doc_rounded"].items():
        places = len(shown.split(".")[1])
        exact = fx["expected"][key]
        value = to_mpf(Fraction(exact)) if "/" in exact else mpmath.mpf(exact)
        rounded = mpmath.nint(value * 10**places) / 10**places
        assert mpmath.nstr(rounded, 30, min_fixed=-30, max_fixed=30) == mpmath.nstr(
            mpmath.mpf(shown), 30, min_fixed=-30, max_fixed=30
        ), f"{name}.{key}: fixture {value} does not round to documented {shown}"


@pytest.mark.parametrize("name", ["B1", "B1-VD", "B2"])
def test_fcff_benchmarks(name: str) -> None:
    fx = load(name)
    result = value_fcff(fx)
    for key, expected in fx["expected"].items():
        if key == "fcff":
            years = operating_forecast(fx["inputs"])
            for y, exp in zip(years, expected, strict=True):
                assert y.fcff == Fraction(exp)
            continue
        assert key in result, f"{name}: reference does not produce {key}"
        assert_matches(name, key, result[key], expected)


def test_b1_and_b3_are_exact_rationals() -> None:
    """Integer exponents only → the reference must stay in exact arithmetic."""
    assert isinstance(value_fcff(load("B1"))["price"], Fraction)
    assert isinstance(value_fcfe(load("B3"))["price"], Fraction)


def test_fcfe_benchmark_b3_without_bridge() -> None:
    fx = load("B3")
    result = value_fcfe(fx)
    for k, exp in enumerate(fx["expected"]["fcfe"]):
        assert result["fcfe"][k] == Fraction(exp)  # type: ignore[index]
    for key in ("sum_pv_flows", "tv", "pv_tv", "equity", "price"):
        assert_matches("B3", key, result[key], fx["expected"][key])
    bridge = fx["inputs"]["bridge"]
    bridged = result["equity"] + q(bridge["cash"]) - q(bridge["debt"])  # type: ignore[operator]
    assert bridged != Fraction(fx["expected"]["equity"]), "FCFE must not pass through the bridge"


def test_tsm_benchmark_b4_by_brute_force() -> None:
    fx = load("B4")
    inp = fx["inputs"]
    tranches = [(q(t["count"]), q(t["strike"])) for t in inp["option_tranches"]]
    price, diluted, subset = tsm.solve(q(inp["equity_value"]), q(inp["basic_shares"]), tranches)
    assert price == Fraction(fx["expected"]["price"])
    assert diluted == Fraction(fx["expected"]["diluted_shares"])
    assert list(subset) == fx["expected"]["in_the_money"]
    assert q(inp["equity_value"]) / diluted == price


@pytest.mark.parametrize("ic_0", [Fraction(0), Fraction(800), Fraction(-250), Fraction(12345, 7)])
def test_economic_profit_identity_simple_terminal(ic_0: Fraction) -> None:
    """EV via economic profit (CV = TV − IC_N) equals the FCFF DCF, for any IC_0 (B1)."""
    fx = load("B1")
    years = operating_forecast(fx["inputs"])
    tv = Fraction(fx["expected"]["tv"])
    ev = economic_profit.enterprise_value(
        years, q(fx["inputs"]["wacc"]), ic_0, g=q("0.02"), terminal_value=tv
    )
    assert ev == Fraction(fx["expected"]["ev"])


@pytest.mark.parametrize("ic_0", [Fraction(0), Fraction(800), Fraction(5000)])
def test_economic_profit_identity_value_driver(ic_0: Fraction) -> None:
    """McKinsey EP continuing-value formula reproduces the value-driver DCF (B1-VD)."""
    fx = load("B1-VD")
    years = operating_forecast(fx["inputs"])
    term = fx["inputs"]["terminal"]
    ev = economic_profit.enterprise_value(
        years, q(fx["inputs"]["wacc"]), ic_0, g=q(term["g"]), ronic=q(term["ronic"])
    )
    assert ev == Fraction(fx["expected"]["ev"])
