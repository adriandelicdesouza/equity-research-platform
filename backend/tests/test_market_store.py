"""Market-data persistence against the test database. Network is never used: the Yahoo
client is gated off, and the enabled path runs on an ``httpx.MockTransport``."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from erp_backend.ingestion import archive
from erp_backend.ingestion.http import ProviderClient, fetch_and_archive
from erp_backend.market.store import (
    MarketDataError,
    current_prices,
    fetch_history,
    ingest_archived,
    store_history,
)
from erp_backend.market.yahoo import (
    ProviderNotEnabledError,
    YahooChart,
    parse_chart,
    yahoo_client,
)
from erp_backend.models import (
    AuditEvent,
    Company,
    CorporateAction,
    PriceObservation,
    ProviderFetch,
    ProviderListing,
    Security,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from . import yahoo_payloads as y

DAYS = y.weekdays(date(2024, 3, 4), 4)
PAYLOAD = y.chart(DAYS, [100.0, 101.0, 102.0, 103.0], adj=[98.0, 99.0, 100.0, 103.0],
                  splits=[(DAYS[2], 2, 1)], dividends=[(DAYS[3], 1.1)])  # fmt: skip


def make_security(db: Session, currency: str | None = "CAD", symbol: str = "RY.TO") -> Security:
    c = Company(legal_name="Market Test Co")
    db.add(c)
    db.flush()
    s = Security(company_id=c.id, ticker="RY", exchange_name="TSX", exchange_mic="XTSE",
                 trading_currency=currency, provider_symbols={"yahoo_chart": symbol})  # fmt: skip
    db.add(s)
    db.flush()
    return s


def archived_fetch(db: Session, root: Path, payload: bytes) -> ProviderFetch:
    """Record a fetch through the real archive path, served by a mock transport."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    client = ProviderClient(
        provider_code="yahoo_chart", user_agent="test", per_second=1e6, transport=transport
    )
    fetch, body = fetch_and_archive(db, client, root, "https://query1.finance.yahoo.com/x")
    assert body == payload
    return fetch


def count(db: Session, model: type, security: Security) -> int:
    query = select(func.count()).select_from(model).where(model.security_id == security.id)  # type: ignore[attr-defined]
    return db.scalar(query) or 0


def test_client_is_refused_while_the_provider_is_disabled(db: Session) -> None:
    with pytest.raises(ProviderNotEnabledError, match="owner's approval"):
        yahoo_client(db)


def test_enabled_path_archives_then_stores_via_mock_transport(
    owner_engine: Engine, tmp_path: Path
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "query1.finance.yahoo.com"  # mock only; never sent
        return httpx.Response(200, content=PAYLOAD, headers={"content-type": "application/json"})

    conn = owner_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        session.execute(
            text("UPDATE source.providers SET enabled = true WHERE code = 'yahoo_chart'")
        )
        security = make_security(session)
        client = yahoo_client(session, transport=httpx.MockTransport(handler))
        result = fetch_history(session, YahooChart(), client, security, DAYS[0], DAYS[-1], tmp_path)
        assert (result.prices_inserted, result.actions_inserted, result.listing_inserted) == (
            4,
            2,
            True,
        )
        assert seen[0].url.path == "/v8/finance/chart/RY.TO"
        assert seen[0].url.params["events"] == "div,splits"
        assert "@" not in seen[0].headers["user-agent"]  # no personal contact data sent
        fetch = session.get(ProviderFetch, result.fetch_id)
        assert fetch is not None and fetch.http_status == 200
        assert archive.load(tmp_path, fetch.payload_path, fetch.content_sha256) == PAYLOAD  # type: ignore[arg-type]
    finally:
        session.close()
        trans.rollback()
        conn.close()


def test_store_preserves_semantics_and_provenance(db: Session, tmp_path: Path) -> None:
    security = make_security(db)
    fetch = archived_fetch(db, tmp_path, PAYLOAD)
    result = store_history(db, security, parse_chart(PAYLOAD), fetch)
    assert (result.prices_inserted, result.actions_inserted) == (4, 2)
    rows = current_prices(db, security.id, "yahoo_chart")
    assert (rows[0].trade_date, rows[0].close, rows[0].adj_close) == (
        DAYS[0], Decimal("100.0"), Decimal("98.0"),
    )  # fmt: skip
    assert {r.close_basis for r in rows} == {"unverified"} and {r.currency for r in rows} == {"CAD"}
    assert all(r.fetch_id == fetch.id for r in rows)
    actions = db.scalars(select(CorporateAction).where(CorporateAction.security_id == security.id)
                         .order_by(CorporateAction.ex_date)).all()  # fmt: skip
    assert [(a.kind, a.ratio_numerator, a.ratio_denominator, a.amount, a.currency_source)
            for a in actions] == [
        ("split", Decimal(2), Decimal(1), None, None),
        ("cash_dividend", None, None, Decimal("1.1"), "assumed_quote_currency"),
    ]  # fmt: skip
    listing = db.scalars(
        select(ProviderListing).where(ProviderListing.security_id == security.id)
    ).one()
    assert (listing.provider_symbol, listing.exchange_code, listing.quote_price) == (
        "RY.TO", "TOR", Decimal("135.21"),
    )  # fmt: skip
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "market_data_stored")).one()
    assert event.after["close_basis"] == "unverified"  # type: ignore[index]


