"""SEC EDGAR ingestion service (Blueprint §4.2–§4.4).

Each step follows the same pattern:
  1. fetch → archive raw bytes → log the fetch, and **commit** (the log survives failures);
  2. parse the archived bytes;
  3. write domain rows (companies, filings, append-only facts) and audit, then commit.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.ingestion import archive, coverage
from erp_backend.ingestion.http import ProviderClient, fetch_and_archive
from erp_backend.ingestion.sec import SEC_DATA_BASE, SEC_WWW_BASE
from erp_backend.ingestion.sec_parse import (
    EXCHANGE_MIC,
    RawFact,
    SecParseError,
    Submission,
    parse_companyfacts,
    parse_submissions,
    parse_ticker_map,
)
from erp_backend.models import (
    Company,
    CompanyIdentifier,
    Filing,
    ProviderFetch,
    ReportedFact,
    SecTicker,
    Security,
)

log = structlog.get_logger("erp.sec")

TICKER_MAP_URL = f"{SEC_WWW_BASE}/files/company_tickers_exchange.json"
TICKER_MAP_MAX_AGE = timedelta(days=1)
BATCH = 2000
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
ISO_CURRENCIES = frozenset(
    {
        "USD",
        "CAD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "AUD",
        "CNY",
        "HKD",
        "SEK",
        "NOK",
        "DKK",
        "INR",
        "BRL",
        "MXN",
        "KRW",
        "TWD",
        "ZAR",
    }
)


class SecIngestError(Exception):
    pass


def submissions_url(cik: int) -> str:
    return f"{SEC_DATA_BASE}/submissions/CIK{cik:010d}.json"


def companyfacts_url(cik: int) -> str:
    return f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json"


def _fetch(
    db: Session, client: ProviderClient, raw_root: Path, url: str
) -> tuple[ProviderFetch, bytes]:
    row, payload = fetch_and_archive(db, client, raw_root, url)
    db.commit()  # the fetch log is committed before any parsing
    if payload is None:
        raise SecIngestError(f"Fetch failed: {row.error}")
    if row.http_status != 200:
        raise SecIngestError(f"SEC returned HTTP {row.http_status} for {row.url}")
    return row, payload


# ---- Ticker map --------------------------------------------------------------------------


def refresh_ticker_map(db: Session, client: ProviderClient, raw_root: Path) -> int:
    fetch, payload = _fetch(db, client, raw_root, TICKER_MAP_URL)
    try:
        rows = parse_ticker_map(payload)
    except SecParseError as exc:
        raise SecIngestError(f"Ticker map unparseable (fetch {fetch.id}): {exc}") from exc
    seen: set[tuple[str, int]] = set()
    values = []
    for r in rows:
        if (r.ticker, r.cik) in seen:
            continue
        seen.add((r.ticker, r.cik))
        values.append(
            {
                "fetch_id": fetch.id,
                "cik": r.cik,
                "ticker": r.ticker,
                "name": r.name,
                "exchange": r.exchange,
            }
        )
    for i in range(0, len(values), BATCH):
        db.execute(insert(SecTicker), values[i : i + BATCH])
    db.commit()
    log.info("sec_ticker_map_refreshed", rows=len(values), fetch_id=str(fetch.id))
    return len(values)


def latest_ticker_fetch(db: Session) -> ProviderFetch | None:
    return db.scalar(
        select(ProviderFetch)
        .where(ProviderFetch.id.in_(select(SecTicker.fetch_id).distinct()))
        .order_by(ProviderFetch.retrieved_at.desc())
        .limit(1)
    )


def resolve_cik(db: Session, ticker: str) -> int:
    fetch = latest_ticker_fetch(db)
    if fetch is None:
        raise SecIngestError("No SEC ticker map snapshot; refresh it first.")
    ciks = db.scalars(
        select(SecTicker.cik).where(
            SecTicker.fetch_id == fetch.id, SecTicker.ticker == ticker.upper()
        )
    ).all()
    if not ciks:
        raise SecIngestError(f"Ticker {ticker.upper()} not found in the SEC ticker map.")
    if len(set(ciks)) > 1:
        raise SecIngestError(f"Ticker {ticker.upper()} maps to several CIKs: {sorted(ciks)}")
    return ciks[0]


# ---- Submissions (profile, listings, filings) ---------------------------------------------


def _profile(sub: Submission) -> dict[str, Any]:
    return {
        "legal_name": sub.name,
        "sic_code": sub.sic,
        "sic_description": sub.sic_description,
        "fiscal_year_end_mmdd": sub.fiscal_year_end_mmdd,
        "state_of_incorporation": sub.state_of_incorporation,
        "country": sub.country,
    }


def company_for_cik(db: Session, cik: int) -> Company | None:
    ident = db.get(CompanyIdentifier, ("SEC_CIK", f"{cik:010d}"))
    return db.get(Company, ident.company_id) if ident else None


def ingest_submissions(db: Session, client: ProviderClient, raw_root: Path, cik: int) -> Company:
    fetch, payload = _fetch(db, client, raw_root, submissions_url(cik))
    try:
        sub = parse_submissions(payload)
    except SecParseError as exc:
        raise SecIngestError(f"Submissions unparseable (fetch {fetch.id}): {exc}") from exc
    if sub.cik != cik:
        raise SecIngestError(f"Submissions CIK {sub.cik} does not match requested {cik}.")
    company = _apply_profile(db, cik, sub, reason=f"SEC submissions fetch {fetch.id}")
    _apply_listings_and_filings(db, company, sub, fetch.id)
    db.commit()
    return company


def _apply_profile(db: Session, cik: int, sub: Submission, *, reason: str) -> Company:
    profile = _profile(sub)
    company = company_for_cik(db, cik)
    if company is None:
        company = Company(**profile, common_name=sub.name)
        db.add(company)
        db.flush()
        db.add(CompanyIdentifier(scheme="SEC_CIK", value=f"{cik:010d}", company_id=company.id))
        audit.record(
            db,
            action="company_created",
            entity_type="company",
            entity_id=str(company.id),
            after={**profile, "cik": cik},
            reason=reason,
        )
        return company
    before = {k: getattr(company, k) for k in profile}
    changed = {k: v for k, v in profile.items() if before[k] != v}
    if changed:
        for k, v in changed.items():
            setattr(company, k, v)
        audit.record(
            db,
            action="company_profile_updated",
            entity_type="company",
            entity_id=str(company.id),
            before={k: before[k] for k in changed},
            after=changed,
            reason=reason,
        )
    return company


def _apply_listings_and_filings(
    db: Session, company: Company, sub: Submission, fetch_id: uuid.UUID
) -> None:
    for i, (ticker, exchange) in enumerate(zip(sub.tickers, sub.exchanges, strict=False)):
        db.execute(
            insert(Security)
            .values(
                id=uuid.uuid4(),
                company_id=company.id,
                ticker=ticker,
                exchange_name=exchange or "",
                exchange_mic=EXCHANGE_MIC.get(exchange),
                is_primary=(i == 0),
                provider_symbols={"sec": ticker},
            )
            .on_conflict_do_nothing(constraint="uq_securities_listing")
        )
    _insert_filings(
        db,
        company.id,
        fetch_id,
        (
            {
                "accession_no": f.accession_no,
                "form": f.form,
                "filed_date": f.filed_date,
                "period_of_report": f.period_of_report,
                "primary_document": f.primary_document,
            }
            for f in sub.filings
        ),
    )


def _insert_filings(
    db: Session, company_id: uuid.UUID, fetch_id: uuid.UUID, rows: Iterable[dict[str, Any]]
) -> None:
    values = [
        {"id": uuid.uuid4(), "company_id": company_id, "fetch_id": fetch_id, **r} for r in rows
    ]
    for i in range(0, len(values), BATCH):
        db.execute(
            insert(Filing).on_conflict_do_nothing(index_elements=["accession_no"]),
            values[i : i + BATCH],
        )


# ---- Company facts -------------------------------------------------------------------------


@dataclass(frozen=True)
class FactsSummary:
    observations: int
    inserted: int
    taxonomies: tuple[str, ...]
    accounting_standard: str | None
    reporting_currency: str | None
    interim_coverage: str


def infer_standard(facts: list[RawFact]) -> str | None:
    taxonomies = {f.taxonomy for f in facts}
    if "ifrs-full" in taxonomies:
        return "IFRS"
    if "us-gaap" in taxonomies:
        return "US_GAAP"
    return None


def infer_currency(facts: list[RawFact]) -> str | None:
    """Most common ISO currency in the latest periodic filing (annual forms preferred;
    otherwise interim forms, e.g. 6-K filers). Registration forms (F-10, S-1…) are ignored."""
    for forms in (ANNUAL_FORMS, coverage.INTERIM_FORMS):
        periodic = [f for f in facts if f.form.upper() in forms and f.unit in ISO_CURRENCIES]
        if periodic:
            latest = max(f.filed_date for f in periodic)
            counts = Counter(f.unit for f in periodic if f.filed_date == latest)
            return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return None


def ingest_companyfacts(
    db: Session, client: ProviderClient, raw_root: Path, company: Company, cik: int
) -> FactsSummary:
    fetch, payload = _fetch(db, client, raw_root, companyfacts_url(cik))
    try:
        facts_cik, facts = parse_companyfacts(payload)
    except SecParseError as exc:
        raise SecIngestError(f"companyfacts unparseable (fetch {fetch.id}): {exc}") from exc
    if facts_cik != cik:
        raise SecIngestError(f"companyfacts CIK {facts_cik} does not match requested {cik}.")
    first_seen: dict[str, RawFact] = {}
    for f in facts:
        first_seen.setdefault(f.accession_no, f)
    _insert_filings(
        db,
        company.id,
        fetch.id,
        (
            {
                "accession_no": a,
                "form": f.form,
                "filed_date": f.filed_date,
                "period_of_report": None,
                "primary_document": None,
            }
            for a, f in first_seen.items()
        ),
    )
    before = (
        db.scalar(
            select(func.count())
            .select_from(ReportedFact)
            .where(ReportedFact.company_id == company.id)
        )
        or 0
    )
    values = [
        {
            "company_id": company.id,
            "taxonomy": f.taxonomy,
            "concept": f.concept,
            "unit": f.unit,
            "value": f.value,
            "period_start": f.period_start,
            "period_end": f.period_end,
            "fiscal_year": f.fiscal_year,
            "fiscal_period": f.fiscal_period,
            "form": f.form,
            "filed_date": f.filed_date,
            "accession_no": f.accession_no,
            "frame": f.frame,
            "fetch_id": fetch.id,
        }
        for f in facts
    ]
    for i in range(0, len(values), BATCH):
        db.execute(insert(ReportedFact).on_conflict_do_nothing(), values[i : i + BATCH])
    after = (
        db.scalar(
            select(func.count())
            .select_from(ReportedFact)
            .where(ReportedFact.company_id == company.id)
        )
        or 0
    )
    standard, currency, level = _apply_inferences(
        db, company, facts, reason=f"SEC companyfacts fetch {fetch.id} (inferred, unconfirmed)"
    )
    db.commit()
    return FactsSummary(
        len(facts),
        after - before,
        tuple(sorted({f.taxonomy for f in facts})),
        standard,
        currency,
        level,
    )


def _apply_inferences(
    db: Session, company: Company, facts: list[RawFact], *, reason: str
) -> tuple[str | None, str | None, str]:
    """Accounting standard, reporting currency (while the profile is unconfirmed) and interim
    coverage, derived from facts; changes are audited."""
    standard, currency = infer_standard(facts), infer_currency(facts)
    cov = coverage.detect(facts)
    changes: dict[str, Any] = {}
    if company.accounting_standard_inferred:
        if standard and company.accounting_standard != standard:
            changes["accounting_standard"] = standard
        if currency and company.reporting_currency != currency:
            changes["reporting_currency"] = currency
    if company.interim_coverage != cov.level:
        changes["interim_coverage"] = cov.level
    before_vals = {k: getattr(company, k) for k in changes}
    for k, v in changes.items():
        setattr(company, k, v)
    company.interim_coverage_details = cov.details()
    company.interim_coverage_as_of = datetime.now(UTC)
    if changes:
        audit.record(
            db,
            action="company_inferred_fields_updated",
            entity_type="company",
            entity_id=str(company.id),
            before=before_vals,
            after=changes,
            reason=reason,
        )
    return standard, currency, cov.level


# ---- Orchestration -------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestSummary:
    cik: int
    company_id: uuid.UUID
    legal_name: str
    facts: FactsSummary


def ticker_map_is_stale(db: Session, now: datetime | None = None) -> bool:
    fetch = latest_ticker_fetch(db)
    current = now or datetime.now(UTC)
    return fetch is None or current - fetch.retrieved_at > TICKER_MAP_MAX_AGE


def ingest_company(
    db: Session,
    client: ProviderClient,
    raw_root: Path,
    *,
    ticker: str | None = None,
    cik: int | None = None,
) -> IngestSummary:
    if (ticker is None) == (cik is None):
        raise SecIngestError("Provide exactly one of ticker or cik.")
    if cik is None:
        assert ticker is not None
        if ticker_map_is_stale(db):
            refresh_ticker_map(db, client, raw_root)
        cik = resolve_cik(db, ticker)
    company = ingest_submissions(db, client, raw_root, cik)
    facts = ingest_companyfacts(db, client, raw_root, company, cik)
    log.info(
        "sec_company_ingested",
        cik=cik,
        company_id=str(company.id),
        facts=facts.observations,
        inserted=facts.inserted,
        coverage=facts.interim_coverage,
    )
    return IngestSummary(cik, company.id, company.legal_name, facts)


# ---- Rebuild from the archive (no network) ---------------------------------------------------


def stored_facts(db: Session, company_id: uuid.UUID) -> list[RawFact]:
    rows = db.scalars(select(ReportedFact).where(ReportedFact.company_id == company_id)).all()
    return [
        RawFact(
            r.taxonomy,
            r.concept,
            r.unit,
            r.value,
            r.period_start,
            r.period_end,
            r.fiscal_year,
            r.fiscal_period,
            r.form,
            r.filed_date,
            r.accession_no,
            r.frame,
        )
        for r in rows
    ]


def rebuild_company_from_archive(db: Session, raw_root: Path, company: Company) -> str:
    """Re-parse the latest archived submissions payload and recompute inferred fields from
    stored facts. Takes no HTTP client: it cannot make a network request."""
    ident = db.scalar(
        select(CompanyIdentifier).where(
            CompanyIdentifier.company_id == company.id, CompanyIdentifier.scheme == "SEC_CIK"
        )
    )
    if ident is None:
        raise SecIngestError(f"Company {company.id} has no SEC CIK.")
    cik = int(ident.value)
    fetch = db.scalar(
        select(ProviderFetch)
        .where(ProviderFetch.url == submissions_url(cik), ProviderFetch.http_status == 200)
        .order_by(ProviderFetch.retrieved_at.desc())
        .limit(1)
    )
    if fetch is None or fetch.payload_path is None or fetch.content_sha256 is None:
        raise SecIngestError(f"No archived submissions payload for CIK {cik}.")
    sub = parse_submissions(archive.load(raw_root, fetch.payload_path, fetch.content_sha256))
    reason = f"Rebuilt from archived fetch {fetch.id} (no network)"
    _apply_profile(db, cik, sub, reason=reason)
    _apply_listings_and_filings(db, company, sub, fetch.id)
    _, _, level = _apply_inferences(db, company, stored_facts(db, company.id), reason=reason)
    db.commit()
    return level
