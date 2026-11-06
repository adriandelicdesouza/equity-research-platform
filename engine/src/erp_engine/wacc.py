"""Cost of capital (methodology §7, D21).

Separate from the DCF orchestration: it produces the WACC / Ke that a valuation consumes,
with every rule from §7 enforced as an error or warning.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ONE, ZERO, calc
from erp_engine.core.validation import Severity, ValidationReport


class ErpBasis(StrEnum):
    MATURE_PLUS_CRP = "mature_plus_crp"
    TOTAL_COUNTRY_ERP = "total_country_erp"


class CrpMethod(StrEnum):
    LAMBDA = "lambda"
    BETA = "beta"
    FLAT = "flat"


class Relevering(StrEnum):
    HAMADA = "hamada"
    HARRIS_PRINGLE = "harris_pringle"


class Frequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"


MIN_OBSERVATIONS = {Frequency.WEEKLY: 80, Frequency.MONTHLY: 48}
LOW_CRP_COUNTRIES = {"US", "CA"}


@dataclass(frozen=True)
class CountryExposure:
    country: str
    weight: Decimal
    crp: Decimal


@dataclass(frozen=True)
class CrpExposure:
    method: CrpMethod
    countries: tuple[CountryExposure, ...]
    lambda_: Decimal | None = None
    reason: str | None = None  # required for non-zero CRP on US/CA


@dataclass(frozen=True)
class EquityInputs:
    risk_free_rate: Decimal
    rf_currency: str
    cash_flow_currency: str
    beta_levered: Decimal
    erp_basis: ErpBasis
    mature_erp: Decimal | None = None
    total_country_erp: Decimal | None = None
    crp_exposures: tuple[CrpExposure, ...] = ()
    specific_premium: Decimal = ZERO
    specific_premium_rationale: str | None = None
    rf_includes_default_spread: bool = False
    rf_default_spread_removed: bool = False


@dataclass(frozen=True)
class CostOfEquity:
    value: Decimal
    crp_term: Decimal
    erp_used: Decimal


@calc
def cost_of_equity(inp: EquityInputs, report: ValidationReport) -> CostOfEquity:
    if inp.rf_currency != inp.cash_flow_currency:
        raise EngineError(
            "currency_mismatch", "Risk-free rate must be in the currency of the cash flows."
        )
    if len(inp.crp_exposures) > 1:
        raise EngineError("crp_multiple_methods", "Only one CRP exposure method may be active.")
    crp_term = ZERO
    if inp.crp_exposures:
        exp = inp.crp_exposures[0]
        total_w = sum((c.weight for c in exp.countries), ZERO)
        if abs(total_w - ONE) > Decimal("1e-9"):
            raise EngineError("crp_weights", f"CRP country weights sum to {total_w}, not 1.")
        crp = sum((c.weight * c.crp for c in exp.countries), ZERO)
        if (
            crp != 0
            and any(c.country in LOW_CRP_COUNTRIES and c.crp != 0 for c in exp.countries)
            and not (exp.reason or "").strip()
        ):
            raise EngineError("crp_reason_required", "Non-zero CRP for US/CA requires a reason.")
        if crp != 0 and inp.rf_includes_default_spread and not inp.rf_default_spread_removed:
            raise EngineError(
                "crp_with_default_spread_rf",
                "Risk-free rate includes a sovereign default spread; remove it "
                "before adding a CRP.",
            )
        if exp.method is CrpMethod.LAMBDA:
            if exp.lambda_ is None:
                raise EngineError("crp_lambda_missing", "Lambda method requires λ.")
            crp_term = exp.lambda_ * crp
        elif exp.method is CrpMethod.BETA:
            crp_term = inp.beta_levered * crp
        else:
            crp_term = crp
    if inp.erp_basis is ErpBasis.TOTAL_COUNTRY_ERP:
        if inp.total_country_erp is None:
            raise EngineError("erp_missing", "Total country ERP not provided.")
        if crp_term != 0:
            raise EngineError("crp_double_count", "CRP is already embedded in a total country ERP.")
        erp = inp.total_country_erp
    else:
        if inp.mature_erp is None:
            raise EngineError("erp_missing", "Mature-market ERP not provided.")
        erp = inp.mature_erp
    if inp.specific_premium != 0 and not (inp.specific_premium_rationale or "").strip():
        raise EngineError(
            "specific_premium_rationale", "A specific premium requires a written rationale."
        )
    if inp.specific_premium != 0:
        report.add(
            "specific_premium_applied",
            Severity.INFO,
            "Company-specific premium included.",
            premium=inp.specific_premium,
        )
    value = inp.risk_free_rate + inp.beta_levered * erp + crp_term + inp.specific_premium
    return CostOfEquity(value, crp_term, erp)


# ---- Beta -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegressionBeta:
    beta: Decimal
    standard_error: Decimal
    r_squared: Decimal
    ci_low: Decimal
    ci_high: Decimal
    observations: int


def sample_returns(
    prices: dict[date, Decimal], frequency: Frequency
) -> dict[tuple[int, int], Decimal]:
    """Simple returns between the last trading days of consecutive weeks/months."""
    last: OrderedDict[tuple[int, int], Decimal] = OrderedDict()
    for d in sorted(prices):
        key = (
            (d.isocalendar()[0], d.isocalendar()[1])
            if frequency is Frequency.WEEKLY
            else (d.year, d.month)
        )
        last[key] = prices[d]
    keys = list(last)
    return {k: last[k] / last[p] - 1 for p, k in pairwise(keys)}


def _t_975(df: int) -> Decimal:
    """Student-t 97.5% quantile via the Cornish–Fisher expansion (|error| < 1e-5 for df ≥ 40)."""
    z = Decimal("1.959963984540054")
    v = Decimal(df)
    return (
        z
        + (z**3 + z) / (4 * v)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * v**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * v**3)
    )


@calc
def regression_beta(
    stock_prices: dict[date, Decimal],
    market_prices: dict[date, Decimal],
    frequency: Frequency,
    report: ValidationReport,
) -> RegressionBeta:
    rs, rm = sample_returns(stock_prices, frequency), sample_returns(market_prices, frequency)
    common = sorted(set(rs) & set(rm))
    n = len(common)
    if n < MIN_OBSERVATIONS[frequency]:
        raise EngineError(
            "beta_insufficient_observations",
            f"{n} observations; {frequency.value} beta needs {MIN_OBSERVATIONS[frequency]}.",
        )
    xs, ys = [rm[k] for k in common], [rs[k] for k in common]
    mx, my = sum(xs, ZERO) / n, sum(ys, ZERO) / n
    sxx = sum(((x - mx) ** 2 for x in xs), ZERO)
    sxy = sum(((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)), ZERO)
    beta = sxy / sxx
    alpha = my - beta * mx
    sse = sum(((y - alpha - beta * x) ** 2 for x, y in zip(xs, ys, strict=True)), ZERO)
    sst = sum(((y - my) ** 2 for y in ys), ZERO)
    se = ((sse / (n - 2)) / sxx).sqrt()
    r2 = ONE - sse / sst if sst != 0 else ZERO
    t = _t_975(n - 2)
    if se > Decimal("0.25") or r2 < Decimal("0.10"):
        report.add(
            "beta_statistically_weak",
            Severity.WARNING,
            "Regression beta is statistically weak; consider bottom-up beta.",
            standard_error=se,
            r_squared=r2,
        )
    return RegressionBeta(beta, se, r2, beta - t * se, beta + t * se, n)


def blume(beta_raw: Decimal) -> Decimal:
    return Decimal("0.67") * beta_raw + Decimal("0.33")


def unlever(beta_l: Decimal, de: Decimal, tax: Decimal, formula: Relevering) -> Decimal:
    factor = ONE + (ONE - tax) * de if formula is Relevering.HAMADA else ONE + de
    return beta_l / factor


def relever(beta_u: Decimal, de: Decimal, tax: Decimal, formula: Relevering) -> Decimal:
    factor = ONE + (ONE - tax) * de if formula is Relevering.HAMADA else ONE + de
    return beta_u * factor


@calc
def relever_regression_beta(
    beta_regression: Decimal,
    window_average_de: Decimal,
    target_de: Decimal,
    tax: Decimal,
    formula: Relevering = Relevering.HAMADA,
) -> Decimal:
    """Unlever at the window's average D/E, then relever at the target (same formula)."""
    return relever(
        unlever(beta_regression, window_average_de, tax, formula), target_de, tax, formula
    )


