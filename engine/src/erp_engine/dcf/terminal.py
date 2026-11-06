"""Terminal value, timing, implied counterparts and coherence checks (methodology §6)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ONE, ZERO
from erp_engine.core.timeline import Convention
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.inputs import ExitMetric, Terminal, TerminalForm, TerminalTiming

MODULE = "terminal"


@dataclass(frozen=True)
class TerminalResult:
    value: Decimal
    time: Decimal
    implied_exit_multiple: Decimal | None
    implied_growth: Decimal | None
    implied_ronic: Decimal | None
    terminal_flow: Decimal | None


def terminal_value(
    spec: Terminal,
    *,
    final_flow: Decimal,
    final_income: Decimal,
    final_metric: dict[ExitMetric, Decimal],
    rate: Decimal,
    end_time: Decimal,
    convention: Convention,
    is_equity: bool,
    run_lease_basis: str | None,
    risk_free_rate: Decimal | None,
    report: ValidationReport,
) -> TerminalResult:
    g = spec.g
    if spec.form is not TerminalForm.EXIT_MULTIPLE and g >= rate:
        raise EngineError(
            "terminal_growth_ge_rate",
            f"Terminal growth {g} must be below the discount rate {rate}.",
        )
    if g < 0:
        report.add("terminal_growth_negative", Severity.INFO, "Negative terminal growth.", g=g)
    if risk_free_rate is not None and g > risk_free_rate:
        report.add(
            "terminal_growth_above_rf",
            Severity.WARNING,
            "Terminal growth exceeds the risk-free rate.",
            g=g,
            rf=risk_free_rate,
        )
    implied_ronic = None
    terminal_flow: Decimal | None = None
    reinvestment = ONE - final_flow / final_income if final_income != 0 else None
    if spec.form is TerminalForm.SIMPLE:
        terminal_flow = final_flow * (1 + g)
        value = terminal_flow / (rate - g)
        if reinvestment is not None and reinvestment > 0 and g != 0:
            implied_ronic = g / reinvestment
    elif spec.form is TerminalForm.VALUE_DRIVER:
        if spec.ronic is None:
            raise EngineError(
                "ronic_required", "Value-driver terminal value requires an approved RONIC (D35)."
            )
        if spec.ronic <= 0:
            raise EngineError("invalid_ronic", "RONIC must be positive.")
        implied_ronic = spec.ronic
        terminal_flow = final_income * (1 + g) * (1 - g / spec.ronic)
        value = terminal_flow / (rate - g)
        if reinvestment is not None and abs(reinvestment - g / spec.ronic) > Decimal("0.05"):
            report.add(
                "terminal_reinvestment_inconsistent",
                Severity.WARNING,
                "Final-year reinvestment rate differs from g/RONIC by more than 5 pp.",
                final_year=reinvestment,
                implied=g / spec.ronic,
            )
    else:
        if is_equity:
            raise EngineError(
                "exit_multiple_requires_fcff",
                "Exit multiples value the enterprise; not valid for FCFE.",
            )
        if spec.exit_multiple is None:
            raise EngineError("exit_multiple_missing", "Exit multiple not provided.")
        if (
            spec.exit_metric is ExitMetric.EBITDA
            and run_lease_basis is not None
            and spec.exit_lease_basis != run_lease_basis
        ):
            raise EngineError(
                "lease_basis_mismatch",
                f"Exit EBITDA multiple basis {spec.exit_lease_basis!r} differs "
                f"from the run's {run_lease_basis!r}.",
            )
        value = spec.exit_multiple * final_metric[spec.exit_metric]
    gordon = spec.form is not TerminalForm.EXIT_MULTIPLE
    follow = spec.timing is TerminalTiming.FOLLOW_CONVENTION
    mid = gordon and follow and convention is Convention.MID_PERIOD
    time = end_time - Decimal("0.5") if mid else end_time
    if spec.timing is TerminalTiming.END_OF_PERIOD and convention is Convention.MID_PERIOD:
        report.add(
            "terminal_timing_override",
            Severity.INFO,
            "Conservative override: Gordon TV discounted at end of final period.",
        )
    if terminal_flow is not None and terminal_flow <= 0:
        report.add(
            "terminal_flow_non_positive",
            Severity.WARNING,
            "Terminal cash flow is zero or negative.",
            flow=terminal_flow,
        )
    if implied_ronic is not None and g > 0 and implied_ronic < rate:
        report.add(
            "terminal_value_destroying_growth",
            Severity.WARNING,
            "RONIC below the discount rate with positive growth destroys value.",
            ronic=implied_ronic,
            rate=rate,
        )
    ebitda = final_metric.get(ExitMetric.EBITDA, ZERO)
    implied_multiple = value / ebitda if gordon and ebitda > 0 and not is_equity else None
    implied_growth = None
    if not gordon and value + final_flow != 0:
        implied_growth = (value * rate - final_flow) / (value + final_flow)
    return TerminalResult(
        value, time, implied_multiple, implied_growth, implied_ronic, terminal_flow
    )
