"""Persist parsed rate/FX payloads; gated fetch; re-ingestion from the archive (D58; D54 pattern).

Append-only and idempotent (migration 0014): an exact repeat inserts nothing; a value that
differs from a stored one for the same series and date is kept as a **new** observation
and counted as a revision (readers take the latest per date). Nothing is overwritten.
Every stored row references the provider fetch whose archived, hash-verified payload
delivered it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.ingestion import archive
from erp_backend.ingestion.http import ProviderClient, fetch_and_archive
from erp_backend.market.store import insert_new
from erp_backend.models import FxRate, ProviderFetch, RateObservationRow
from erp_backend.rates.model import ParsedSeries

HTTP_OK = 200


class RatesError(Exception):
    pass


class RatesAdapter(Protocol):
    code: str

    def parse(self, payload: bytes, today: date | None = None) -> ParsedSeries: ...


@dataclass(frozen=True)
class RatesStoreResult:
    fetch_id: uuid.UUID
    rates_inserted: int
    rates_already_present: int
    rates_revised: int
    fx_inserted: int
    fx_already_present: int
    fx_revised: int
    rejected: int
    missing: int
    warnings: int

    def as_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in self.__dict__.items()}


def _revised_rate_keys(db: Session, parsed: ParsedSeries) -> set[tuple[str, date]]:
    """(series, date) keys of this payload for which a *different* value is stored."""
    if not parsed.rates:
        return set()
    keys = {(o.series_code, o.obs_date) for o in parsed.rates}
    stored = db.execute(
        select(
            RateObservationRow.series_code, RateObservationRow.obs_date, RateObservationRow.value
        ).where(
            RateObservationRow.provider_code == parsed.provider_code,
            tuple_(RateObservationRow.series_code, RateObservationRow.obs_date).in_(keys),
        )
    ).all()
    by_key: dict[tuple[str, date], set[Any]] = {}
    for series, on, value in stored:
        by_key.setdefault((series, on), set()).add(value)
    return {
        (o.series_code, o.obs_date)
        for o in parsed.rates
        if by_key.get((o.series_code, o.obs_date))
        and o.value not in by_key[(o.series_code, o.obs_date)]
    }


def _revised_fx_keys(db: Session, parsed: ParsedSeries) -> set[tuple[str, date]]:
    if not parsed.fx:
        return set()
    keys = {(o.provider_series_id, o.obs_date) for o in parsed.fx}
    stored = db.execute(
        select(FxRate.provider_series_id, FxRate.obs_date, FxRate.rate).where(
            FxRate.provider_code == parsed.provider_code,
            tuple_(FxRate.provider_series_id, FxRate.obs_date).in_(keys),
        )
    ).all()
    by_key: dict[tuple[str, date], set[Any]] = {}
    for series, on, rate in stored:
        by_key.setdefault((series, on), set()).add(rate)
    return {
        (o.provider_series_id, o.obs_date)
        for o in parsed.fx
        if by_key.get((o.provider_series_id, o.obs_date))
        and o.rate not in by_key[(o.provider_series_id, o.obs_date)]
    }


def store_parsed(
    db: Session,
    parsed: ParsedSeries,
    fetch: ProviderFetch,
    *,
    user_id: uuid.UUID | None = None,
) -> RatesStoreResult:
    if fetch.provider_code != parsed.provider_code:
        raise RatesError("Fetch and payload come from different providers")
    revised_rates = _revised_rate_keys(db, parsed)
    revised_fx = _revised_fx_keys(db, parsed)
    rate_rows = [
        {
            "series_code": o.series_code,
            "provider_code": parsed.provider_code,
            "obs_date": o.obs_date,
            "value": o.value,
            "fetch_id": fetch.id,
        }
        for o in parsed.rates
    ]
    fx_rows = [
        {
            "base": o.base,
            "quote": o.quote,
            "obs_date": o.obs_date,
            "rate": o.rate,
            "provider_code": parsed.provider_code,
            "provider_series_id": o.provider_series_id,
            "fetch_id": fetch.id,
        }
        for o in parsed.fx
    ]
    rates_inserted = insert_new(db, RateObservationRow, rate_rows)
    fx_inserted = insert_new(db, FxRate, fx_rows)
    result = RatesStoreResult(
        fetch_id=fetch.id,
        rates_inserted=rates_inserted,
        rates_already_present=len(rate_rows) - rates_inserted,
        rates_revised=len(revised_rates),
        fx_inserted=fx_inserted,
        fx_already_present=len(fx_rows) - fx_inserted,
        fx_revised=len(revised_fx),
        rejected=len(parsed.errors),
        missing=parsed.missing,
        warnings=len(parsed.issues) - len(parsed.errors),
    )
    audit.record(
        db,
        action="rates_stored",
        entity_type="provider_fetch",
        entity_id=str(fetch.id),
        user_id=user_id,
        after={
            **result.as_dict(),
            "provider": parsed.provider_code,
            "series": list(parsed.series_seen),
            "revised": sorted(f"{s}@{d.isoformat()}" for s, d in revised_rates | revised_fx)[:200],
            "issues": [i.as_dict() for i in parsed.issues[:50]],
        },
    )
    db.flush()
    return result


def fetch_rates(
    db: Session,
    adapter: RatesAdapter,
    client: ProviderClient,
    url: str,
    params: dict[str, Any],
    raw_root: Path,
) -> RatesStoreResult:
    """Fetch (archived first), parse and store. ``client`` must come from the provider's
    gate (``treasury_client`` / ``boc_client``), which refuses while it is disabled."""
    if client.provider_code != adapter.code:
        raise RatesError("Client and adapter belong to different providers")
    fetch, payload = fetch_and_archive(db, client, raw_root, url, params)
    if payload is None:
        raise RatesError(f"Fetch failed: {fetch.error}")
    if fetch.http_status != HTTP_OK:
        raise RatesError(f"HTTP {fetch.http_status}; payload archived as fetch {fetch.id}")
    return store_parsed(db, adapter.parse(payload), fetch)


def load_archived(db: Session, fetch_id: uuid.UUID, raw_root: Path) -> tuple[ProviderFetch, bytes]:
    fetch = db.get(ProviderFetch, fetch_id)
    if fetch is None or fetch.payload_path is None or fetch.content_sha256 is None:
        raise RatesError(f"No archived payload for fetch {fetch_id}")
    return fetch, archive.load(raw_root, fetch.payload_path, fetch.content_sha256)


def ingest_archived(
    db: Session,
    adapter: RatesAdapter,
    fetch_id: uuid.UUID,
    raw_root: Path,
    *,
    today: date | None = None,
    user_id: uuid.UUID | None = None,
) -> RatesStoreResult:
    """Re-parse an archived payload (hash-verified) and store it — no network access."""
    fetch, payload = load_archived(db, fetch_id, raw_root)
    if fetch.provider_code != adapter.code:
        raise RatesError(f"Fetch {fetch_id} is from {fetch.provider_code}, not {adapter.code}")
    return store_parsed(db, adapter.parse(payload, today), fetch, user_id=user_id)


def current_rates(
    db: Session, series_code: str, start: date | None = None, end: date | None = None
) -> list[RateObservationRow]:
    """Latest observation per date of one series (a revision supersedes, never mixes)."""
    conditions = [RateObservationRow.series_code == series_code]
    if start is not None:
        conditions.append(RateObservationRow.obs_date >= start)
    if end is not None:
        conditions.append(RateObservationRow.obs_date <= end)
    latest = (
        select(func.max(RateObservationRow.id).label("id"))
        .where(*conditions)
        .group_by(RateObservationRow.obs_date)
        .subquery()
    )
    return list(
        db.scalars(
            select(RateObservationRow)
            .join(latest, latest.c.id == RateObservationRow.id)
            .order_by(RateObservationRow.obs_date)
        )
    )


def current_fx(
    db: Session,
    base: str,
    quote: str,
    start: date | None = None,
    end: date | None = None,
    provider_code: str | None = None,
) -> list[FxRate]:
    """Latest stored rate per date for exactly ``base``→``quote`` (quote per 1 base).

    The reverse direction is **not** derived: asking for CAD→USD when only USD→CAD is
    stored returns nothing (currency conversion is out of scope for M2)."""
    conditions = [FxRate.base == base.upper(), FxRate.quote == quote.upper()]
    if provider_code is not None:
        conditions.append(FxRate.provider_code == provider_code)
    if start is not None:
        conditions.append(FxRate.obs_date >= start)
    if end is not None:
        conditions.append(FxRate.obs_date <= end)
    latest = (
        select(func.max(FxRate.id).label("id"))
        .where(*conditions)
        .group_by(FxRate.provider_code, FxRate.obs_date)
        .subquery()
    )
    return list(
        db.scalars(
            select(FxRate)
            .join(latest, latest.c.id == FxRate.id)
            .order_by(FxRate.obs_date, FxRate.provider_code)
        )
    )
