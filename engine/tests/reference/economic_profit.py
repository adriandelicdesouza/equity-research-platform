"""Economic-profit (EVA) valuation — an independent route to enterprise value.

With invested capital IC_k = IC_{k−1} + net investment_k and economic profit
EP_k = NOPAT_k − WACC × IC_{k−1}, enterprise value (end-of-period, constant WACC) is

    EV = IC_0 + Σ EP_k / (1+WACC)^k + CV_EP / (1+WACC)^N

where the continuing value is either
  * value-driver form (McKinsey):  CV_EP = EP_{N+1}/WACC
        + NOPAT_{N+1} × (g/RONIC) × (RONIC − WACC) / (WACC × (WACC − g)),
    with EP_{N+1} = NOPAT_{N+1} − WACC × IC_N; or
  * any other DCF terminal value TV:  CV_EP = TV − IC_N.
The result must equal the FCFF DCF and must not depend on the choice of IC_0.
"""

from __future__ import annotations

from fractions import Fraction

from .forecast import Year


def enterprise_value(
    years: list[Year],
    wacc: Fraction,
    ic_0: Fraction,
    *,
    g: Fraction,
    ronic: Fraction | None = None,
    terminal_value: Fraction | None = None,
) -> Fraction:
    ic = ic_0
    value = ic_0
    for k, y in enumerate(years, start=1):
        economic_profit = y.nopat - wacc * ic
        value += economic_profit / (1 + wacc) ** k
        ic += y.net_investment
    n = len(years)
    if ronic is not None:
        nopat_next = years[-1].nopat * (1 + g)
        ep_next = nopat_next - wacc * ic
        cv = ep_next / wacc + nopat_next * (g / ronic) * (ronic - wacc) / (wacc * (wacc - g))
    elif terminal_value is not None:
        cv = terminal_value - ic
    else:
        raise ValueError("ronic or terminal_value required")
    return value + cv / (1 + wacc) ** n
