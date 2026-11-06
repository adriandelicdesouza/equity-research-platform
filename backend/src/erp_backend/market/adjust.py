"""Split and dividend adjustments, provider-neutral (Blueprint §4.5, §7.7; D54). Pure.

**Share-count basis.** A share count (or per-share amount) reported in a filing is
expressed in the shares outstanding when that filing was issued: splits before issuance
are applied retroactively (ASC 260-10-55-12 / IAS 33.64), later splits are not. Its *basis
date* is therefore the filing date. To restate it to a *reference date*:

    factor  = Π split.factor  for  basis_date < ex_date ≤ reference_date
    shares_at_reference    = shares × factor
    per_share_at_reference = per_share ÷ factor

(and the inverse when the reference date precedes the basis date). Splits are applied in
ex-date order; the product is exact (Decimal) and order-independent. The reported value
is never modified: :class:`AdjustedAmount` carries both, the factor and the splits used.

**Adjusted close.** Our own backward multiplicative series (§4.5): going back in time, each
split ex-date divides earlier prices by the split factor (only when closes are raw), each
dividend ex-date multiplies earlier prices by ``1 − D / C_prev`` (C_prev = the close before
the ex-date). Provider adjusted closes that differ by more than 0.5% are flagged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from erp_backend.market.model import CashDividend, CloseBasis, PriceBar, Split

MISMATCH_TOLERANCE = Decimal("0.005")
BASIS_TOLERANCE = Decimal("0.25")


def split_factor(splits: Iterable[Split], basis_date: date, reference_date: date) -> Decimal:
    """Factor converting a count on ``basis_date``'s share basis to ``reference_date``'s."""
    factor = Decimal(1)
    if reference_date >= basis_date:
        for s in splits:
            if basis_date < s.ex_date <= reference_date:
                factor *= s.factor
    else:  # invert the forward product once (exact for any number of splits)
        for s in splits:
            if reference_date < s.ex_date <= basis_date:
                factor *= s.factor
        factor = Decimal(1) / factor
    return factor


@dataclass(frozen=True)
class AdjustedAmount:
    """A reported share count or per-share amount restated to a reference date."""

    kind: str  # shares | per_share
    reported: Decimal
    basis_date: date
    reference_date: date
    factor: Decimal
    adjusted: Decimal
    splits_applied: tuple[Split, ...]

    @property
    def changed(self) -> bool:
        return self.factor != 1


def _applied(splits: Iterable[Split], a: date, b: date) -> tuple[Split, ...]:
    lo, hi = min(a, b), max(a, b)
    return tuple(sorted((s for s in splits if lo < s.ex_date <= hi), key=lambda s: s.ex_date))


def adjust_shares(
    shares: Decimal, basis_date: date, reference_date: date, splits: Sequence[Split]
) -> AdjustedAmount:
    f = split_factor(splits, basis_date, reference_date)
    return AdjustedAmount(
        "shares",
        shares,
        basis_date,
        reference_date,
        f,
        shares * f,
        _applied(splits, basis_date, reference_date),
    )


def adjust_per_share(
    amount: Decimal, basis_date: date, reference_date: date, splits: Sequence[Split]
) -> AdjustedAmount:
    f = split_factor(splits, basis_date, reference_date)
    return AdjustedAmount(
        "per_share",
        amount,
        basis_date,
        reference_date,
        f,
        amount / f,
        _applied(splits, basis_date, reference_date),
    )


@dataclass(frozen=True)
class BasisEvidence:
    split: Split
    close_before: Decimal
    close_on_ex: Decimal
    ratio: Decimal  # close_before / close_on_ex
    reading: str  # raw | split_adjusted | inconclusive


def infer_close_basis(
    bars: Sequence[PriceBar], splits: Sequence[Split]
) -> tuple[CloseBasis, tuple[BasisEvidence, ...]]:
    """Decide whether closes are raw or split-adjusted from the price move across splits.

    A raw series drops by about the split factor on the ex-date; a split-adjusted one does
    not. Only splits whose factor is far from 1 (≥ 1.5 or ≤ 2/3) are informative. All
    informative splits must agree, otherwise the basis stays unverified.
    """
    ordered = sorted(bars, key=lambda b: b.trade_date)
    evidence: list[BasisEvidence] = []
    for s in sorted(splits, key=lambda s: s.ex_date):
        if Decimal("0.6667") < s.factor < Decimal("1.5"):
            continue
        before = [b for b in ordered if b.trade_date < s.ex_date]
        after = [b for b in ordered if b.trade_date >= s.ex_date]
        if not before or not after:
            continue
        ratio = before[-1].close / after[0].close
        if abs(ratio - s.factor) / s.factor <= BASIS_TOLERANCE:
            reading = "raw"
        elif abs(ratio - 1) <= BASIS_TOLERANCE:
            reading = "split_adjusted"
        else:
            reading = "inconclusive"
        evidence.append(BasisEvidence(s, before[-1].close, after[0].close, ratio, reading))
    readings = {e.reading for e in evidence}
    if readings == {"raw"}:
        return CloseBasis.RAW, tuple(evidence)
    if readings == {"split_adjusted"}:
        return CloseBasis.SPLIT_ADJUSTED, tuple(evidence)
    return CloseBasis.UNVERIFIED, tuple(evidence)


@dataclass(frozen=True)
class AdjustedClose:
    trade_date: date
    close: Decimal
    ours: Decimal
    provider: Decimal | None
    relative_difference: Decimal | None
    flagged: bool


def recompute_adjusted_close(
    bars: Sequence[PriceBar],
    splits: Sequence[Split],
    dividends: Sequence[CashDividend],
    close_basis: CloseBasis,
) -> list[AdjustedClose]:
    """Our split-and-dividend-adjusted closes, compared with the provider's (§4.5)."""
    if close_basis is CloseBasis.UNVERIFIED:
        raise ValueError("Close basis must be established before recomputing adjustments")
    ordered = sorted(bars, key=lambda b: b.trade_date)
    events: dict[date, Decimal] = {}
    for i, b in enumerate(ordered):
        factor = Decimal(1)
        for s in splits:
            if close_basis is CloseBasis.RAW and s.ex_date == b.trade_date:
                factor /= s.factor
        for d in dividends:
            if d.ex_date == b.trade_date and i > 0:
                factor *= 1 - d.amount / ordered[i - 1].close
        if factor != 1:
            events[b.trade_date] = factor  # applies to all closes *before* this date
    out: list[AdjustedClose] = []
    running = Decimal(1)
    for b in reversed(ordered):
        ours = (b.close * running).quantize(Decimal("1e-8"))
        rel = None
        flagged = False
        if b.adj_close is not None and ours:
            rel = (abs(b.adj_close - ours) / ours).quantize(Decimal("1e-8"))
            flagged = rel > MISMATCH_TOLERANCE
        out.append(AdjustedClose(b.trade_date, b.close, ours, b.adj_close, rel, flagged))
        running *= events.get(b.trade_date, Decimal(1))
    return list(reversed(out))


@dataclass(frozen=True)
class DatedClose:
    trade_date: date
    close: Decimal
    close_basis: CloseBasis


def latest_close(bars: Iterable[PriceBar], as_of: date, basis: CloseBasis) -> DatedClose | None:
    """The valuation price: the last close on or before ``as_of`` (never a live quote)."""
    eligible = [b for b in bars if b.trade_date <= as_of]
    if not eligible:
        return None
    last = max(eligible, key=lambda b: b.trade_date)
    return DatedClose(last.trade_date, last.close, basis)
