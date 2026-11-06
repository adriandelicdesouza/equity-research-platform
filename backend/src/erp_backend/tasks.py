"""Background tasks. SEC tasks share the lock "sec" so they never overlap (rate limit)."""

from __future__ import annotations

import structlog

from erp_backend.config import get_settings
from erp_backend.db import new_session
from erp_backend.jobs import DEFAULT_QUEUE, INGESTION_QUEUE, app

log = structlog.get_logger("erp.tasks")


@app.task(name="system.ping", queue=DEFAULT_QUEUE)
def ping(message: str) -> str:
    """End-to-end queue check: enqueue → worker → completion."""
    log.info("ping", message=message)
    return message


@app.task(name="sec.refresh_ticker_map", queue=INGESTION_QUEUE, lock="sec")
def sec_refresh_ticker_map() -> int:
    from erp_backend.ingestion.sec import sec_client
    from erp_backend.ingestion.sec_ingest import refresh_ticker_map

    settings = get_settings()
    client = sec_client(settings)
    with new_session() as db:
        try:
            return refresh_ticker_map(db, client, settings.raw_root)
        finally:
            client.close()


@app.task(name="sec.ingest_company", queue=INGESTION_QUEUE, lock="sec")
def sec_ingest_company(ticker: str | None = None, cik: int | None = None) -> str:
    from erp_backend.ingestion.sec import sec_client
    from erp_backend.ingestion.sec_ingest import ingest_company

    settings = get_settings()
    client = sec_client(settings)
    with new_session() as db:
        try:
            summary = ingest_company(db, client, settings.raw_root, ticker=ticker, cik=cik)
        finally:
            client.close()
    return (
        f"{summary.legal_name}: {summary.facts.observations} facts, coverage "
        f"{summary.facts.interim_coverage}"
    )