def test_reingestion_is_idempotent_and_revisions_are_kept(db: Session, tmp_path: Path) -> None:
    security = make_security(db)
    fetch = archived_fetch(db, tmp_path, PAYLOAD)
    store_history(db, security, parse_chart(PAYLOAD), fetch)
    again = ingest_archived(db, YahooChart(), security, fetch.id, tmp_path)
    assert (again.prices_inserted, again.prices_already_present) == (0, 4)
    assert (again.actions_inserted, again.listing_inserted) == (0, False)
    # A later fetch revises one adjusted close (e.g. after a dividend): old value kept.
    revised = y.chart(DAYS, [100.0, 101.0, 102.0, 103.0], adj=[97.0, 99.0, 100.0, 103.0],
                      splits=[(DAYS[2], 2, 1)], dividends=[(DAYS[3], 1.1)])  # fmt: skip
    later = archived_fetch(db, tmp_path, revised)
    result = store_history(db, security, parse_chart(revised), later)
    assert (result.prices_inserted, result.prices_already_present) == (1, 3)
    assert count(db, PriceObservation, security) == 5
    first_day = current_prices(db, security.id, "yahoo_chart")[0]
    assert first_day.adj_close == Decimal("97.0") and first_day.fetch_id == later.id


@pytest.mark.parametrize(
    ("currency", "symbol", "payload_meta", "error"),
    [
        ("USD", "RY.TO", {}, "differs from the listing's trading currency"),
        ("CAD", "RY.NE", {}, "listing maps to RY.NE"),
        ("CAD", "RY.TO", {"currency": None}, "no currency"),
    ],
)
def test_listing_mismatches_are_rejected(
    db: Session, tmp_path: Path, currency: str, symbol: str, payload_meta: dict[str, object],
    error: str,
) -> None:  # fmt: skip
    security = make_security(db, currency=currency, symbol=symbol)
    payload = y.chart(DAYS[:1], [100.0], meta_overrides=payload_meta)
    fetch = archived_fetch(db, tmp_path, payload)
    with pytest.raises(MarketDataError, match=error):
        store_history(db, security, parse_chart(payload), fetch)
    assert count(db, PriceObservation, security) == 0


def test_security_without_provider_symbol_is_refused(db: Session, tmp_path: Path) -> None:
    security = make_security(db)
    security.provider_symbols = {}
    db.flush()
    with pytest.raises(MarketDataError, match="no yahoo_chart symbol"):
        ingest_archived(
            db, YahooChart(), security, archived_fetch(db, tmp_path, PAYLOAD).id, tmp_path
        )
    with pytest.raises(MarketDataError, match="No archived payload"):
        ingest_archived(db, YahooChart(), security, uuid.uuid4(), tmp_path)


def test_observations_are_append_only(db: Session, tmp_path: Path) -> None:
    security = make_security(db)
    store_history(db, security, parse_chart(PAYLOAD), archived_fetch(db, tmp_path, PAYLOAD))
    for statement in (
        "UPDATE market.price_observations SET close = 1",
        "DELETE FROM market.corporate_actions",
        "UPDATE market.provider_listings SET currency = 'USD'",
    ):
        with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
            db.execute(text(statement))
