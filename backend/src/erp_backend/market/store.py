"""Persist parsed provider histories; fetch through a gated client; re-ingest from the archive.

Idempotent and append-only (migration 0010): exact repeats insert nothing, changed values
are kept as new observations. Before anything is stored the payload must match the listing
it is stored for: the provider symbol (``securities.provider_symbols[provider]``) and the
trading currency (when the security has one). A mismatch is an error, never a silent
conversion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.ingestion import archive
from erp_backend.ingestion.http import ProviderClient, fetch_and_archive
from erp_backend.market.model import MarketDataProvider, PriceHistory
from erp_backend.models import (
    CorporateAction,
    PriceObservation,
    ProviderFetch,
    ProviderListing,
    Security,
)


class MarketDataError(Exception):
    pass


@dataclass(frozen=True)
class StoreResult:
    fetch_id: uuid.UUID
    prices_inserted: int
    prices_already_present: int
    actions_inserted: int
    actions_already_present: int
    listing_inserted: bool
    rejected_observations: int
    warnings: int


def _check_listing(security: Security, history: PriceHistory) -> None:
    expected = (security.provider_symbols or {}).get(history.provider_code)
    if expected is None:
        raise MarketDataError(
            f"Security {security.ticker} has no {history.provider_code} symbol; set it first (D15)"
        )
    if history.meta.provider_symbol.upper() != expected.upper():
        raise MarketDataError(
            f"Payload is for {history.meta.provider_symbol}, the listing maps to {expected}"
        )
    if history.meta.currency is None:
        raise MarketDataError("Payload has no currency; prices cannot be stored")
    if security.trading_currency and history.meta.currency != security.trading_currency:
        raise MarketDataError(
            f"Payload currency {history.meta.currency} differs from the listing's trading "
            f"currency {security.trading_currency}; not stored (no silent conversion)"
        )


def _insert(db: Session, model: type, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    # RETURNING counts exactly the rows inserted (rowcount is unreliable for batched inserts).
    statement = insert(model).values(rows).on_conflict_do_nothing().returning(model.id)  # type: ignore[attr-defined]
    return len(db.execute(statement).all())


def store_history(
    db: Session,
    security: Security,
    history: PriceHistory,
    fetch: ProviderFetch,
    *,
    user_id: uuid.UUID | None = None,
) -> StoreResult:
    if fetch.provider_code != history.provider_code:
        raise MarketDataError("Fetch and payload come from different providers")
    _check_listing(security, history)
    base = {
        "security_id": security.id,
        "provider_code": history.provider_code,
        "fetch_id": fetch.id,
    }
    currency = history.meta.currency
    prices = [
        {
            **base,
            "trade_date": b.trade_date,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "adj_close": b.adj_close,
            "volume": b.volume,
            "currency": currency,
            "close_basis": history.close_basis.value,
            "adj_close_basis": history.adj_close_basis.value,
        }
        for b in history.bars
    ]
    actions = [
        {
            **base,
            "kind": "split",
            "ex_date": s.ex_date,
            "ratio_numerator": s.numerator,
            "ratio_denominator": s.denominator,
            "amount": None,
            "currency": None,
            "currency_source": None,
        }
        for s in history.splits
    ] + [
        {
            **base,
            "kind": "cash_dividend",
            "ex_date": d.ex_date,
            "ratio_numerator": None,
            "ratio_denominator": None,
            "amount": d.amount,
            "currency": d.currency,
            "currency_source": d.currency_source,
        }
        for d in history.dividends
    ]
    m = history.meta
    listing = {
        **base,
        "provider_symbol": m.provider_symbol,
        "currency": m.currency,
        "exchange_code": m.exchange_code,
        "exchange_name": m.exchange_name,
        "exchange_timezone": m.exchange_timezone,
        "instrument_type": m.instrument_type,
        "first_trade_date": m.first_trade_date,
        "quote_price": m.quote_price,
        "quote_time": m.quote_time,
        "raw": dict(m.raw),
    }
    inserted_prices = _insert(db, PriceObservation, prices)
    inserted_actions = _insert(db, CorporateAction, actions)
    listing_inserted = _insert(db, ProviderListing, [listing]) == 1
    result = StoreResult(
        fetch_id=fetch.id,
        prices_inserted=inserted_prices,
        prices_already_present=len(prices) - inserted_prices,
        actions_inserted=inserted_actions,
        actions_already_present=len(actions) - inserted_actions,
        listing_inserted=listing_inserted,
        rejected_observations=len(history.errors),
        warnings=len(history.issues) - len(history.errors),
    )
    audit.record(
        db,
        action="market_data_stored",
        entity_type="security",
        entity_id=str(security.id),
        user_id=user_id,
        after={
            "provider": history.provider_code,
            "fetch_id": str(fetch.id),
            "prices_inserted": inserted_prices,
            "actions_inserted": inserted_actions,
            "close_basis": history.close_basis.value,
            "issues": [i.as_dict() for i in history.issues[:50]],
        },
    )
    db.flush()
    return result


def fetch_history(
    db: Session,
    provider: MarketDataProvider,
    client: ProviderClient,
    security: Security,
    start: date,
    end: date,
    raw_root: Path,
) -> StoreResult:
    """Fetch (archived first), parse and store. Requires a client from the provider's gate."""
    symbol = (security.provider_symbols or {}).get(provider.code)
    if symbol is None:
        raise MarketDataError(f"Security {security.ticker} has no {provider.code} symbol")
    url, params = provider.history_url(symbol, start, end)
    fetch, payload = fetch_and_archive(db, client, raw_root, url, params)
    if payload is None:
        raise MarketDataError(f"Fetch failed: {fetch.error}")
    return store_history(db, security, provider.parse_history(payload), fetch)


def ingest_archived(
    db: Session,
    provider: MarketDataProvider,
    security: Security,
    fetch_id: uuid.UUID,
    raw_root: Path,
) -> StoreResult:
    """Re-parse an archived payload (hash-verified) and store it — no network access."""
    fetch = db.get(ProviderFetch, fetch_id)
    if fetch is None or fetch.payload_path is None or fetch.content_sha256 is None:
        raise MarketDataError(f"No archived payload for fetch {fetch_id}")
    payload = archive.load(raw_root, fetch.payload_path, fetch.content_sha256)
    return store_history(db, security, provider.parse_history(payload), fetch)


def current_prices(
    db: Session, security_id: uuid.UUID, provider_code: str
) -> list[PriceObservation]:
    """Latest observation per trade date (by fetch order of first delivery)."""
    latest = (
        select(PriceObservation.trade_date, func.max(PriceObservation.id).label("id"))
        .where(
            PriceObservation.security_id == security_id,
            PriceObservation.provider_code == provider_code,
        )
        .group_by(PriceObservation.trade_date)
        .subquery()
    )
    return list(
        db.scalars(
            select(PriceObservation)
            .join(latest, latest.c.id == PriceObservation.id)
            .order_by(PriceObservation.trade_date)
        )
    )
