"""Shared helpers for read services: pagination, lookups, period mapping."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend.errors import ApiError
from erp_backend.models import FiscalPeriodRow
from erp_backend.read.schemas import Page, PeriodOut

MAX_LIMIT = 500


def not_found(what: str, ident: object) -> ApiError:
    return ApiError(404, "not_found", f"No {what} {ident}.")


def paginate[T](
    db: Session,
    stmt: Any,
    limit: int,
    offset: int,
    mapper: Callable[[Sequence[Any]], list[T]],
    *,
    scalars: bool = True,
) -> Page[T]:
    """Count the matching rows, fetch one page, map it in one batch (no N+1)."""
    if not 1 <= limit <= MAX_LIMIT or offset < 0:  # pragma: no cover - validated by the API
        raise ApiError(422, "validation_error", "Invalid limit/offset.")
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    page_stmt = stmt.limit(limit).offset(offset)
    rows: Sequence[Any] = db.scalars(page_stmt).all() if scalars else db.execute(page_stmt).all()
    return Page[T](items=mapper(rows), total=total, limit=limit, offset=offset)


def period_out(p: FiscalPeriodRow) -> PeriodOut:
    return PeriodOut(
        fiscal_year=p.fiscal_year,
        period_type=p.period_type,
        start_date=p.start_date,
        end_date=p.end_date,
        weeks=p.weeks,
    )


def periods_by_id(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, FiscalPeriodRow]:
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(FiscalPeriodRow).where(FiscalPeriodRow.id.in_(ids)))}
