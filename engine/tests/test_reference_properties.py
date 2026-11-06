"""Property tests of the reference against closed forms (Hypothesis, exact arithmetic)."""

from __future__ import annotations

from fractions import Fraction

import mpmath
from hypothesis import given, settings
from hypothesis import strategies as st
from reference import economic_profit, tsm
from reference.arithmetic import to_mpf
from reference.discounting import present_value
from reference.forecast import operating_forecast
from reference.terminal import gordon_simple
from reference.timeline import flow_times, period_end_times

rates = st.fractions(min_value=Fraction(1, 100), max_value=Fraction(30, 100), max_denominator=1000)
small = st.fractions(min_value=Fraction(-5, 100), max_value=Fraction(25, 100), max_denominator=100)
amounts = st.fractions(min_value=1, max_value=10_000, max_denominator=100)
horizon = st.integers(min_value=1, max_value=12)


@settings(max_examples=60, deadline=None)
@given(c=amounts, r=rates, n=horizon)
def test_level_flows_with_zero_growth_terminal_equal_perpetuity(
    c: Fraction, r: Fraction, n: int
) -> None:
    ends = period_end_times(n, None)
    events = [(t, c) for t in ends] + [(ends[-1], gordon_simple(c, r, Fraction(0)))]
    assert present_value(events, [r] * n, ends) == c / r


@settings(max_examples=60, deadline=None)
@given(
    c=amounts,
    r=rates,
    n=horizon,
    g=st.fractions(min_value=Fraction(-5, 100), max_value=Fraction(9, 1000), max_denominator=1000),
)
def test_growing_flows_equal_growing_perpetuity(
    c: Fraction, r: Fraction, n: int, g: Fraction
) -> None:
    ends = period_end_times(n, None)
    flows = [c * (1 + g) ** k for k in range(n)]
    events = [*zip(ends, flows, strict=True), (ends[-1], gordon_simple(flows[-1], r, g))]
    assert present_value(events, [r] * n, ends) == c / (r - g)


@settings(max_examples=40, deadline=None)
@given(c=amounts, r=rates, n=horizon)
def test_mid_period_equals_end_period_times_half_year_growth(
    c: Fraction, r: Fraction, n: int
) -> None:
    ends = period_end_times(n, None)
    end_pv = present_value([(t, c) for t in ends], [r] * n, ends)
    mid_pv = present_value([(t, c) for t in flow_times(n, None, "mid_period")], [r] * n, ends)
    expected = to_mpf(end_pv) * mpmath.sqrt(to_mpf(1 + r))
    assert abs(to_mpf(mid_pv) - expected) / expected < mpmath.mpf("1e-40")


@settings(max_examples=60, deadline=None)
@given(c=amounts, r1=rates, r2=rates, r3=rates)
def test_per_period_rates_compound_cumulatively(
    c: Fraction, r1: Fraction, r2: Fraction, r3: Fraction
) -> None:
    ends = period_end_times(3, None)
    pv = present_value([(Fraction(3), c)], [r1, r2, r3], ends)
    assert pv == c / ((1 + r1) * (1 + r2) * (1 + r3))


@settings(max_examples=40, deadline=None)
@given(
    growth=st.lists(small, min_size=1, max_size=6),
    margin=st.fractions(
        min_value=Fraction(-10, 100), max_value=Fraction(40, 100), max_denominator=100
    ),
    wacc=rates,
    ic_0=st.fractions(min_value=-1000, max_value=5000, max_denominator=10),
)
def test_economic_profit_identity_holds_for_random_forecasts(
    growth: list[Fraction], margin: Fraction, wacc: Fraction, ic_0: Fraction
) -> None:
    years_in = [
        {
            "growth": str(g),
            "ebit_margin": str(margin),
            "tax_rate": "0.25",
            "da_pct": "0.04",
            "capex_pct": "0.06",
            "nwc_pct": "0.12",
        }
        for g in growth
    ]
    years = operating_forecast({"revenue_0": "500", "nwc_0": "55", "years": years_in})
    g_t = min(wacc - Fraction(1, 100), Fraction(2, 100))
    n = len(years)
    ends = period_end_times(n, None)
    tv = gordon_simple(years[-1].fcff, wacc, g_t)
    dcf = present_value(
        [*zip(ends, (y.fcff for y in years), strict=True), (ends[-1], tv)], [wacc] * n, ends
    )
    ep = economic_profit.enterprise_value(years, wacc, ic_0, g=g_t, terminal_value=tv)
    assert dcf == ep


@settings(max_examples=60, deadline=None)
@given(
    equity=st.fractions(min_value=100, max_value=100_000, max_denominator=10),
    basic=st.fractions(min_value=10, max_value=10_000, max_denominator=1),
    tranches=st.lists(
        st.tuples(
            st.fractions(min_value=1, max_value=500, max_denominator=1),
            st.fractions(min_value=1, max_value=200, max_denominator=4),
        ),
        max_size=4,
    ),
)
def test_tsm_solution_is_self_consistent(
    equity: Fraction, basic: Fraction, tranches: list[tuple[Fraction, Fraction]]
) -> None:
    price, diluted, subset = tsm.solve(equity, basic, tranches)
    assert equity / diluted == price
    assert all(tranches[i][1] < price for i in subset)
