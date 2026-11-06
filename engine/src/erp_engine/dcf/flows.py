"""FCFF and FCFE streams, including stub periods (methodology §3–§5.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ZERO
from erp_engine.core.trace import Trace
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.forecast import ForecastYear
from erp_engine.inputs import Financing, Leases, LeaseStandard, Stub, StubMode

MODULE = "flows"


def stub_fraction_days(balance_sheet_date: date, stub: Stub) -> tuple[int, int]:
    remaining = (stub.fiscal_year_end - balance_sheet_date).days
    in_year = (stub.fiscal_year_end - stub.fiscal_year_start).days + 1
    if not 0 < remaining < in_year:
        raise EngineError("invalid_stub", "Balance-sheet date must fall inside fiscal year 1.")
    return remaining, in_year


def stub_fcff(
    fy1: ForecastYear,
    stub: Stub,
    balance_sheet_date: date,
    leases: Leases,
    report: ValidationReport,
    trace: Trace,
) -> Decimal:
    if stub.mode is StubMode.PRORATED:
        remaining, in_year = stub_fraction_days(balance_sheet_date, stub)
        f = Decimal(remaining) / Decimal(in_year)
        report.add(
            "stub_mode_a_prorated",
            Severity.WARNING,
            "Stub prorated by time (Mode A): ignores seasonality and working-capital timing.",
            fraction=f,
        )
        return trace.record(
            "flows.fcff_stub",
            "FCFF, stub (Mode A)",
            f * fy1.fcff,
            unit="currency",
            rule="f × FCFF_FY1, f = days(B→FYE1)/days(FY1)",
            module=MODULE,
            inputs=("forecast.fcff[1]",),
        )
    ytd, nwc_b = stub.ytd, stub.nwc_at_b
    if ytd is None or nwc_b is None:
        raise EngineError("stub_inputs_missing", "Mode B requires YTD actuals and NWC at B.")
    if stub.ytd_fiscal_year_end is not None and stub.ytd_fiscal_year_end != stub.fiscal_year_end:
        raise EngineError("stub_fiscal_year_mismatch", "YTD actuals are for a different year.")
    remaining_revenue = fy1.revenue - ytd.revenue
    remaining, in_year = stub_fraction_days(balance_sheet_date, stub)
    prorated = fy1.revenue * Decimal(remaining) / Decimal(in_year)
    if remaining_revenue <= 0 or remaining_revenue < prorated / 4:
        report.add(
            "stub_remaining_revenue_low",
            Severity.WARNING,
            "Implied remaining-year revenue is implausibly low.",
            remaining=remaining_revenue,
            prorated=prorated,
        )
    if leases.standard is not LeaseStandard.NONE:
        report.add(
            "stub_lease_basis",
            Severity.INFO,
            "YTD actuals must be supplied on the run's lease basis.",
        )
    ebit = fy1.ebit - ytd.ebit
    taxes = fy1.tax_rate * max(ebit, ZERO)
    flow = (ebit - taxes) + (fy1.da - ytd.da) - (fy1.capex - ytd.capex) - (fy1.nwc - nwc_b)
    return trace.record(
        "flows.fcff_stub",
        "FCFF, stub (Mode B)",
        flow,
        unit="currency",
        rule="(EBIT_FY1−EBIT_YTD)(1−τ) + ΔD&A − ΔCapex − (NWC_FYE1 − NWC_B)",
        module=MODULE,
        inputs=("forecast.fcff[1]",),
    )


def fcfe_stream(
    years: list[ForecastYear], fin: Financing, trace: Trace
) -> tuple[list[Decimal], list[Decimal]]:
    """Returns (FCFE per year, net income per year) — methodology §4."""
    n = len(years)
    if len(fin.debt_path) != n + 1 or len(fin.cash_path) != n + 1:
        raise EngineError("financing_path_length", "Debt and cash paths need N + 1 balances.")
    pref = fin.preferred_dividends or tuple(ZERO for _ in years)
    nci = fin.nci_share or tuple(ZERO for _ in years)
    flows: list[Decimal] = []
    incomes: list[Decimal] = []
    for k, y in enumerate(years):
        pti = y.ebit - fin.cost_of_debt * fin.debt_path[k] + fin.cash_yield * fin.cash_path[k]
        net_income = pti - y.tax_rate * max(pti, ZERO) - pref[k] - nci[k]
        net_borrowing = fin.debt_path[k + 1] - fin.debt_path[k]
        add_back = y.sbc if y.sbc_add_back else ZERO
        flow = net_income + y.da - y.capex - y.delta_nwc + net_borrowing + add_back
        incomes.append(net_income)
        flows.append(
            trace.record(
                f"flows.fcfe[{k + 1}]",
                f"FCFE, year {k + 1}",
                flow,
                unit="currency",
                rule="NI + D&A − Capex − ΔNWC + net borrowing; interest on opening balances",
                module=MODULE,
                inputs=(f"forecast.ebit[{k + 1}]",),
            )
        )
    return flows, incomes
