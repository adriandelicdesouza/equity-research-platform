"""Valuation orchestration (Blueprint §6.2): typed inputs → typed outputs + trace.

Steps are registry modules with declared requirements; the registry orders them and rejects
cycles or missing dependencies. FCFF produces ENTERPRISE_VALUE (bridge applies); FCFE
produces EQUITY_VALUE (no bridge). Identical inputs and versions give identical hashes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from erp_engine import bridge as bridge_mod
from erp_engine import classification, dilution, forecast, rollforward
from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import calc
from erp_engine.core.registry import Context, Module, execute
from erp_engine.core.timeline import cash_flow_times, period_ends, year_fraction
from erp_engine.core.trace import Trace
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.dcf import flows as flows_mod
from erp_engine.dcf.discounting import present_values
from erp_engine.dcf.terminal import TerminalResult, terminal_value
from erp_engine.inputs import ExitMetric, Method, ValuationInputs
from erp_engine.outputs import OutputKind, ValuationOutput
from erp_engine.version import ENGINE_VERSION, MODULE_VERSIONS


@dataclass(frozen=True)
class ValuationResult:
    method: Method
    primary: ValuationOutput  # EV (FCFF) or equity at B (FCFE)
    equity_b: ValuationOutput
    equity_v: ValuationOutput
    per_share: ValuationOutput
    flows: tuple[Decimal, ...]
    times: tuple[Decimal, ...]
    discount_factors: tuple[Decimal, ...]
    present_values: tuple[Decimal, ...]
    sum_pv_flows: Decimal
    terminal: TerminalResult
    pv_terminal: Decimal
    diluted_shares: Decimal
    trace: dict[str, dict[str, object]]
    validation: ValidationReport
    engine_version: str
    module_versions: dict[str, str]
    module_order: tuple[str, ...]
    input_hash: str
    output_hash: str

    @property
    def terminal_share(self) -> Decimal:
        return self.pv_terminal / (self.sum_pv_flows + self.pv_terminal)


def _canonical(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _canonical(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, Enum)):
        return str(obj.value if isinstance(obj, Enum) else obj.isoformat())
    return obj


def digest(obj: Any) -> str:
    payload = json.dumps(_canonical(obj), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _module(name: str, requires: set[str], provides: set[str], fn: Any) -> Module:
    return Module(name, MODULE_VERSIONS[name], frozenset(requires), frozenset(provides), fn)


def _modules(inp: ValuationInputs) -> list[Module]:
    def gate(ctx: Context) -> None:
        if inp.rate_currency is not None and inp.rate_currency != inp.currency:
            raise EngineError(
                "currency_mismatch",
                f"Rates are in {inp.rate_currency}, cash flows in {inp.currency}.",
            )
        classification.gate(inp.classification, ctx["report"])
        ctx["gate"] = True

    def project(ctx: Context) -> None:
        ctx["forecast"] = forecast.project(inp.operating, inp.leases, ctx["report"], ctx["trace"])

    def build_flows(ctx: Context) -> None:
        years = ctx["forecast"]
        t_stub = None
        if inp.method is Method.FCFF:
            stream = [y.fcff for y in years]
            incomes = [y.nopat for y in years]
            rate_list = list(inp.discount_rates)
            if not rate_list:
                raise EngineError("discount_rate_missing", "FCFF requires WACC.")
        else:
            if inp.financing is None or inp.cost_of_equity is None:
                raise EngineError("fcfe_inputs_missing", "FCFE needs financing paths and Ke.")
            if inp.stub is not None:
                raise EngineError(
                    "fcfe_stub_not_supported",
                    "FCFE with an interim balance sheet is not supported in R1.",
                )
            stream, incomes = flows_mod.fcfe_stream(years, inp.financing, ctx["trace"])
            rate_list = [inp.cost_of_equity]
        if inp.stub is not None:
            if len(years) < 2:
                raise EngineError("stub_horizon", "A stub requires at least two forecast periods.")
            stream[0] = flows_mod.stub_fcff(
                years[0], inp.stub, inp.balance_sheet_date, inp.leases, ctx["report"], ctx["trace"]
            )
            t_stub = year_fraction(inp.balance_sheet_date, inp.stub.fiscal_year_end)
        n = len(years)
        if len(rate_list) == 1:
            rate_list = rate_list * n
        if len(rate_list) != n:
            raise EngineError("discount_rate_length", "Provide one rate or one per period.")
        ctx.update(
            stream=stream,
            incomes=incomes,
            rates=rate_list,
            t_stub=t_stub,
            ends=period_ends(n, t_stub),
            times=cash_flow_times(n, t_stub, inp.convention),
        )

    def terminal(ctx: Context) -> None:
        y = ctx["forecast"][-1]
        ctx["terminal"] = terminal_value(
            inp.terminal,
            final_flow=ctx["stream"][-1],
            final_income=ctx["incomes"][-1],
            final_metric={
                ExitMetric.EBITDA: y.ebitda,
                ExitMetric.EBIT: y.ebit,
                ExitMetric.REVENUE: y.revenue,
            },
            rate=ctx["rates"][-1],
            end_time=ctx["ends"][-1],
            convention=inp.convention,
            is_equity=inp.method is Method.FCFE,
            run_lease_basis=forecast.lease_basis(inp.leases),
            risk_free_rate=inp.risk_free_rate,
            report=ctx["report"],
        )

    def discount(ctx: Context) -> None:
        pvs = present_values(ctx["stream"], ctx["times"], ctx["rates"], ctx["ends"])
        tv: TerminalResult = ctx["terminal"]
        [(_, pv_tv)] = present_values([tv.value], [tv.time], ctx["rates"], ctx["ends"])
        total = sum((pv for _, pv in pvs), Decimal(0)) + pv_tv
        trace: Trace = ctx["trace"]
        kind = OutputKind.ENTERPRISE_VALUE if inp.method is Method.FCFF else OutputKind.EQUITY_VALUE
        node = "discounting.ev" if kind is OutputKind.ENTERPRISE_VALUE else "discounting.equity_b"
        trace.record(
            "discounting.pv_tv",
            "PV of terminal value",
            pv_tv,
            unit="currency",
            rule="TV × DF(t_TV)",
            module="discounting",
        )
        trace.record(
            node,
            "Present value at B",
            total,
            unit="currency",
            rule="Σ flow × DF(t) + PV(TV); cumulative DF with per-period rates",
            module="discounting",
            inputs=("discounting.pv_tv",),
        )
        ctx.update(
            pvs=pvs,
            pv_tv=pv_tv,
            primary=ValuationOutput(
                kind, total, inp.currency, inp.balance_sheet_date, inp.method.value, trace_id=node
            ),
        )
        tv_share = pv_tv / total if total != 0 else Decimal(0)
        if tv_share > Decimal("0.85"):
            ctx["report"].add(
                "terminal_share_high",
                Severity.WARNING,
                "Terminal value exceeds 85% of total value.",
                share=tv_share,
            )

    def to_equity(ctx: Context) -> None:
        primary: ValuationOutput = ctx["primary"]
        if inp.method is Method.FCFF:
            ctx["equity_b"] = bridge_mod.to_equity(
                primary, inp.bridge, inp.leases, inp.shares.instruments, ctx["trace"]
            )
        else:
            ctx["equity_b"] = primary  # FCFE: equity directly, never bridged (D19)

    def roll(ctx: Context) -> None:
        ctx["equity_v"] = rollforward.roll_forward(
            ctx["equity_b"],
            inp.balance_sheet_date,
            inp.valuation_date,
            inp.cost_of_equity,
            inp.dividends,
            inp.shares.basic,
            ctx["report"],
            ctx["trace"],
        )

    def per_share(ctx: Context) -> None:
        ctx["dilution"] = dilution.to_per_share(
            ctx["equity_v"], inp.shares, ctx["report"], ctx["trace"]
        )

    return [
        _module("classification", set(), {"gate"}, gate),
        _module("forecast", {"gate"}, {"forecast"}, project),
        _module(
            "flows",
            {"forecast"},
            {"stream", "incomes", "rates", "t_stub", "ends", "times"},
            build_flows,
        ),
        _module(
            "terminal", {"forecast", "stream", "incomes", "rates", "ends"}, {"terminal"}, terminal
        ),
        _module(
            "discounting",
            {"stream", "times", "rates", "ends", "terminal"},
            {"pvs", "pv_tv", "primary"},
            discount,
        ),
        _module("bridge", {"primary"}, {"equity_b"}, to_equity),
        _module("rollforward", {"equity_b"}, {"equity_v"}, roll),
        _module("dilution", {"equity_v"}, {"dilution"}, per_share),
    ]


@calc
def run_valuation(inp: ValuationInputs) -> ValuationResult:
    report, trace = ValidationReport(), Trace()
    ctx: Context = {"report": report, "trace": trace}
    order = execute(_modules(inp), ctx)
    if ctx["equity_v"].value < 0:
        report.add("negative_equity_value", Severity.INFO, "Equity value is negative (shown).")
    pvs = ctx["pvs"]
    dil: dilution.DilutionResult = ctx["dilution"]
    result_core = {
        "primary": ctx["primary"].value,
        "equity_b": ctx["equity_b"].value,
        "equity_v": ctx["equity_v"].value,
        "per_share": dil.price.value,
        "diluted_shares": dil.diluted_shares,
        "pv_tv": ctx["pv_tv"],
        "flows": list(ctx["stream"]),
    }
    return ValuationResult(
        method=inp.method,
        primary=ctx["primary"],
        equity_b=ctx["equity_b"],
        equity_v=ctx["equity_v"],
        per_share=dil.price,
        flows=tuple(ctx["stream"]),
        times=tuple(ctx["times"]),
        discount_factors=tuple(df for df, _ in pvs),
        present_values=tuple(pv for _, pv in pvs),
        sum_pv_flows=sum((pv for _, pv in pvs), Decimal(0)),
        terminal=ctx["terminal"],
        pv_terminal=ctx["pv_tv"],
        diluted_shares=dil.diluted_shares,
        trace=trace.to_dict(),
        validation=report,
        engine_version=ENGINE_VERSION,
        module_versions=dict(MODULE_VERSIONS),
        module_order=tuple(m.name for m in order),
        input_hash=digest(inp),
        output_hash=digest(
            {
                "result": result_core,
                "trace": trace.to_dict(),
                "engine": ENGINE_VERSION,
                "modules": MODULE_VERSIONS,
            }
        ),
    )
