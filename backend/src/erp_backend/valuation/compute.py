"""Input document → engine → outputs (design §4, §6). Pure: no database, clock or I/O.

The same function serves previews, saved runs and reproduction, so a stored run is re-executed
from its immutable input document only (approved qualification 4): nothing here can read
current market data or newer reference versions.

All arithmetic is the engine's (``erp_engine.wacc``, ``erp_engine.api.run_valuation``) or runs
inside the engine's decimal context (precision 34, ROUND_HALF_EVEN): currency conversion of the
market price, WACC weights and the upside. Every backend step is recorded as a trace node next
to the engine's own trace.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from erp_engine import wacc as wacc_mod
from erp_engine.api import ValuationResult, run_valuation
from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import make_context
from erp_engine.core.timeline import Convention
from erp_engine.core.validation import ValidationReport
from erp_engine.inputs import (
    Bridge,
    Claim,
    Classification,
    ExitMetric,
    Financing,
    Instrument,
    InstrumentKind,
    InstrumentTreatment,
    Leases,
    LeaseStandard,
    LeaseTreatment,
    Method,
    Operating,
    ShareAdjustment,
    Shares,
    Terminal,
    TerminalForm,
    TerminalTiming,
    ValuationInputs,
    YearDrivers,
)
from erp_engine.outputs import OutputKind
from erp_engine.version import ENGINE_VERSION, MODULE_VERSIONS

from erp_backend.valuation.canonical import jsonable, sha256_of
from erp_backend.valuation.errors import ValuationRefused

METHODS: dict[str, dict[str, str]] = {
    "dcf_fcff": {"version": "1.0.0", "produces": OutputKind.ENTERPRISE_VALUE.value},
    "dcf_fcfe": {"version": "1.0.0", "produces": OutputKind.EQUITY_VALUE.value},
}
LEASES = {
    "none": (LeaseStandard.NONE, LeaseTreatment.AS_DEBT),
    "ifrs16_as_debt": (LeaseStandard.IFRS16, LeaseTreatment.AS_DEBT),
    "asc842_as_operating": (LeaseStandard.ASC842, LeaseTreatment.AS_OPERATING),
}
ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class Computation:
    outputs: dict[str, Any]
    trace: dict[str, Any]
    validation: list[dict[str, Any]]
    engine_input_sha256: str
    output_sha256: str
    primary_value: Decimal
    per_share_value: Decimal
    blocks_save: list[dict[str, Any]]


class _Doc:
    """Typed access to an input document's ``inputs`` map."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.doc = document
        self.inputs: dict[str, dict[str, Any]] = document["inputs"]

    def has(self, key: str) -> bool:
        return key in self.inputs

    def dec(self, key: str, default: Decimal | None = None) -> Decimal:
        item = self.inputs.get(key)
        if item is None or item.get("value") is None:
            if default is not None:
                return default
            raise ValuationRefused("input_missing", f"Input {key} is missing.", [{"input": key}])
        return Decimal(item["value"])

    def opt(self, key: str) -> Decimal | None:
        item = self.inputs.get(key)
        return None if item is None or item.get("value") is None else Decimal(item["value"])

    def choice(self, key: str, default: str | None = None) -> str:
        item = self.inputs.get(key)
        if item is None:
            if default is None:
                raise ValuationRefused(
                    "input_missing", f"Input {key} is missing.", [{"input": key}]
                )
            return default
        return str(item["json"])

    def json(self, key: str) -> Any:
        item = self.inputs.get(key)
        return None if item is None else item.get("json")

    def a(self, key: str) -> str:
        return f"assumption:{key}"


def _trace(
    nodes: dict[str, Any],
    node_id: str,
    label: str,
    value: Any,
    rule: str,
    inputs: list[str],
    unit: str = "ratio",
) -> None:
    nodes[node_id] = {
        "label": label,
        "value": jsonable(value) if value is not None else None,
        "unit": unit,
        "rule": rule,
        "inputs": inputs,
        "module": "backend.valuation",
    }


def _year_drivers(d: _Doc, horizon: int) -> tuple[YearDrivers, ...]:
    return tuple(
        YearDrivers(
            growth=d.dec(f"assumption:revenue_growth[{k}]"),
            ebit_margin=d.dec(f"assumption:ebit_margin[{k}]"),
            tax_rate=d.dec(f"assumption:tax_rate[{k}]"),
            da_pct=d.dec(f"assumption:da_pct[{k}]"),
            capex_pct=d.dec(f"assumption:capex_pct[{k}]"),
            nwc_pct=d.dec(f"assumption:nwc_pct[{k}]"),
        )
        for k in range(1, horizon + 1)
    )


