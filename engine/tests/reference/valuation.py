"""End-to-end reference valuations from benchmark fixture inputs."""

from __future__ import annotations

from datetime import date
from fractions import Fraction
from typing import Any

from . import terminal
from .arithmetic import Num, add, div, mul, power, q
from .discounting import present_value
from .forecast import fcfe_flows, operating_forecast, stub_mode_a, stub_mode_b
from .timeline import YEAR_DAYS, flow_times, gordon_tv_time, period_end_times, years_between


def _terminal(
    final_flow: Fraction, final_nopat: Fraction, rate: Fraction, spec: dict[str, Any]
) -> Fraction:
    g = q(spec["g"])
    if spec["form"] == "simple":
        return terminal.gordon_simple(final_flow, rate, g)
    if spec["form"] == "value_driver":
        return terminal.gordon_value_driver(final_nopat, rate, g, q(spec["ronic"]))
    raise ValueError(spec["form"])


def _price(equity: Num, shares: dict[str, Any]) -> Num:
    if shares["option_tranches"]:
        raise NotImplementedError("dilution with instruments is covered by tsm.solve (B4)")
    return div(equity, q(shares["basic"]))


def value_fcff(fx: dict[str, Any]) -> dict[str, Num]:
    inp, conv = fx["inputs"], fx["convention"]
    years = operating_forecast(inp)
    rate = q(inp["wacc"])
    flows = [y.fcff for y in years]
    t_stub = None
    out: dict[str, Num] = {}
    if "stub" in inp:
        stub = inp["stub"]
        if stub["mode"] != "B":
            raise ValueError("fixtures use Mode B; Mode A is checked separately")
        t_stub = years_between(stub["balance_sheet_date"], stub["fiscal_year_end"])
        flows[0] = stub_mode_b(years[0], stub)
        fye = date.fromisoformat(stub["fiscal_year_end"])
        days_remaining = (fye - date.fromisoformat(stub["balance_sheet_date"])).days
        days_in_year = (fye - fye.replace(year=fye.year - 1)).days
        out["fcff_stub"] = flows[0]
        out["fcff_stub_mode_a"] = stub_mode_a(years[0], days_remaining, days_in_year)
        out["t_stub"] = t_stub
    n = len(years)
    ends = period_end_times(n, t_stub)
    rates = [rate] * n
    times = flow_times(n, t_stub, conv)
    tv = _terminal(years[-1].fcff, years[-1].nopat, rate, inp["terminal"])
    tv_time = gordon_tv_time(n, t_stub, conv)
    out["sum_pv_flows"] = present_value(list(zip(times, flows, strict=True)), rates, ends)
    out["tv"] = tv
    out["pv_tv"] = present_value([(tv_time, tv)], rates, ends)
    ev = present_value([*zip(times, flows, strict=True), (tv_time, tv)], rates, ends)
    out["ev" if t_stub is None else "ev_b"] = ev
    bridge = inp["bridge"]
    equity_b = add(ev, q(bridge["cash"]) - q(bridge["debt"]))
    if "valuation_date" in inp:
        out["equity_b"] = equity_b
        equity = _roll_forward(equity_b, inp, fx)
        out["equity_v"] = equity
    else:
        equity = equity_b
        out["equity"] = equity
    out["price"] = _price(equity, inp["shares"])
    return out


def _roll_forward(equity_b: Num, inp: dict[str, Any], fx: dict[str, Any]) -> Num:
    """§5.4: compound at Ke from B to V, then subtract dividends with ex-date in (B, V]."""
    b = inp["stub"]["balance_sheet_date"]
    v = inp["valuation_date"]
    factor = power(1 + q(inp["cost_of_equity"]), years_between(b, v))
    dividends = sum(
        (
            q(d["per_share"]) * q(inp["shares"]["basic"])
            for d in inp["dividends"]
            if b < d["ex_date"] <= v
        ),
        Fraction(0),
    )
    return add(mul(equity_b, factor), -dividends)


def value_fcfe(fx: dict[str, Any]) -> dict[str, Num]:
    """§4: FCFE at Ke produces equity value directly — no bridge."""
    inp = fx["inputs"]
    years = operating_forecast(inp)
    ke = q(inp["cost_of_equity"])
    flows = fcfe_flows(years, inp)
    n = len(years)
    ends = period_end_times(n, None)
    times = flow_times(n, None, fx["convention"])
    tv = terminal.gordon_simple(flows[-1], ke, q(inp["terminal"]["g"]))
    tv_time = gordon_tv_time(n, None, fx["convention"])
    equity = present_value([*zip(times, flows, strict=True), (tv_time, tv)], [ke] * n, ends)
    return {
        "fcfe": flows,  # type: ignore[dict-item]
        "sum_pv_flows": present_value(list(zip(times, flows, strict=True)), [ke] * n, ends),
        "tv": tv,
        "pv_tv": present_value([(tv_time, tv)], [ke] * n, ends),
        "equity": equity,
        "price": _price(equity, inp["shares"]),
    }


__all__ = ["YEAR_DAYS", "value_fcfe", "value_fcff"]