# ---- Cost of debt and WACC --------------------------------------------------------------


def synthetic_spread(coverage: Decimal, table: tuple[tuple[Decimal, Decimal], ...]) -> Decimal:
    """``table``: (minimum coverage, spread) rows in ascending order of minimum coverage;
    the last row whose minimum the coverage meets applies."""
    if any(table[i][0] >= table[i + 1][0] for i in range(len(table) - 1)):
        raise EngineError("spread_table_order", "Spread table must ascend by minimum coverage.")
    eligible = [spread for minimum, spread in table if coverage >= minimum]
    if not eligible:
        raise EngineError("coverage_below_table", "Interest coverage is below the spread table.")
    return eligible[-1]


@dataclass(frozen=True)
class WaccResult:
    wacc: Decimal
    weight_equity: Decimal
    weight_debt: Decimal
    weight_preferred: Decimal
    after_tax_cost_of_debt: Decimal


@calc
def weighted_average_cost_of_capital(
    *,
    cost_of_equity_value: Decimal,
    pre_tax_cost_of_debt: Decimal,
    marginal_tax_rate: Decimal,
    equity_value: Decimal,
    debt_value: Decimal,
    preferred_value: Decimal = ZERO,
    cost_of_preferred: Decimal = ZERO,
    lease_liabilities: Decimal = ZERO,
    leases_as_debt: bool = False,
) -> WaccResult:
    debt = debt_value + (lease_liabilities if leases_as_debt else ZERO)
    total = equity_value + debt + preferred_value
    if equity_value <= 0 or total <= 0:
        raise EngineError("invalid_capital_structure", "Equity and total capital must be > 0.")
    after_tax = pre_tax_cost_of_debt * (ONE - marginal_tax_rate)
    we, wd, wp = equity_value / total, debt / total, preferred_value / total
    return WaccResult(
        we * cost_of_equity_value + wd * after_tax + wp * cost_of_preferred, we, wd, wp, after_tax
    )
