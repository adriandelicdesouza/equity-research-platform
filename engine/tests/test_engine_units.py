"""Unit tests: leases, bridge, terminal counterparts, dilution, roll-forward, rates."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

from engine_fixtures import load, to_inputs
from erp_engine.api import run_valuation
from erp_engine.bridge import lease_deduction
from erp_engine.core.numbers import calc, make_context
from erp_engine.core.trace import Trace
from erp_engine.core.validation import ValidationReport
from erp_engine.dilution import to_per_share, tsm_price
from erp_engine.forecast import project
from erp_engine.inputs import (
    Bridge,
    Dividend,
    Instrument,
    InstrumentKind,
    InstrumentTreatment,
    Leases,
    LeaseStandard,
    LeaseTreatment,
    LeaseYear,
    Shares,
    Terminal,
    TerminalForm,
)
from erp_engine.outputs import OutputKind, ValuationOutput

B1 = to_inputs(load("B1"))
B2 = to_inputs(load("B2"))
D = Decimal


@calc
def _years(leases: Leases):  # type: ignore[no-untyped-def]
    return project(B1.operating, leases, ValidationReport(), Trace())


def test_asc842_as_debt_moves_lease_cost_out_of_ebitda() -> None:
    plain = _years(Leases())
    ly = LeaseYear(operating_lease_cost=D(30), rou_depreciation=D(25))
    adj = _years(Leases(LeaseStandard.ASC842, LeaseTreatment.AS_DEBT, years=(ly,) * 3))
    for p, a in zip(plain, adj, strict=True):
        assert a.ebitda == p.ebitda + 30
        assert a.ebit == p.ebit + 30 - 25
        assert a.da == p.da + 25


def test_ifrs16_as_operating_deducts_full_lease_cash_cost() -> None:
    plain = _years(Leases())
    ly = LeaseYear(lease_interest=D(4), lease_principal=D(20), rou_depreciation=D(22))
    adj = _years(Leases(LeaseStandard.IFRS16, LeaseTreatment.AS_OPERATING, years=(ly,) * 3))
    for p, a in zip(plain, adj, strict=True):
        assert a.ebitda == p.ebitda - 24
        assert a.da == p.da - 22


def test_bridge_lease_deductions_by_treatment() -> None:
    b = Bridge(
        lease_liabilities=D(10), operating_lease_liabilities=D(20), finance_lease_liabilities=D(5)
    )
    assert lease_deduction(b, Leases(LeaseStandard.IFRS16, LeaseTreatment.AS_DEBT)) == 35
    assert lease_deduction(b, Leases(LeaseStandard.IFRS16, LeaseTreatment.AS_OPERATING)) == 0
    assert lease_deduction(b, Leases(LeaseStandard.ASC842, LeaseTreatment.AS_OPERATING)) == 5


def test_implied_growth_from_exit_multiple_round_trips_gordon() -> None:
    gordon = run_valuation(B1)
    multiple = gordon.terminal.implied_exit_multiple
    assert multiple is not None
    exit_run = run_valuation(
        replace(B1, terminal=Terminal(TerminalForm.EXIT_MULTIPLE, D(0), exit_multiple=multiple))
    )
    assert exit_run.terminal.implied_growth is not None
    assert abs(exit_run.terminal.implied_growth - D("0.02")) < D("1e-30")
    assert abs(exit_run.primary.value - gordon.primary.value) < D("1e-28")


def test_mid_period_gordon_tv_follows_convention() -> None:
    r = run_valuation(B2)
    assert r.terminal.time == r.times[-1]  # mid-point of final period (D27 amended)


def test_rsu_and_convertible_treatments() -> None:
    equity = ValuationOutput(OutputKind.EQUITY_VALUE, D(2000), "USD", date(2024, 12, 31), "x")
    rsu = Instrument("r1", InstrumentKind.RSU, D(10), InstrumentTreatment.ADD_SHARES)
    cheap_cb = Instrument(
        "cb", InstrumentKind.CONVERTIBLE_DEBT, D(10), InstrumentTreatment.AUTO, face_value=D(100)
    )  # 10 shares worth ~18 each → convert (>100)
    costly_cb = replace(cheap_cb, face_value=D(500))  # conversion worth < face → claim
    shares = Shares(D(100), date(2024, 12, 31))

    @calc
    def run(*inst: Instrument):  # type: ignore[no-untyped-def]
        return to_per_share(equity, replace(shares, instruments=inst), ValidationReport(), Trace())

    with localcontext(make_context()):
        per_110 = D(2000) / 110
    assert run(rsu).price.value == per_110
    converted = run(cheap_cb)
    assert converted.converted == ("cb",) and converted.price.value == per_110
    claim = run(costly_cb)
    assert claim.converted == () and claim.price.value == D(1500) / 100


def test_tsm_out_of_the_money_and_negative_equity() -> None:
    opt = Instrument("o", InstrumentKind.OPTION, D(10), InstrumentTreatment.TSM, strike=D(50))

    @calc
    def run(e: str):  # type: ignore[no-untyped-def]
        return tsm_price(D(e), D(100), [opt])

    assert run("2000") == (D(20), D(100), ())
    price, _, itm = run("-100")
    assert price == D(-1) and itm == ()


def test_roll_forward_counts_only_dividends_in_window() -> None:
    outside = (Dividend(date(2025, 6, 30), D(1)), Dividend(date(2025, 10, 1), D(1)))
    base = run_valuation(replace(B2, dividends=()))
    same = run_valuation(replace(B2, dividends=outside))
    assert base.equity_v.value == same.equity_v.value
    inside = run_valuation(replace(B2, dividends=(Dividend(date(2025, 9, 30), D(1)),)))
    assert base.equity_v.value - inside.equity_v.value == 100


def test_per_period_discount_rates_change_value() -> None:
    flat = run_valuation(B1)
    rising = run_valuation(replace(B1, discount_rates=(D("0.10"), D("0.10"), D("0.12"))))
    assert rising.primary.value < flat.primary.value
