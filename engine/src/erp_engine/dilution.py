"""Equity → per-share conversion with the instrument register (methodology §10, D23).

The only equity → per-share conversion in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ZERO
from erp_engine.core.trace import Trace
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.inputs import Instrument, InstrumentKind, InstrumentTreatment, Shares
from erp_engine.outputs import OutputKind, ValuationOutput, require_kind

MODULE = "dilution"
_OPTIONS = {InstrumentKind.OPTION, InstrumentKind.WARRANT}
_UNITS = {InstrumentKind.RSU, InstrumentKind.PSU, InstrumentKind.DSU}
_CONVERTIBLES = {InstrumentKind.CONVERTIBLE_DEBT, InstrumentKind.CONVERTIBLE_PREFERRED}
_ALLOWED = {
    **dict.fromkeys(_OPTIONS, frozenset({InstrumentTreatment.TSM, InstrumentTreatment.EXCLUDED})),
    **dict.fromkeys(
        _UNITS, frozenset({InstrumentTreatment.ADD_SHARES, InstrumentTreatment.EXCLUDED})
    ),
    **dict.fromkeys(
        _CONVERTIBLES,
        frozenset(
            {InstrumentTreatment.CLAIM, InstrumentTreatment.IF_CONVERTED, InstrumentTreatment.AUTO}
        ),
    ),
}


@dataclass(frozen=True)
class DilutionResult:
    price: ValuationOutput
    diluted_shares: Decimal
    base_shares: Decimal
    in_the_money: tuple[str, ...]
    converted: tuple[str, ...]


def base_shares(shares: Shares, valuation_date: date) -> Decimal:
    if shares.base_kind != "point_in_time":
        raise EngineError(
            "weighted_average_base",
            "Diluted value must start from point-in-time basic shares, not a "
            f"{shares.base_kind} count.",
        )
    total = shares.basic
    for adj in shares.adjustments:
        if not shares.base_date < adj.date <= valuation_date or not adj.source.strip():
            raise EngineError(
                "invalid_share_adjustment",
                "Share adjustments must be sourced and dated after the base date "
                "and on or before the valuation date.",
            )
        total += adj.shares
    return total


def _validate_register(instruments: tuple[Instrument, ...]) -> None:
    ids = [i.id for i in instruments]
    if len(ids) != len(set(ids)):
        raise EngineError("instrument_double_count", "Each instrument may appear only once.")
    for i in instruments:
        if i.treatment not in _ALLOWED[i.kind]:
            raise EngineError(
                "invalid_treatment", f"{i.id}: {i.treatment} not allowed for {i.kind}."
            )
        if i.treatment is InstrumentTreatment.EXCLUDED and not (i.exclusion_reason or "").strip():
            raise EngineError("exclusion_reason_required", f"{i.id}: exclusion needs a reason.")
        if i.kind in _OPTIONS and i.strike is None:
            raise EngineError("strike_required", f"{i.id}: options need a strike.")
        if i.kind in _CONVERTIBLES and i.face_value is None:
            raise EngineError("face_value_required", f"{i.id}: convertibles need a face value.")


def tsm_price(
    equity: Decimal, shares: Decimal, options: list[Instrument]
) -> tuple[Decimal, Decimal, tuple[str, ...]]:
    """Fixed-point iteration on the in-the-money set (§10.2). Returns (P, diluted, ITM ids)."""
    if shares <= 0:
        raise EngineError("non_positive_shares", "Share count must be positive.")
    itm = {o.id for o in options if o.strike is not None and o.strike < equity / shares}
    for _ in range(len(options) + 2):
        chosen = [o for o in options if o.id in itm]
        n = sum((o.count for o in chosen), ZERO)
        nk = sum((o.count * (o.strike or ZERO) for o in chosen), ZERO)
        price = (equity + nk) / (shares + n)
        nxt = {o.id for o in options if o.strike is not None and o.strike < price}
        if nxt == itm:
            diluted = shares + sum(
                (o.count * (1 - (o.strike or ZERO) / price) for o in chosen), ZERO
            )
            return price, diluted, tuple(sorted(itm))
        itm = nxt
    raise EngineError("tsm_no_convergence", "Treasury-stock iteration did not converge.")


def to_per_share(
    equity_v: ValuationOutput, shares: Shares, report: ValidationReport, trace: Trace
) -> DilutionResult:
    require_kind(equity_v, OutputKind.EQUITY_VALUE, "Dilution")
    _validate_register(shares.instruments)
    base = base_shares(shares, equity_v.valuation_date)
    units = sum(
        (
            i.count
            for i in shares.instruments
            if i.kind in _UNITS and i.treatment is InstrumentTreatment.ADD_SHARES
        ),
        ZERO,
    )
    options = [
        i
        for i in shares.instruments
        if i.kind in _OPTIONS and i.treatment is InstrumentTreatment.TSM
    ]
    convertibles = [i for i in shares.instruments if i.kind in _CONVERTIBLES]

    equity, count, converted = equity_v.value, base + units, []
    for c in convertibles:  # decided before TSM
        face = c.face_value or ZERO
        treat = c.treatment
        if treat is InstrumentTreatment.AUTO:
            p_claim, _, _ = tsm_price(equity - face, count, options)
            treat = (
                InstrumentTreatment.IF_CONVERTED
                if c.count * p_claim > face
                else InstrumentTreatment.CLAIM
            )
            report.add(
                "convertible_auto_treatment",
                Severity.INFO,
                f"{c.id}: treated as {treat.value} (value to holder).",
                conversion_value=c.count * p_claim,
                face=face,
            )
        if treat is InstrumentTreatment.IF_CONVERTED:
            count += c.count
            converted.append(c.id)
        else:
            equity -= face
    price, diluted, itm = tsm_price(equity, count, options)
    if equity <= 0:
        report.add(
            "non_positive_equity",
            Severity.WARNING,
            "Equity value available to common shareholders is zero or negative.",
        )
    if shares.reported_diluted_weighted:
        gap = abs(diluted - shares.reported_diluted_weighted) / shares.reported_diluted_weighted
        if gap > Decimal("0.05"):
            report.add(
                "diluted_shares_vs_reported",
                Severity.WARNING,
                "Implied diluted shares differ from reported diluted shares by > 5%.",
                implied=diluted,
                reported=shares.reported_diluted_weighted,
            )
    trace.record(
        "dilution.diluted_shares",
        "Diluted shares",
        diluted,
        unit="shares",
        rule="Base + units + converted + Σ_ITM n(1 − K/P)",
        module=MODULE,
    )
    trace.record(
        "dilution.price",
        "Implied value per share",
        price,
        unit="currency/share",
        rule="(Equity_V − convertible claims + Σ nK) / (shares + Σ n) over ITM set",
        module=MODULE,
        inputs=(equity_v.trace_id, "dilution.diluted_shares"),
    )
    out = ValuationOutput(
        OutputKind.PER_SHARE_VALUE,
        price,
        equity_v.currency,
        equity_v.valuation_date,
        equity_v.methodology,
        equity_v.basis,
        "dilution.price",
    )
    return DilutionResult(out, diluted, base, itm, tuple(converted))
