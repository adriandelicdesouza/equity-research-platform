"""Policy guards (Blueprint §13.1): each rule has a failing-input test."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest
from engine_fixtures import load, to_inputs
from erp_engine import bridge, dilution
from erp_engine.api import run_valuation
from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import make_context
from erp_engine.core.trace import Trace
from erp_engine.core.validation import ValidationReport
from erp_engine.inputs import (
    Bridge,
    Claim,
    Classification,
    ExitMetric,
    Instrument,
    InstrumentKind,
    InstrumentTreatment,
    Leases,
    LeaseStandard,
    LeaseTreatment,
    LeaseYear,
    SbcTreatment,
    ShareAdjustment,
    Terminal,
    TerminalForm,
    TerminalTiming,
)
from erp_engine.outputs import OutputKind, ValuationOutput

B1 = to_inputs(load("B1"))
B2 = to_inputs(load("B2"))
B3 = to_inputs(load("B3"))


def raises(code: str, inputs: object) -> None:
    with pytest.raises(EngineError) as exc:
        run_valuation(inputs)  # type: ignore[arg-type]
    assert exc.value.code == code


# --- Output kinds (D18/D19) ---------------------------------------------------------------


def test_bridge_rejects_equity_value_inputs() -> None:
    fcfe_equity = ValuationOutput(
        OutputKind.EQUITY_VALUE, Decimal(1), "USD", date(2025, 1, 1), "dcf_fcfe"
    )
    with pytest.raises(EngineError, match="output_kind_mismatch"):
        bridge.to_equity(fcfe_equity, Bridge(), Leases(), (), Trace())


def test_dilution_rejects_enterprise_value_inputs() -> None:
    ev = ValuationOutput(
        OutputKind.ENTERPRISE_VALUE, Decimal(1), "USD", date(2025, 1, 1), "dcf_fcff"
    )
    with pytest.raises(EngineError, match="output_kind_mismatch"):
        dilution.to_per_share(ev, B1.shares, ValidationReport(), Trace())


def test_fcfe_value_ignores_bridge_inputs_entirely() -> None:
    base = run_valuation(B3)
    other = run_valuation(replace(B3, bridge=Bridge(cash=Decimal(9999), debt=Decimal(1))))
    assert base.equity_v.value == other.equity_v.value


# --- Terminal value ------------------------------------------------------------------------


def test_growth_at_or_above_rate_refused() -> None:
    raises(
        "terminal_growth_ge_rate",
        replace(B1, terminal=Terminal(TerminalForm.SIMPLE, Decimal("0.10"))),
    )


def test_value_driver_requires_ronic() -> None:
    raises(
        "ronic_required", replace(B1, terminal=Terminal(TerminalForm.VALUE_DRIVER, Decimal("0.02")))
    )


def test_exit_multiple_not_valid_for_fcfe() -> None:
    raises(
        "exit_multiple_requires_fcff",
        replace(
            B3, terminal=Terminal(TerminalForm.EXIT_MULTIPLE, Decimal(0), exit_multiple=Decimal(8))
        ),
    )


def test_exit_multiple_lease_basis_must_match() -> None:
    leases = Leases(LeaseStandard.IFRS16, LeaseTreatment.AS_DEBT)
    term = Terminal(
        TerminalForm.EXIT_MULTIPLE,
        Decimal(0),
        exit_multiple=Decimal(8),
        exit_metric=ExitMetric.EBITDA,
        exit_lease_basis="post_lease",
    )
    raises("lease_basis_mismatch", replace(B1, leases=leases, terminal=term))


def test_terminal_warnings() -> None:
    r = run_valuation(
        replace(
            B1,
            risk_free_rate=Decimal("0.01"),
            terminal=Terminal(TerminalForm.VALUE_DRIVER, Decimal("0.02"), ronic=Decimal("0.05")),
        )
    )
    codes = r.validation.codes()
    assert "terminal_growth_above_rf" in codes
    assert "terminal_value_destroying_growth" in codes


def test_end_of_period_override_is_recorded() -> None:
    r = run_valuation(
        replace(B2, terminal=replace(B2.terminal, timing=TerminalTiming.END_OF_PERIOD))
    )
    assert "terminal_timing_override" in r.validation.codes()
    with localcontext(make_context()):
        assert r.terminal.time == r.times[-1] + Decimal("0.5")


# --- Leases ---------------------------------------------------------------------------------


def test_lease_inputs_required_for_treatment() -> None:
    raises(
        "lease_inputs_missing",
        replace(
            B1,
            leases=Leases(LeaseStandard.ASC842, LeaseTreatment.AS_DEBT, years=(LeaseYear(),) * 3),
        ),
    )


def test_rou_additions_in_capex_rejected() -> None:
    raises("rou_double_count", replace(B1, leases=Leases(capex_includes_rou_additions=True)))


def test_lease_liabilities_without_standard_rejected() -> None:
    raises(
        "lease_standard_missing",
        replace(B1, bridge=replace(B1.bridge, lease_liabilities=Decimal(50))),
    )


# --- Dilution -------------------------------------------------------------------------------


def _with_instruments(*instruments: Instrument, **bridge_kw: object):  # type: ignore[no-untyped-def]
    return replace(
        B1,
        shares=replace(B1.shares, instruments=instruments),
        bridge=replace(B1.bridge, **bridge_kw),
    )


CONV = Instrument(
    "cb1",
    InstrumentKind.CONVERTIBLE_DEBT,
    Decimal(5),
    InstrumentTreatment.CLAIM,
    face_value=Decimal(100),
)


def test_instrument_in_bridge_and_register_rejected() -> None:
    raises(
        "instrument_double_count",
        _with_instruments(CONV, other_claims=(Claim("cb1", "Convertible", Decimal(100)),)),
    )


def test_duplicate_instrument_rejected() -> None:
    raises("instrument_double_count", _with_instruments(CONV, CONV))


def test_weighted_average_base_rejected() -> None:
    raises(
        "weighted_average_base",
        replace(B1, shares=replace(B1.shares, base_kind="weighted_average_diluted")),
    )


def test_share_adjustment_must_be_dated_after_base_and_sourced() -> None:
    bad = ShareAdjustment(date(2024, 1, 1), Decimal(-5), "buyback")
    raises("invalid_share_adjustment", replace(B1, shares=replace(B1.shares, adjustments=(bad,))))
    unsourced = ShareAdjustment(B1.valuation_date, Decimal(-5), " ")
    raises(
        "invalid_share_adjustment", replace(B1, shares=replace(B1.shares, adjustments=(unsourced,)))
    )


def test_exclusion_requires_reason_and_treatment_must_fit_kind() -> None:
    raises(
        "exclusion_reason_required",
        _with_instruments(
            Instrument(
                "o1",
                InstrumentKind.OPTION,
                Decimal(1),
                InstrumentTreatment.EXCLUDED,
                strike=Decimal(1),
            )
        ),
    )
    raises(
        "invalid_treatment",
        _with_instruments(
            Instrument(
                "o1",
                InstrumentKind.OPTION,
                Decimal(1),
                InstrumentTreatment.CLAIM,
                strike=Decimal(1),
            )
        ),
    )


def test_reported_diluted_share_sanity_check() -> None:
    r = run_valuation(
        replace(B1, shares=replace(B1.shares, reported_diluted_weighted=Decimal(120)))
    )
    assert "diluted_shares_vs_reported" in r.validation.codes()


def test_sbc_double_count_and_omission_flagged() -> None:
    double = replace(B1, operating=replace(B1.operating, future_dilution_modelled=True))
    omitted = replace(B1, operating=replace(B1.operating, sbc_treatment=SbcTreatment.ADD_BACK))
    for inputs, code in ((double, "sbc_double_count"), (omitted, "sbc_dilution_omitted")):
        r = run_valuation(inputs)
        assert code in r.validation.codes() and r.validation.blocks_save


# --- Stub, roll-forward, currency, classification ---------------------------------------------


def test_stale_balance_sheet_warning_and_valuation_before_b() -> None:
    late = run_valuation(replace(B2, valuation_date=date(2026, 3, 1), dividends=()))
    assert "stale_balance_sheet" in late.validation.codes()
    raises("valuation_before_balance_sheet", replace(B2, valuation_date=date(2025, 1, 1)))


def test_fcfe_with_stub_not_supported_in_r1() -> None:
    raises("fcfe_stub_not_supported", replace(B3, stub=B2.stub))


def test_currency_mismatch_rejected() -> None:
    raises("currency_mismatch", replace(B1, rate_currency="CAD"))


@pytest.mark.parametrize(
    "cls",
    [
        Classification.BANK,
        Classification.INSURER,
        Classification.SPECIALTY_LENDER,
        Classification.INVESTMENT_HOLDING,
    ],
)
def test_financial_institutions_blocked(cls: Classification) -> None:
    raises("dcf_not_permitted", replace(B1, classification=cls))


def test_reit_broker_warn_and_needs_review_blocks_save() -> None:
    assert (
        "reit_fair_value_gains"
        in run_valuation(replace(B1, classification=Classification.REIT)).validation.codes()
    )
    assert (
        "client_cash_excluded"
        in run_valuation(
            replace(B1, classification=Classification.ASSET_MANAGER_BROKER)
        ).validation.codes()
    )
    review = run_valuation(replace(B1, classification=Classification.NEEDS_REVIEW))
    assert review.validation.blocks_save


# --- Stub validation (methodology §5.3) ---------------------------------------------------


def test_stub_requires_balance_sheet_inside_fiscal_year() -> None:
    assert B2.stub is not None
    raises(
        "invalid_stub",
        replace(
            B2, balance_sheet_date=date(2026, 1, 15), valuation_date=date(2026, 1, 15), dividends=()
        ),
    )


def test_mode_b_requires_ytd_and_interim_nwc() -> None:
    assert B2.stub is not None
    raises("stub_inputs_missing", replace(B2, stub=replace(B2.stub, nwc_at_b=None)))


def test_mode_b_fiscal_year_must_match() -> None:
    assert B2.stub is not None
    raises(
        "stub_fiscal_year_mismatch",
        replace(B2, stub=replace(B2.stub, ytd_fiscal_year_end=date(2024, 12, 31))),
    )


def test_mode_b_low_remaining_revenue_warns() -> None:
    assert B2.stub is not None and B2.stub.ytd is not None
    heavy_ytd = replace(B2.stub.ytd, revenue=Decimal(1090))
    r = run_valuation(replace(B2, stub=replace(B2.stub, ytd=heavy_ytd)))
    assert "stub_remaining_revenue_low" in r.validation.codes()


def test_stub_needs_two_periods() -> None:
    one_year = replace(B2.operating, years=B2.operating.years[:1])
    raises("stub_horizon", replace(B2, operating=one_year))
