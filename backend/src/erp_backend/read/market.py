"""Prices, corporate actions, rate series and FX (read-only).

Series are never mixed silently:

* prices are read for **one** provider and **one** close basis; when a listing has more
  than one and the caller did not choose, the request is refused with the options
  (``409 ambiguous_series``) instead of picking one;
* FX is read only in the stored direction (quote per 1 base) — never inverted;
* ``revisions=latest`` (default) returns the latest stored observation per date (D54/D58:
  a revision is a new row); ``revisions=all`` returns every stored observation, and each
  row reports how many observations exist for its date.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from erp_backend.errors import ApiError
from erp_backend.models import (
    CorporateAction,
    FxRate,
    PriceObservation,
    RateObservationRow,
    RateSeries,
    Security,
)
from erp_backend.read.common import not_found, paginate
from erp_backend.read.schemas import (
    CorporateActionOut,
    FxRateOut,
    OriginOut,
    Page,
    PriceOut,
    RateObservationOut,
    RateSeriesOut,
)


def _origin(row: Any) -> OriginOut:
    if getattr(row, "import_id", None) is not None:
        return OriginOut(kind="file_import", import_id=row.import_id, import_row=row.import_row)
    return OriginOut(kind="provider_fetch", fetch_id=row.fetch_id)


def _date_filter(stmt: Select[Any], column: Any, start: date | None, end: date | None) -> Any:
    if start is not None:
        stmt = stmt.where(column >= start)
    if end is not None:
        stmt = stmt.where(column <= end)
    return stmt


def _require_choice(kind: str, options: list[str], chosen: str | None) -> str | None:
    if chosen is not None:
        return chosen
    if len(options) > 1:
        raise ApiError(
            409,
            "ambiguous_series",
            f"Several values of '{kind}' are stored ({', '.join(options)}); choose one with "
            f"'{kind}' — series are never mixed.",
        )
    return options[0] if options else None


# --------------------------------------------------------------------------- prices


def list_prices(
    db: Session,
    security_id: uuid.UUID,
    *,
    provider: str | None,
    close_basis: str | None,
    start: date | None,
    end: date | None,
    revisions: str,
    descending: bool,
    limit: int,
    offset: int,
) -> Page[PriceOut]:
    if db.get(Security, security_id) is None:
        raise not_found("listing", security_id)
    base = PriceObservation.security_id == security_id
    providers = sorted(
        db.scalars(select(PriceObservation.provider_code).where(base).distinct()).all()
    )
    provider = _require_choice("provider", providers, provider)
    bases = sorted(
        db.scalars(
            select(PriceObservation.close_basis)
            .where(base, PriceObservation.provider_code == provider)
            .distinct()
        ).all()
    )
    close_basis = _require_choice("close_basis", bases, close_basis)
    conditions = [
        base,
        PriceObservation.provider_code == provider,
        PriceObservation.close_basis == close_basis,
    ]
    if start is not None:
        conditions.append(PriceObservation.trade_date >= start)
    if end is not None:
        conditions.append(PriceObservation.trade_date <= end)
    stmt = select(PriceObservation).where(*conditions)
    if revisions == "latest":
        latest = (
            select(func.max(PriceObservation.id))
            .where(*conditions)
            .group_by(PriceObservation.trade_date)
        )
        stmt = stmt.where(PriceObservation.id.in_(latest))
    order = PriceObservation.trade_date.desc() if descending else PriceObservation.trade_date
    stmt = stmt.order_by(order, PriceObservation.id)

    def mapper(rows: Sequence[PriceObservation]) -> list[PriceOut]:
        dates = {r.trade_date for r in rows}
        counts = dict(
            db.execute(
                select(PriceObservation.trade_date, func.count())
                .where(*conditions, PriceObservation.trade_date.in_(dates))
                .group_by(PriceObservation.trade_date)
            ).all()
        ) if dates else {}  # fmt: skip
        return [
            PriceOut(
                observation_id=r.id,
                trade_date=r.trade_date,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                close_basis=r.close_basis,
                adj_close=r.adj_close,
                adj_close_basis=r.adj_close_basis,
                volume=r.volume,
                currency=r.currency,
                provider_code=r.provider_code,
                origin=_origin(r),
                revisions=counts.get(r.trade_date, 1),
            )
            for r in rows
        ]

    return paginate(db, stmt, limit, offset, mapper)


def list_corporate_actions(
    db: Session,
    security_id: uuid.UUID,
    *,
    kind: str | None,
    provider: str | None,
    start: date | None,
    end: date | None,
    limit: int,
    offset: int,
) -> Page[CorporateActionOut]:
    if db.get(Security, security_id) is None:
        raise not_found("listing", security_id)
    stmt = select(CorporateAction).where(CorporateAction.security_id == security_id)
    if kind:
        stmt = stmt.where(CorporateAction.kind == kind)
    if provider:
        stmt = stmt.where(CorporateAction.provider_code == provider)
    stmt = _date_filter(stmt, CorporateAction.ex_date, start, end)
    stmt = stmt.order_by(CorporateAction.ex_date, CorporateAction.id)
    return paginate(
        db,
        stmt,
        limit,
        offset,
        lambda rows: [
            CorporateActionOut(
                id=r.id,
                kind=r.kind,
                ex_date=r.ex_date,
                ratio_numerator=r.ratio_numerator,
                ratio_denominator=r.ratio_denominator,
                amount=r.amount,
                currency=r.currency,
                currency_source=r.currency_source,
                provider_code=r.provider_code,
                origin=_origin(r),
            )
            for r in rows
        ],
    )


# --------------------------------------------------------------------------- rates


def _series_out(s: RateSeries, stats: tuple[int, date | None, date | None]) -> RateSeriesOut:
    return RateSeriesOut(
        code=s.code,
        provider_code=s.provider_code,
        provider_series_id=s.provider_series_id,
        currency=s.currency,
        rate_type=s.rate_type,
        tenor=s.tenor,
        tenor_months=s.tenor_months,
        unit=s.unit,
        quote_convention=s.quote_convention,
        description=s.description,
        observation_count=stats[0],
        first_date=stats[1],
        last_date=stats[2],
    )


def _series_stats(db: Session, codes: list[str]) -> dict[str, tuple[int, date | None, date | None]]:
    if not codes:
        return {}
    return {
        code: (count, first, last)
        for code, count, first, last in db.execute(
            select(
                RateObservationRow.series_code,
                func.count(),
                func.min(RateObservationRow.obs_date),
                func.max(RateObservationRow.obs_date),
            )
            .where(RateObservationRow.series_code.in_(codes))
            .group_by(RateObservationRow.series_code)
        )
    }


def list_rate_series(
    db: Session,
    *,
    currency: str | None,
    provider: str | None,
    rate_type: str | None,
    tenor: str | None,
    limit: int,
    offset: int,
) -> Page[RateSeriesOut]:
    stmt = select(RateSeries)
    if currency:
        stmt = stmt.where(RateSeries.currency == currency.upper())
    if provider:
        stmt = stmt.where(RateSeries.provider_code == provider)
    if rate_type:
        stmt = stmt.where(RateSeries.rate_type == rate_type)
    if tenor:
        stmt = stmt.where(RateSeries.tenor == tenor.upper())
    stmt = stmt.order_by(
        RateSeries.provider_code, RateSeries.tenor_months.nulls_last(), RateSeries.code
    )

    def mapper(rows: Sequence[RateSeries]) -> list[RateSeriesOut]:
        stats = _series_stats(db, [s.code for s in rows])
        return [_series_out(s, stats.get(s.code, (0, None, None))) for s in rows]

    return paginate(db, stmt, limit, offset, mapper)


def get_rate_series(db: Session, code: str) -> RateSeriesOut:
    s = db.get(RateSeries, code)
    if s is None:
        raise not_found("rate series", code)
    return _series_out(s, _series_stats(db, [code]).get(code, (0, None, None)))


def list_rate_observations(
    db: Session,
    code: str,
    *,
    start: date | None,
    end: date | None,
    revisions: str,
    descending: bool,
    limit: int,
    offset: int,
) -> Page[RateObservationOut]:
    series = db.get(RateSeries, code)
    if series is None:
        raise not_found("rate series", code)
    conditions = [RateObservationRow.series_code == code]
    if start is not None:
        conditions.append(RateObservationRow.obs_date >= start)
    if end is not None:
        conditions.append(RateObservationRow.obs_date <= end)
    stmt = select(RateObservationRow).where(*conditions)
    if revisions == "latest":
        stmt = stmt.where(
            RateObservationRow.id.in_(
                select(func.max(RateObservationRow.id))
                .where(*conditions)
                .group_by(RateObservationRow.obs_date)
            )
        )
    order = RateObservationRow.obs_date.desc() if descending else RateObservationRow.obs_date
    stmt = stmt.order_by(order, RateObservationRow.id)

    def mapper(rows: Sequence[RateObservationRow]) -> list[RateObservationOut]:
        dates = {r.obs_date for r in rows}
        counts = dict(
            db.execute(
                select(RateObservationRow.obs_date, func.count())
                .where(*conditions, RateObservationRow.obs_date.in_(dates))
                .group_by(RateObservationRow.obs_date)
            ).all()
        ) if dates else {}  # fmt: skip
        return [
            RateObservationOut(
                observation_id=r.id,
                series_code=r.series_code,
                obs_date=r.obs_date,
                value=r.value,
                unit=series.unit,
                provider_code=r.provider_code,
                fetch_id=r.fetch_id,
                revisions=counts.get(r.obs_date, 1),
            )
            for r in rows
        ]

    return paginate(db, stmt, limit, offset, mapper)


# --------------------------------------------------------------------------- FX


def list_fx(
    db: Session,
    *,
    base: str,
    quote: str,
    provider: str | None,
    start: date | None,
    end: date | None,
    revisions: str,
    descending: bool,
    limit: int,
    offset: int,
) -> Page[FxRateOut]:
    base, quote = base.upper(), quote.upper()
    if base == quote:
        raise ApiError(422, "validation_error", "base and quote must differ.")
    pair = [FxRate.base == base, FxRate.quote == quote]
    providers = sorted(db.scalars(select(FxRate.provider_code).where(*pair).distinct()).all())
    provider = _require_choice("provider", providers, provider)
    conditions = [*pair, FxRate.provider_code == provider]
    if start is not None:
        conditions.append(FxRate.obs_date >= start)
    if end is not None:
        conditions.append(FxRate.obs_date <= end)
    stmt = select(FxRate).where(*conditions)
    if revisions == "latest":
        stmt = stmt.where(
            FxRate.id.in_(select(func.max(FxRate.id)).where(*conditions).group_by(FxRate.obs_date))
        )
    order = FxRate.obs_date.desc() if descending else FxRate.obs_date
    stmt = stmt.order_by(order, FxRate.id)

    def mapper(rows: Sequence[FxRate]) -> list[FxRateOut]:
        dates = {r.obs_date for r in rows}
        counts = dict(
            db.execute(
                select(FxRate.obs_date, func.count())
                .where(*conditions, FxRate.obs_date.in_(dates))
                .group_by(FxRate.obs_date)
            ).all()
        ) if dates else {}  # fmt: skip
        return [
            FxRateOut(
                observation_id=r.id,
                base=r.base,
                quote=r.quote,
                direction=f"{r.quote} per 1 {r.base}",
                obs_date=r.obs_date,
                rate=r.rate,
                provider_code=r.provider_code,
                provider_series_id=r.provider_series_id,
                fetch_id=r.fetch_id,
                revisions=counts.get(r.obs_date, 1),
            )
            for r in rows
        ]

    return paginate(db, stmt, limit, offset, mapper)