def _beta(d: _Doc, nodes: dict[str, Any]) -> Decimal:
    method = d.choice(d.a("beta_method"))
    if method == "analyst_levered":
        beta = d.dec(d.a("beta_levered"))
        _trace(
            nodes,
            "wacc.beta_levered",
            "Levered beta (analyst)",
            beta,
            "Analyst-entered levered beta",
            [d.a("beta_levered")],
            "number",
        )
        return beta
    formula = wacc_mod.Relevering(d.choice(d.a("relevering")))
    beta_u = d.dec(d.a("beta_unlevered"))
    de = d.dec(d.a("target_debt_to_equity"))
    tax = d.dec(d.a("marginal_tax_rate"))
    beta = wacc_mod.relever(beta_u, de, tax, formula)
    rule = (
        "βU × [1 + (1 − t) × D/E] (Hamada)"
        if formula is wacc_mod.Relevering.HAMADA
        else "βU × (1 + D/E) (Harris–Pringle)"
    )
    _trace(
        nodes,
        "wacc.beta_levered",
        "Levered beta (bottom-up, relevered)",
        beta,
        rule,
        [d.a("beta_unlevered"), d.a("target_debt_to_equity"), d.a("marginal_tax_rate")],
        "number",
    )
    return beta


def _cost_of_equity(
    d: _Doc, beta: Decimal, currency: str, report: ValidationReport, nodes: dict[str, Any]
) -> tuple[wacc_mod.CostOfEquity, Decimal, str]:
    rf = d.dec(d.a("risk_free_rate"))
    rf_currency = d.inputs[d.a("risk_free_rate")].get("currency") or currency
    exposures: tuple[wacc_mod.CrpExposure, ...] = ()
    if d.has(d.a("country_risk_premium")):
        exposures = (
            wacc_mod.CrpExposure(
                method=wacc_mod.CrpMethod(d.choice(d.a("crp_method"))),
                countries=(
                    wacc_mod.CountryExposure(
                        country=str(d.json(d.a("crp_country"))),
                        weight=ONE,
                        crp=d.dec(d.a("country_risk_premium")),
                    ),
                ),
                lambda_=d.opt(d.a("crp_lambda")),
                reason=d.json(d.a("crp_reason")),
            ),
        )
    inputs = wacc_mod.EquityInputs(
        risk_free_rate=rf,
        rf_currency=rf_currency,
        cash_flow_currency=currency,
        beta_levered=beta,
        erp_basis=wacc_mod.ErpBasis(d.choice(d.a("erp_basis"))),
        mature_erp=d.opt(d.a("mature_market_erp")),
        total_country_erp=d.opt(d.a("total_country_erp")),
        crp_exposures=exposures,
        specific_premium=d.opt(d.a("specific_premium")) or ZERO,
        specific_premium_rationale=d.json(d.a("specific_premium_rationale")),
    )
    with localcontext(make_context()):
        ke = wacc_mod.cost_of_equity(inputs, report)
    _trace(
        nodes,
        "wacc.cost_of_equity",
        "Cost of equity",
        ke.value,
        "Rf + βL × ERP + CRP term + specific premium",
        [
            "wacc.beta_levered",
            d.a("risk_free_rate"),
            d.a("mature_market_erp"),
            d.a("total_country_erp"),
            d.a("country_risk_premium"),
            d.a("specific_premium"),
        ],
    )
    return ke, rf, rf_currency


def _cost_of_debt(d: _Doc, rf: Decimal, nodes: dict[str, Any]) -> Decimal:
    if d.choice(d.a("cost_of_debt_method")) == "direct":
        kd = d.dec(d.a("pre_tax_cost_of_debt"))
        rule, ins = "Analyst-entered pre-tax cost of debt", [d.a("pre_tax_cost_of_debt")]
    else:
        with localcontext(make_context()):
            kd = rf + d.dec(d.a("credit_spread"))
        rule, ins = "Rf + credit spread", [d.a("risk_free_rate"), d.a("credit_spread")]
    _trace(nodes, "wacc.pre_tax_cost_of_debt", "Pre-tax cost of debt", kd, rule, ins)
    return kd


