"""Roll-forward from the balance-sheet date B to the valuation date V (methodology §5.4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ONE, dpow
from erp_engine.core.timeline import year_fraction
from erp_engine.core.trace import Trace
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.inputs import Dividend
from erp_engine.outputs import OutputKind, ValuationOutput, require_kind

MODULE = "rollforward"


def roll_forward(
    equity_b: ValuationOutput,
    balance_sheet_date: date,
    valuation_date: date,
    cost_of_equity: Decimal | None,
    dividends: tuple[Dividend, ...],
    basic_shares: Decimal,
    report: ValidationReport,
    trace: Trace,
) -> ValuationOutput:
    require_kind(equity_b, OutputKind.EQUITY_VALUE, "Roll-forward")
    if valuation_date < balance_sheet_date:
        raise EngineError("valuation_before_balance_sheet", "Valuation date precedes B.")
    if valuation_date == balance_sheet_date:
        return replace(equity_b, valuation_date=valuation_date)
    if cost_of_equity is None:
        raise EngineError("cost_of_equity_required", "Roll-forward requires the cost of equity.")
    gap = (valuation_date - balance_sheet_date).days
    if gap > 183:
        report.add(
            "stale_balance_sheet",
            Severity.WARNING,
            "Valuation date is more than 183 days after the balance-sheet date.",
            days=gap,
        )
    factor = dpow(ONE + cost_of_equity, year_fraction(balance_sheet_date, valuation_date))
    paid = sum(
        (
            d.per_share * basic_shares
            for d in dividends
            if balance_sheet_date < d.ex_date <= valuation_date
        ),
        Decimal(0),
    )
    value = equity_b.value * factor - paid
    trace.record(
        "rollforward.factor",
        "Roll-forward factor",
        factor,
        unit="ratio",
        rule="(1 + Ke)^((V − B)/365.25)",
        module=MODULE,
    )
    trace.record(
        "rollforward.dividends",
        "Dividends with ex-date in (B, V]",
        paid,
        unit="currency",
        rule="Σ DPS × basic shares",
        module=MODULE,
    )
    trace.record(
        "rollforward.equity_v",
        "Equity value at V",
        value,
        unit="currency",
        rule="Equity_B × factor − dividends",
        module=MODULE,
        inputs=(equity_b.trace_id, "rollforward.factor", "rollforward.dividends"),
    )
    return ValuationOutput(
        OutputKind.EQUITY_VALUE,
        value,
        equity_b.currency,
        valuation_date,
        equity_b.methodology,
        equity_b.basis,
        "rollforward.equity_v",
    )
