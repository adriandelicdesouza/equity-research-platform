"""Operating forecast, FCFF and FCFE (methodology §2–§4)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .arithmetic import q


@dataclass(frozen=True)
class Year:
    revenue: Fraction
    ebit: Fraction
    tax_rate: Fraction
    da: Fraction
    capex: Fraction
    nwc: Fraction
    delta_nwc: Fraction

    @property
    def taxes(self) -> Fraction:
        return self.tax_rate * max(self.ebit, Fraction(0))

    @property
    def nopat(self) -> Fraction:
        return self.ebit - self.taxes

    @property
    def fcff(self) -> Fraction:
        return self.nopat + self.da - self.capex - self.delta_nwc

    @property
    def net_investment(self) -> Fraction:
        """Change in invested capital: capex − D&A + ΔNWC."""
        return self.capex - self.da + self.delta_nwc


def operating_forecast(inputs: dict[str, Any]) -> list[Year]:
    revenue, nwc_prev = q(inputs["revenue_0"]), q(inputs["nwc_0"])
    years: list[Year] = []
    for y in inputs["years"]:
        revenue = revenue * (1 + q(y["growth"]))
        nwc = revenue * q(y["nwc_pct"])
        years.append(
            Year(
                revenue=revenue,
                ebit=revenue * q(y["ebit_margin"]),
                tax_rate=q(y["tax_rate"]),
                da=revenue * q(y["da_pct"]),
                capex=revenue * q(y["capex_pct"]),
                nwc=nwc,
                delta_nwc=nwc - nwc_prev,
            )
        )
        nwc_prev = nwc
    return years


def stub_mode_b(year1: Year, stub: dict[str, Any]) -> Fraction:
    """§5.3 Mode B: full-year forecast minus YTD actuals; ΔNWC from the interim balance sheet."""
    ytd = stub["ytd"]
    ebit = year1.ebit - q(ytd["ebit"])
    taxes = year1.tax_rate * max(ebit, Fraction(0))
    da = year1.da - q(ytd["da"])
    capex = year1.capex - q(ytd["capex"])
    delta_nwc = year1.nwc - q(stub["nwc_at_b"])
    return (ebit - taxes) + da - capex - delta_nwc


def stub_mode_a(year1: Year, days_remaining: int, days_in_year: int) -> Fraction:
    """§5.3 Mode A: every component prorated by f = days(B → FYE₁) / days(FY₁)."""
    return Fraction(days_remaining, days_in_year) * year1.fcff


def fcfe_flows(years: list[Year], inputs: dict[str, Any]) -> list[Fraction]:
    """§4: opening-balance interest, constant/explicit cash and debt paths."""
    kd, r_cash = q(inputs["cost_of_debt"]), q(inputs["cash_yield"])
    debt = [q(d) for d in inputs["debt_path"]]
    cash = [q(c) for c in inputs["cash_path"]]
    flows = []
    for k, y in enumerate(years):
        pti = y.ebit - kd * debt[k] + r_cash * cash[k]
        net_income = pti - y.tax_rate * max(pti, Fraction(0))
        flows.append(net_income + y.da - y.capex - y.delta_nwc + (debt[k + 1] - debt[k]))
    return flows