def _price(d: _Doc, nodes: dict[str, Any]) -> Decimal | None:
    """Market price converted to the reporting currency (exact reciprocal allowed, Q5)."""
    if not d.has("market:price"):
        return None
    close = d.dec("market:price")
    if not d.has("market:fx"):
        _trace(
            nodes,
            "market.price_reporting",
            "Market price (reporting currency)",
            close,
            "Close as stored (same currency)",
            ["market:price"],
            "currency",
        )
        return close
    fx = d.inputs["market:fx"]
    rate = Decimal(fx["source_value"])
    with localcontext(make_context()):
        factor = rate if fx["conversion"] == "direct" else ONE / rate
        price = close * factor
    if fx["conversion"] == "reciprocal":
        _trace(
            nodes,
            "market.fx_reciprocal",
            "Reciprocal of the published FX rate",
            factor,
            f"1 / {fx['source_value']} (published {fx['published_direction']}); exact "
            "Decimal division, precision 34, ROUND_HALF_EVEN; the stored rate is unchanged",
            ["market:fx"],
            "ratio",
        )
    _trace(
        nodes,
        "market.price_reporting",
        "Market price (reporting currency)",
        price,
        "Close × FX factor",
        ["market:price", "market:fx"],
        "currency",
    )
    return price


def _wacc(
    d: _Doc, ke: Decimal, kd: Decimal, price: Decimal | None, nodes: dict[str, Any]
) -> wacc_mod.WaccResult:
    tax = d.dec(d.a("marginal_tax_rate"))
    leases_as_debt = d.choice(d.a("lease_treatment")) == "ifrs16_as_debt"
    if d.choice(d.a("capital_structure_basis")) == "target":
        equity, debt, preferred = ONE, d.dec(d.a("target_debt_to_equity")), ZERO
        lease, rule = ZERO, "Target weights: E = 1, D = target D/E"
    else:
        if price is None:
            raise ValuationRefused(
                "input_missing", "Market-value weights need a market price.", [{"input": "price"}]
            )
        preferred = d.dec("dataset:bridge.preferred")
        if preferred != 0:
            raise ValuationRefused(
                "unsupported_capital_structure",
                "Market-value weights with preferred equity need a cost of preferred, which "
                "Phase 5 does not model; use target weights.",
            )
        with localcontext(make_context()):
            equity = price * d.dec("dataset:shares.basic")
        debt = d.dec("dataset:bridge.debt")
        lease = d.dec("dataset:bridge.lease_liabilities", ZERO) if leases_as_debt else ZERO
        rule = "Market weights: E = price × basic shares at B; D = book debt (proxy)"
    result = wacc_mod.weighted_average_cost_of_capital(
        cost_of_equity_value=ke,
        pre_tax_cost_of_debt=kd,
        marginal_tax_rate=tax,
        equity_value=equity,
        debt_value=debt,
        preferred_value=preferred,
        lease_liabilities=lease,
        leases_as_debt=leases_as_debt,
    )
    _trace(
        nodes,
        "wacc.weights",
        "Capital weights (E, D)",
        {"equity": result.weight_equity, "debt": result.weight_debt},
        rule,
        [d.a("capital_structure_basis"), "market.price_reporting", "dataset:bridge.debt"],
    )
    _trace(
        nodes,
        "wacc.wacc",
        "WACC",
        result.wacc,
        "E/V × Ke + D/V × Kd × (1 − t)",
        [
            "wacc.cost_of_equity",
            "wacc.pre_tax_cost_of_debt",
            "wacc.weights",
            d.a("marginal_tax_rate"),
        ],
    )
    return result


def _shares(d: _Doc, b: date) -> Shares:
    instruments = tuple(
        Instrument(
            id=i["id"],
            kind=InstrumentKind(i["kind"]),
            count=Decimal(i["count"]),
            treatment=InstrumentTreatment(i["treatment"]),
            strike=Decimal(i["strike"]) if "strike" in i else None,
            face_value=Decimal(i["face_value"]) if "face_value" in i else None,
            exclusion_reason=i.get("exclusion_reason"),
        )
        for i in d.json(d.a("instruments")) or []
    )
    adjustments = tuple(
        ShareAdjustment(date.fromisoformat(a["date"]), Decimal(a["shares"]), a["source"])
        for a in d.json(d.a("share_adjustments")) or []
    )
    return Shares(
        basic=d.dec("dataset:shares.basic"),
        base_date=b,
        adjustments=adjustments,
        instruments=instruments,
    )


