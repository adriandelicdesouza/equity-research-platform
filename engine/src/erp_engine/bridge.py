"""EV → equity bridge (methodology §9). The only EV → equity conversion in the engine.

Accepts ``ENTERPRISE_VALUE`` outputs only; FCFE (``EQUITY_VALUE``) is rejected (D18/D19).
Convertible instruments are never bridge claims here — the dilution module decides
between claim and if-converted treatment (§10.2).
"""

from __future__ import annotations

from decimal import Decimal

from erp_engine.core.errors import EngineError
from erp_engine.core.trace import Trace
from erp_engine.inputs import Bridge, Instrument, Leases, LeaseStandard, LeaseTreatment
from erp_engine.outputs import OutputKind, ValuationOutput, require_kind

MODULE = "bridge"


def lease_deduction(bridge: Bridge, leases: Leases) -> Decimal:
    std, treat = leases.standard, leases.treatment
    if std is LeaseStandard.NONE:
        if bridge.lease_liabilities or bridge.operating_lease_liabilities:
            raise EngineError(
                "lease_standard_missing", "Lease liabilities given but no lease standard selected."
            )
        return bridge.finance_lease_liabilities
    if treat is LeaseTreatment.AS_DEBT:
        return (
            bridge.lease_liabilities
            + bridge.operating_lease_liabilities
            + bridge.finance_lease_liabilities
        )
    if std is LeaseStandard.IFRS16:
        return Decimal(0)
    return bridge.finance_lease_liabilities  # ASC 842, as_operating


def to_equity(
    ev: ValuationOutput,
    bridge: Bridge,
    leases: Leases,
    instruments: tuple[Instrument, ...],
    trace: Trace,
) -> ValuationOutput:
    require_kind(ev, OutputKind.ENTERPRISE_VALUE, "Equity bridge")
    instrument_ids = {i.id for i in instruments}
    overlap = sorted(c.id for c in bridge.other_claims if c.id in instrument_ids)
    if overlap:
        raise EngineError(
            "instrument_double_count",
            f"Instruments {overlap} appear both as bridge claims and in the share register.",
        )
    leases_out = lease_deduction(bridge, leases)
    claims = sum((c.amount for c in bridge.other_claims), Decimal(0))
    equity = (
        ev.value
        + bridge.cash
        + bridge.short_term_investments
        + bridge.non_operating_assets
        - bridge.debt
        - leases_out
        - bridge.preferred
        - bridge.nci
        - claims
    )
    trace.record(
        "bridge.lease_deduction",
        "Lease liabilities deducted",
        leases_out,
        unit="currency",
        rule="Per lease standard and treatment (§8)",
        module=MODULE,
    )
    trace.record(
        "bridge.equity_b",
        "Equity value at B",
        equity,
        unit="currency",
        rule="EV + cash + STI + non-operating − debt − leases − preferred − NCI − other claims",
        module=MODULE,
        inputs=("discounting.ev", "bridge.lease_deduction"),
    )
    return ValuationOutput(
        OutputKind.EQUITY_VALUE,
        equity,
        ev.currency,
        ev.valuation_date,
        ev.methodology,
        ev.basis,
        "bridge.equity_b",
    )
