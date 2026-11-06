"""Engine vs. the independent reference on randomized inputs (Blueprint §13.1.1)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

from engine_fixtures import load, rel_err, to_inputs
from erp_engine.api import run_valuation
from hypothesis import given, settings
from hypothesis import strategies as st
from reference.arithmetic import to_mpf
from reference.valuation import value_fcfe, value_fcff

pct = st.integers(min_value=-5, max_value=25)  # percent
margin = st.integers(min_value=5, max_value=40)


def _years(growths: list[int], m: int) -> list[dict[str, str]]:
    return [
        {
            "growth": f"{g / 100}",
            "ebit_margin": f"{m / 100}",
            "tax_rate": "0.25",
            "da_pct": "0.05",
            "capex_pct": "0.07",
            "nwc_pct": "0.10",
        }
        for g in growths
    ]


def _ref_fraction(value: object) -> Fraction:
    if isinstance(value, Fraction):
        return value
    return Fraction(str(to_mpf(value)))  # type: ignore[arg-type]


@settings(max_examples=40, deadline=None)
@given(
    growths=st.lists(pct, min_size=2, max_size=8),
    m=margin,
    wacc=st.integers(min_value=6, max_value=15),
    g=st.integers(min_value=-2, max_value=4),
    form=st.sampled_from(["simple", "value_driver"]),
    conv=st.sampled_from(["end_of_period", "mid_period"]),
)
def test_fcff_matches_reference(
    growths: list[int], m: int, wacc: int, g: int, form: str, conv: str
) -> None:
    fx = load("B1")
    fx["convention"] = conv
    fx["inputs"]["years"] = _years(growths, m)
    fx["inputs"]["wacc"] = f"{wacc / 100}"
    fx["inputs"]["terminal"] = {"form": form, "g": f"{g / 100}", "ronic": f"{(wacc + 3) / 100}"}
    engine = run_valuation(to_inputs(fx))
    ref = value_fcff(fx)
    tol = Fraction(1, 10**12) if conv == "end_of_period" else Fraction(1, 10**10)
    assert rel_err(engine.primary.value, _ref_fraction(ref["ev"])) < tol
    assert rel_err(engine.per_share.value, _ref_fraction(ref["price"])) < tol


@settings(max_examples=25, deadline=None)
@given(
    growths=st.lists(pct, min_size=2, max_size=6),
    m=margin,
    wacc=st.integers(min_value=6, max_value=15),
    ytd_share=st.integers(min_value=30, max_value=60),
    nwc_b=st.integers(min_value=80, max_value=160),
)
def test_stub_mode_b_mid_period_matches_reference(
    growths: list[int], m: int, wacc: int, ytd_share: int, nwc_b: int
) -> None:
    fx = load("B2")
    fx["inputs"]["years"] = _years(growths, m)
    fx["inputs"]["wacc"] = f"{wacc / 100}"
    fx["inputs"]["stub"]["nwc_at_b"] = str(nwc_b)
    fx["inputs"]["stub"]["ytd"]["ebit"] = str(Fraction(ytd_share, 100) * 200)
    engine = run_valuation(to_inputs(fx))
    ref = value_fcff(fx)
    assert rel_err(engine.primary.value, _ref_fraction(ref["ev_b"])) < Fraction(1, 10**10)
    assert rel_err(engine.per_share.value, _ref_fraction(ref["price"])) < Fraction(1, 10**10)


@settings(max_examples=30, deadline=None)
@given(
    growths=st.lists(pct, min_size=3, max_size=3),
    m=margin,
    ke=st.integers(min_value=8, max_value=16),
    debts=st.lists(st.integers(min_value=0, max_value=900), min_size=3, max_size=3),
)
def test_fcfe_matches_reference(growths: list[int], m: int, ke: int, debts: list[int]) -> None:
    fx = load("B3")
    fx["inputs"]["years"] = _years(growths, m)
    fx["inputs"]["cost_of_equity"] = f"{ke / 100}"
    fx["inputs"]["debt_path"] = ["400", *map(str, debts)]
    engine = run_valuation(to_inputs(fx))
    ref = value_fcfe(fx)
    assert rel_err(engine.equity_v.value, _ref_fraction(ref["equity"])) < Fraction(1, 10**12)


def test_engine_output_is_decimal_not_float() -> None:
    r = run_valuation(replace(to_inputs(load("B1"))))
    assert isinstance(r.per_share.value, Decimal)