def engine_inputs(
    document: dict[str, Any], nodes: dict[str, Any], discount_rate: Decimal | None = None
) -> tuple[ValuationInputs, dict[str, Any]]:
    """Map an input document to the engine's typed inputs; returns them with the WACC build-up.

    ``discount_rate`` (sensitivities only) replaces the built-up discount rate — WACC for FCFF,
    the cost of equity for FCFE — and is recorded as its own trace node."""
    d = _Doc(document)
    methodology = document["methodology"]
    currency = document["currency"]
    b = date.fromisoformat(document["balance_sheet_period"]["end_date"])
    v = date.fromisoformat(document["valuation_date"])
    horizon = int(d.dec(d.a("horizon_years")))
    report = ValidationReport()
    beta = _beta(d, nodes)
    ke, rf, rf_currency = _cost_of_equity(d, beta, currency, report, nodes)
    kd = _cost_of_debt(d, rf, nodes)
    price = _price(d, nodes)
    build: dict[str, Any] = {
        "risk_free_rate": rf,
        "beta_levered": beta,
        "erp_used": ke.erp_used,
        "crp_term": ke.crp_term,
        "cost_of_equity": ke.value,
        "pre_tax_cost_of_debt": kd,
        "marginal_tax_rate": d.dec(d.a("marginal_tax_rate")),
        "checks": [
            {"code": c.code, "severity": c.severity.value, "message": c.message}
            for c in report.checks
        ],
    }
    terminal = Terminal(
        form=TerminalForm(d.choice(d.a("terminal_form"))),
        g=d.dec(d.a("terminal_growth")),
        ronic=d.opt(d.a("ronic")),
        exit_multiple=d.opt(d.a("exit_multiple")),
        exit_metric=ExitMetric(d.choice(d.a("exit_metric"), "ebitda")),
        exit_lease_basis=d.json(d.a("exit_lease_basis")),
        timing=TerminalTiming(d.choice(d.a("terminal_timing"), "follow_convention")),
    )
    operating = Operating(
        revenue_0=d.dec("dataset:operating.revenue_0"),
        nwc_0=d.dec("dataset:operating.nwc_0"),
        years=_year_drivers(d, horizon),
    )
    common: dict[str, Any] = {
        "currency": currency,
        "balance_sheet_date": b,
        "valuation_date": v,
        "convention": Convention(d.choice(d.a("convention"))),
        "operating": operating,
        "terminal": terminal,
        "shares": _shares(d, b),
        "cost_of_equity": ke.value,
        "classification": Classification(document["classification"]),
        "risk_free_rate": rf,
        "rate_currency": rf_currency,
    }
    if discount_rate is not None and methodology == "dcf_fcfe":
        common["cost_of_equity"] = discount_rate
        build["cost_of_equity"] = discount_rate
        _trace(
            nodes,
            "sensitivity.discount_rate",
            "Cost of equity (sensitivity value)",
            discount_rate,
            "Replaced by the sensitivity grid; the build-up above is the run's",
            ["wacc.cost_of_equity"],
        )
    if methodology == "dcf_fcff":
        result = _wacc(d, ke.value, kd, price, nodes)
        if discount_rate is not None:
            _trace(
                nodes,
                "sensitivity.discount_rate",
                "WACC (sensitivity value)",
                discount_rate,
                "Replaced by the sensitivity grid; the build-up above is the run's",
                ["wacc.wacc"],
            )
            result = replace(result, wacc=discount_rate)
        build.update(
            wacc=result.wacc,
            weight_equity=result.weight_equity,
            weight_debt=result.weight_debt,
            after_tax_cost_of_debt=result.after_tax_cost_of_debt,
            capital_structure_basis=d.choice(d.a("capital_structure_basis")),
        )
        standard, treatment = LEASES[d.choice(d.a("lease_treatment"))]
        claims = tuple(
            Claim(c["id"], c["label"], Decimal(c["amount"]))
            for c in d.json(d.a("other_claims")) or []
        )
        inputs = ValuationInputs(
            method=Method.FCFF,
            discount_rates=(result.wacc,),
            bridge=Bridge(
                cash=d.dec("dataset:bridge.cash"),
                short_term_investments=d.dec("dataset:bridge.short_term_investments"),
                non_operating_assets=d.opt(d.a("non_operating_assets")) or ZERO,
                debt=d.dec("dataset:bridge.debt"),
                lease_liabilities=d.dec("dataset:bridge.lease_liabilities", ZERO),
                finance_lease_liabilities=d.dec("dataset:bridge.finance_lease_liabilities", ZERO),
                preferred=d.dec("dataset:bridge.preferred"),
                nci=d.dec("dataset:bridge.nci"),
                other_claims=claims,
            ),
            leases=Leases(standard=standard, treatment=treatment),
            **common,
        )
    else:
        debt = [d.dec("dataset:financing.debt_0")]
        cash = [d.dec("dataset:financing.cash_0")]
        for k in range(1, horizon + 1):
            debt.append(d.dec(f"assumption:debt_forecast[{k}]"))
            cash.append(d.dec(f"assumption:cash_forecast[{k}]"))
        inputs = ValuationInputs(
            method=Method.FCFE,
            financing=Financing(
                debt_path=tuple(debt),
                cash_path=tuple(cash),
                cost_of_debt=kd,
                cash_yield=d.dec(d.a("cash_yield")),
            ),
            **common,
        )
    build["price_reporting"] = price
    return inputs, build


