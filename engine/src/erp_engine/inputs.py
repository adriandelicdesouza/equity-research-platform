"""Typed, fully materialized valuation inputs (Blueprint §6.2).

Frozen dataclasses with ``Decimal`` amounts. Scenario resolution, provenance and approval
happen upstream (backend); the engine receives only resolved values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from erp_engine.core.timeline import Convention


class Method(StrEnum):
    FCFF = "dcf_fcff"
    FCFE = "dcf_fcfe"


class TerminalForm(StrEnum):
    VALUE_DRIVER = "value_driver"  # default (D35)
    SIMPLE = "simple"
    EXIT_MULTIPLE = "exit_multiple"


class TerminalTiming(StrEnum):
    FOLLOW_CONVENTION = "follow_convention"  # D27 (amended)
    END_OF_PERIOD = "end_of_period"  # recorded conservative override


class ExitMetric(StrEnum):
    EBITDA = "ebitda"
    EBIT = "ebit"
    REVENUE = "revenue"


class StubMode(StrEnum):
    ACTUAL_PARTIAL = "B"
    PRORATED = "A"


class LeaseStandard(StrEnum):
    IFRS16 = "ifrs16"
    ASC842 = "asc842"
    NONE = "none"


class LeaseTreatment(StrEnum):
    AS_DEBT = "as_debt"
    AS_OPERATING = "as_operating"


class SbcTreatment(StrEnum):
    EXPENSE = "expense"
    ADD_BACK = "add_back"


class Classification(StrEnum):
    NON_FINANCIAL = "non_financial"
    BANK = "bank"
    INSURER = "insurer"
    SPECIALTY_LENDER = "specialty_lender"
    INVESTMENT_HOLDING = "investment_holding"
    REIT = "reit"
    ASSET_MANAGER_BROKER = "asset_manager_broker"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class YearDrivers:
    growth: Decimal
    ebit_margin: Decimal
    tax_rate: Decimal
    da_pct: Decimal
    capex_pct: Decimal
    nwc_pct: Decimal
    sbc_pct: Decimal = Decimal(0)


@dataclass(frozen=True)
class LeaseYear:
    """Per-year lease amounts (positive numbers); which are required depends on §8."""

    operating_lease_cost: Decimal | None = None  # ASC 842 single lease cost (in opex)
    rou_depreciation: Decimal | None = None
    lease_interest: Decimal | None = None
    lease_principal: Decimal | None = None


@dataclass(frozen=True)
class Leases:
    standard: LeaseStandard = LeaseStandard.NONE
    treatment: LeaseTreatment = LeaseTreatment.AS_DEBT
    years: tuple[LeaseYear, ...] = ()
    capex_includes_rou_additions: bool = False


@dataclass(frozen=True)
class Operating:
    revenue_0: Decimal
    nwc_0: Decimal
    years: tuple[YearDrivers, ...]
    sbc_treatment: SbcTreatment = SbcTreatment.EXPENSE
    future_dilution_modelled: bool = False


@dataclass(frozen=True)
class YtdActuals:
    revenue: Decimal
    ebit: Decimal
    da: Decimal
    capex: Decimal
    interest_expense: Decimal = Decimal(0)
    interest_income: Decimal = Decimal(0)
    net_borrowing: Decimal = Decimal(0)


@dataclass(frozen=True)
class Stub:
    mode: StubMode
    fiscal_year_start: date
    fiscal_year_end: date
    ytd: YtdActuals | None = None
    nwc_at_b: Decimal | None = None
    ytd_fiscal_year_end: date | None = None  # must equal fiscal_year_end (Mode B check)


@dataclass(frozen=True)
class Terminal:
    form: TerminalForm
    g: Decimal
    ronic: Decimal | None = None  # RONIC (FCFF) / ROE on new equity (FCFE)
    exit_multiple: Decimal | None = None
    exit_metric: ExitMetric = ExitMetric.EBITDA
    exit_lease_basis: str | None = None  # "pre_lease" / "post_lease"
    timing: TerminalTiming = TerminalTiming.FOLLOW_CONVENTION


@dataclass(frozen=True)
class Claim:
    id: str
    label: str
    amount: Decimal


@dataclass(frozen=True)
class Bridge:
    cash: Decimal = Decimal(0)
    short_term_investments: Decimal = Decimal(0)
    non_operating_assets: Decimal = Decimal(0)
    debt: Decimal = Decimal(0)
    lease_liabilities: Decimal = Decimal(0)  # IFRS 16
    operating_lease_liabilities: Decimal = Decimal(0)  # ASC 842
    finance_lease_liabilities: Decimal = Decimal(0)  # ASC 842
    preferred: Decimal = Decimal(0)
    nci: Decimal = Decimal(0)
    other_claims: tuple[Claim, ...] = ()


class InstrumentKind(StrEnum):
    OPTION = "option"
    WARRANT = "warrant"
    RSU = "rsu"
    PSU = "psu"
    DSU = "dsu"
    CONVERTIBLE_DEBT = "convertible_debt"
    CONVERTIBLE_PREFERRED = "convertible_preferred"


class InstrumentTreatment(StrEnum):
    TSM = "tsm"
    ADD_SHARES = "add_shares"
    EXCLUDED = "excluded"
    CLAIM = "claim"
    IF_CONVERTED = "if_converted"
    AUTO = "auto"  # convertibles: whichever is worth more to the holder


@dataclass(frozen=True)
class Instrument:
    id: str
    kind: InstrumentKind
    count: Decimal  # options/units, or shares on conversion for convertibles
    treatment: InstrumentTreatment
    strike: Decimal | None = None
    face_value: Decimal | None = None  # convertibles: claim amount if not converted
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class ShareAdjustment:
    date: date
    shares: Decimal  # + issuance, − buyback
    source: str


@dataclass(frozen=True)
class Shares:
    basic: Decimal
    base_date: date
    base_kind: str = "point_in_time"  # "weighted_average_basic"/"weighted_average_diluted" rejected
    adjustments: tuple[ShareAdjustment, ...] = ()
    instruments: tuple[Instrument, ...] = ()
    reported_diluted_weighted: Decimal | None = None


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    per_share: Decimal


@dataclass(frozen=True)
class Financing:
    """FCFE financing paths: index 0 = balance at B, then end of each forecast period."""

    debt_path: tuple[Decimal, ...]
    cash_path: tuple[Decimal, ...]
    cost_of_debt: Decimal
    cash_yield: Decimal
    preferred_dividends: tuple[Decimal, ...] = ()
    nci_share: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class ValuationInputs:
    method: Method
    currency: str
    balance_sheet_date: date
    valuation_date: date
    convention: Convention
    operating: Operating
    terminal: Terminal
    shares: Shares
    discount_rates: tuple[Decimal, ...] = ()  # WACC per period (FCFF); broadcast if length 1
    cost_of_equity: Decimal | None = None  # FCFE discounting and roll-forward
    bridge: Bridge = field(default_factory=Bridge)
    leases: Leases = field(default_factory=Leases)
    stub: Stub | None = None
    financing: Financing | None = None
    dividends: tuple[Dividend, ...] = ()
    classification: Classification = Classification.NON_FINANCIAL
    risk_free_rate: Decimal | None = None  # for the g > Rf coherence check
    rate_currency: str | None = None  # currency of the discount-rate inputs
