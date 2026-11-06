"""Cost of capital (methodology §7)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest
from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import make_context
from erp_engine.core.validation import ValidationReport
from erp_engine.wacc import (
    CountryExposure,
    CrpExposure,
    CrpMethod,
    EquityInputs,
    ErpBasis,
    Frequency,
    Relevering,
    _t_975,
    blume,
    cost_of_equity,
    regression_beta,
    relever,
    relever_regression_beta,
    synthetic_spread,
    unlever,
    weighted_average_cost_of_capital,
)

D = Decimal
BASE = EquityInputs(
    risk_free_rate=D("0.04"),
    rf_currency="USD",
    cash_flow_currency="USD",
    beta_levered=D("1.2"),
    erp_basis=ErpBasis.MATURE_PLUS_CRP,
    mature_erp=D("0.05"),
)


def ke(**kw: object) -> Decimal:
    from dataclasses import replace

    return cost_of_equity(replace(BASE, **kw), ValidationReport()).value  # type: ignore[arg-type]


def test_capm_without_crp() -> None:
    assert ke() == D("0.04") + D("1.2") * D("0.05")


@pytest.mark.parametrize(
    ("method", "expected_term"),
    [
        (CrpMethod.LAMBDA, D("0.5") * D("0.03")),
        (CrpMethod.BETA, D("1.2") * D("0.03")),
        (CrpMethod.FLAT, D("0.03")),
    ],
)
def test_crp_exposure_methods(method: CrpMethod, expected_term: Decimal) -> None:
    exp = CrpExposure(method, (CountryExposure("BR", D(1), D("0.03")),), lambda_=D("0.5"))
    assert ke(crp_exposures=(exp,)) == D("0.04") + D("0.06") + expected_term


def test_revenue_weighted_crp() -> None:
    exp = CrpExposure(
        CrpMethod.FLAT,
        (CountryExposure("BR", D("0.4"), D("0.03")), CountryExposure("MX", D("0.6"), D("0.02"))),
    )
    assert ke(crp_exposures=(exp,)) == D("0.10") + D("0.4") * D("0.03") + D("0.6") * D("0.02")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"rf_currency": "CAD"}, "currency_mismatch"),
        (
            {
                "erp_basis": ErpBasis.TOTAL_COUNTRY_ERP,
                "total_country_erp": D("0.08"),
                "crp_exposures": (
                    CrpExposure(CrpMethod.FLAT, (CountryExposure("BR", D(1), D("0.03")),)),
                ),
            },
            "crp_double_count",
        ),
        (
            {"crp_exposures": (CrpExposure(CrpMethod.FLAT, ()), CrpExposure(CrpMethod.BETA, ()))},
            "crp_multiple_methods",
        ),
        (
            {
                "crp_exposures": (
                    CrpExposure(CrpMethod.FLAT, (CountryExposure("BR", D("0.9"), D("0.03")),)),
                )
            },
            "crp_weights",
        ),
        (
            {
                "crp_exposures": (
                    CrpExposure(CrpMethod.FLAT, (CountryExposure("US", D(1), D("0.01")),)),
                )
            },
            "crp_reason_required",
        ),
        (
            {
                "rf_includes_default_spread": True,
                "crp_exposures": (
                    CrpExposure(CrpMethod.FLAT, (CountryExposure("BR", D(1), D("0.03")),)),
                ),
            },
            "crp_with_default_spread_rf",
        ),
        ({"specific_premium": D("0.02")}, "specific_premium_rationale"),
    ],
)
def test_cost_of_equity_guards(kwargs: dict[str, object], code: str) -> None:
    with pytest.raises(EngineError) as exc:
        ke(**kwargs)
    assert exc.value.code == code


def test_total_country_erp_basis_without_crp() -> None:
    assert ke(erp_basis=ErpBasis.TOTAL_COUNTRY_ERP, total_country_erp=D("0.07")) == (
        D("0.04") + D("1.2") * D("0.07")
    )


def _series(n: int, f) -> tuple[dict[date, Decimal], dict[date, Decimal]]:  # type: ignore[no-untyped-def]
    start = date(2022, 1, 7)  # a Friday
    market, stock = {start: D(100)}, {start: D(50)}
    m, s = D(100), D(50)
    for i in range(1, n + 1):
        rm = D((i * 37) % 11 - 5) / 100
        m, s = m * (1 + rm), s * (1 + f(rm, i))
        day = start + timedelta(weeks=i)
        market[day], stock[day] = m, s
    return stock, market


def test_regression_beta_recovers_exact_relationship() -> None:
    stock, market = _series(104, lambda rm, i: D("1.5") * rm)
    r = regression_beta(stock, market, Frequency.WEEKLY, ValidationReport())
    assert abs(r.beta - D("1.5")) < D("1e-25")
    assert r.standard_error < D("1e-12") and abs(r.r_squared - 1) < D("1e-20")
    assert r.observations == 104


def test_regression_beta_with_noise_and_diagnostics() -> None:
    stock, market = _series(104, lambda rm, i: D("0.8") * rm + D((i * 13) % 7 - 3) / 400)
    report = ValidationReport()
    r = regression_beta(stock, market, Frequency.WEEKLY, report)
    assert D("0.6") < r.beta < D("1.0") and r.ci_low < r.beta < r.ci_high


def test_regression_beta_needs_enough_observations() -> None:
    stock, market = _series(50, lambda rm, i: rm)
    with pytest.raises(EngineError, match="beta_insufficient_observations"):
        regression_beta(stock, market, Frequency.WEEKLY, ValidationReport())


def test_t_quantile_accuracy() -> None:
    for df, t in ((46, "2.012896"), (60, "2.000298"), (100, "1.983972")):
        assert abs(_t_975(df) - D(t)) < D("2e-5")


def test_blume_and_relevering() -> None:
    assert blume(D("1.3")) == D("1.201")
    for f in Relevering:
        bu = unlever(D("1.2"), D("0.5"), D("0.25"), f)
        assert relever(bu, D("0.5"), D("0.25"), f) == D("1.2")
    assert relever_regression_beta(D("1.2"), D("0.5"), D("0.5"), D("0.25")) == D("1.2")
    assert relever_regression_beta(D("1.2"), D("0.5"), D("1.0"), D("0.25")) > D("1.2")


def test_synthetic_spread_lookup() -> None:
    table = ((D("0.5"), D("0.10")), (D(2), D("0.04")), (D(8), D("0.01")))
    assert synthetic_spread(D(3), table) == D("0.04")
    with pytest.raises(EngineError, match="coverage_below_table"):
        synthetic_spread(D("0.1"), table)
    with pytest.raises(EngineError, match="spread_table_order"):
        synthetic_spread(D(3), tuple(reversed(table)))


def test_wacc_weights_and_lease_inclusion() -> None:
    r = weighted_average_cost_of_capital(
        cost_of_equity_value=D("0.10"),
        pre_tax_cost_of_debt=D("0.06"),
        marginal_tax_rate=D("0.25"),
        equity_value=D(750),
        debt_value=D(250),
    )
    assert r.wacc == D("0.75") * D("0.10") + D("0.25") * D("0.045")
    with_leases = weighted_average_cost_of_capital(
        cost_of_equity_value=D("0.10"),
        pre_tax_cost_of_debt=D("0.06"),
        marginal_tax_rate=D("0.25"),
        equity_value=D(750),
        debt_value=D(250),
        lease_liabilities=D(100),
        leases_as_debt=True,
    )
    with localcontext(make_context()):
        assert with_leases.weight_debt == D(350) / D(1100)