def _flows_table(result: ValuationResult) -> list[dict[str, Any]]:
    return [
        {"year": k + 1, "flow": f, "time": t, "discount_factor": df, "present_value": pv}
        for k, (f, t, df, pv) in enumerate(
            zip(
                result.flows,
                result.times,
                result.discount_factors,
                result.present_values,
                strict=True,
            )
        )
    ]


def compute(document: dict[str, Any], discount_rate: Decimal | None = None) -> Computation:
    """Execute the methodology for an input document. Raises ``ValuationRefused`` with
    ``engine_refused`` when the engine rejects the inputs. ``discount_rate`` is for
    sensitivity cells only; runs, previews and reproduction never pass it."""
    nodes: dict[str, Any] = {}
    try:
        inputs, build = engine_inputs(document, nodes, discount_rate)
        result = run_valuation(inputs)
    except EngineError as exc:
        raise ValuationRefused("engine_refused", exc.message, [{"engine_code": exc.code}]) from exc
    price = build.pop("price_reporting")
    market: dict[str, Any] | None = None
    if price is not None:
        with localcontext(make_context()):
            upside = result.per_share.value / price - ONE if price != 0 else None
        market = {"price_reporting": price, "upside": upside}
        _trace(
            nodes,
            "market.upside",
            "Upside / downside",
            upside,
            "Implied price / market price − 1",
            [result.per_share.trace_id or "dilution", "market.price_reporting"],
        )
    t = result.terminal
    outputs: dict[str, Any] = {
        "methodology": document["methodology"],
        "produces": METHODS[document["methodology"]]["produces"],
        "currency": document["currency"],
        "primary": {
            "kind": result.primary.kind.value,
            "value": result.primary.value,
            "date": result.primary.valuation_date,
        },
        "equity_value_b": result.equity_b.value,
        "equity_value_v": result.equity_v.value,
        "per_share": result.per_share.value,
        "diluted_shares": result.diluted_shares,
        "sum_pv_flows": result.sum_pv_flows,
        "pv_terminal": result.pv_terminal,
        "terminal_share": result.terminal_share,
        "terminal": {
            "value": t.value,
            "time": t.time,
            "implied_exit_multiple": t.implied_exit_multiple,
            "implied_growth": t.implied_growth,
            "implied_ronic": t.implied_ronic,
            "terminal_flow": t.terminal_flow,
        },
        "flows": _flows_table(result),
        "cost_of_capital": build,
        "market": market,
        "engine": {
            "version": result.engine_version,
            "modules": result.module_versions,
            "module_order": list(result.module_order),
            "input_hash": result.input_hash,
            "output_hash": result.output_hash,
        },
    }
    validation = [
        {
            "code": c.code,
            "severity": c.severity.value,
            "message": c.message,
            "blocks_save": c.blocks_save,
            "details": dict(c.details),
        }
        for c in result.validation.checks
    ]
    trace = {**result.trace, **nodes}
    outputs_json = jsonable(outputs)
    trace_json = jsonable(trace)
    return Computation(
        outputs=outputs_json,
        trace=trace_json,
        validation=validation,
        engine_input_sha256=result.input_hash,
        output_sha256=sha256_of(
            {"outputs": outputs_json, "trace": trace_json, "validation": validation}
        ),
        primary_value=result.primary.value,
        per_share_value=result.per_share.value,
        blocks_save=[v for v in validation if v["blocks_save"]],
    )


def engine_versions() -> dict[str, Any]:
    return {"version": ENGINE_VERSION, "modules": dict(MODULE_VERSIONS)}
