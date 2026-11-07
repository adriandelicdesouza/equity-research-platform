"""Companies, listings, identifiers and classifications (read-only)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from erp_backend.classifications import effective_classification
from erp_backend.models import (
    Company,
    CompanyClassification,
    CompanyIdentifier,
    ListingRegistration,
    Security,
)
from erp_backend.read.common import not_found, paginate
from erp_backend.read.schemas import (
    ClassificationOut,
    ClassificationStatusOut,
    CompanyDetailOut,
    CompanyOut,
    IdentifierOut,
    ListingOut,
    Page,
)

COMPANY_SORTS = {
    "name": (func.lower(Company.legal_name), Company.id),
    "-name": (func.lower(Company.legal_name).desc(), Company.id),
    "created": (Company.created_at, Company.id),
    "-created": (Company.created_at.desc(), Company.id),
}


def _tickers(db: Session, company_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = defaultdict(list)
    if company_ids:
        for cid, ticker, mic in db.execute(
            select(Security.company_id, Security.ticker, Security.exchange_mic)
            .where(Security.company_id.in_(company_ids))
            .order_by(Security.ticker, Security.exchange_mic)
        ):
            out[cid].append(f"{ticker}:{mic}" if mic else ticker)
    return out


def _company(c: Company, tickers: list[str]) -> CompanyOut:
    return CompanyOut(
        id=c.id,
        legal_name=c.legal_name,
        common_name=c.common_name,
        country=c.country,
        reporting_currency=c.reporting_currency,
        fiscal_year_end_mmdd=c.fiscal_year_end_mmdd,
        accounting_standard=c.accounting_standard,
        accounting_standard_inferred=bool(c.accounting_standard_inferred),
        financial_classification=effective_classification(c),
        status=c.status,
        tickers=tickers,
    )


def list_companies(
    db: Session,
    *,
    q: str | None,
    country: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> Page[CompanyOut]:
    stmt = select(Company)
    if q:
        pattern = f"%{q.strip().lower()}%"
        listed = select(Security.company_id).where(func.lower(Security.ticker) == q.strip().lower())
        stmt = stmt.where(
            or_(
                func.lower(Company.legal_name).like(pattern),
                func.lower(func.coalesce(Company.common_name, "")).like(pattern),
                Company.id.in_(listed),
            )
        )
    if country:
        stmt = stmt.where(Company.country == country.upper())
    stmt = stmt.order_by(*COMPANY_SORTS[sort])

    def mapper(rows: Sequence[Company]) -> list[CompanyOut]:
        tickers = _tickers(db, [c.id for c in rows])
        return [_company(c, tickers[c.id]) for c in rows]

    return paginate(db, stmt, limit, offset, mapper)


def _listings(db: Session, securities: Sequence[Security]) -> list[ListingOut]:
    ids = [s.id for s in securities]
    registrations = {
        r.security_id: r
        for r in db.scalars(
            select(ListingRegistration).where(ListingRegistration.security_id.in_(ids))
        )
    } if ids else {}  # fmt: skip
    return [
        ListingOut(
            id=s.id,
            company_id=s.company_id,
            ticker=s.ticker,
            exchange_mic=s.exchange_mic,
            exchange_name=s.exchange_name,
            trading_currency=s.trading_currency,
            is_primary=bool(s.is_primary),
            security_type=s.security_type,
            share_class=s.share_class,
            isin=s.isin,
            provider_symbols=dict(s.provider_symbols or {}),
            origin="manual_registration" if s.id in registrations else "sec",
            registration_source=registrations[s.id].source_name if s.id in registrations else None,
        )
        for s in securities
    ]


def _classification(c: CompanyClassification | None) -> ClassificationOut | None:
    if c is None:
        return None
    return ClassificationOut(
        id=c.id,
        kind=c.kind,
        classification=c.classification,
        classifier_version=c.classifier_version,
        reason=c.reason,
        evidence=c.evidence,
        created_at=c.created_at,
    )


def classification_status(db: Session, company: Company) -> ClassificationStatusOut:
    latest: dict[str, CompanyClassification] = {}
    for row in db.scalars(
        select(CompanyClassification)
        .where(CompanyClassification.company_id == company.id)
        .order_by(CompanyClassification.id)
    ):
        latest[row.kind] = row
    confirmed, suggested = latest.get("confirmed"), latest.get("suggested")
    return ClassificationStatusOut(
        effective=effective_classification(company),
        status="confirmed" if confirmed else ("suggested" if suggested else "none"),
        latest_suggestion=_classification(suggested),
        latest_confirmation=_classification(confirmed),
    )


def get_company(db: Session, company_id: uuid.UUID) -> CompanyDetailOut:
    c = db.get(Company, company_id)
    if c is None:
        raise not_found("company", company_id)
    securities = db.scalars(
        select(Security).where(Security.company_id == c.id).order_by(Security.ticker, Security.id)
    ).all()
    identifiers = db.scalars(
        select(CompanyIdentifier)
        .where(CompanyIdentifier.company_id == c.id)
        .order_by(CompanyIdentifier.scheme, CompanyIdentifier.value)
    ).all()
    base = _company(c, _tickers(db, [c.id])[c.id])
    return CompanyDetailOut(
        **base.model_dump(),
        sic_code=c.sic_code,
        sic_description=c.sic_description,
        state_of_incorporation=c.state_of_incorporation,
        interim_coverage=c.interim_coverage,
        identifiers=[IdentifierOut(scheme=i.scheme, value=i.value) for i in identifiers],
        listings=_listings(db, securities),
        classification=classification_status(db, c),
        created_at=c.created_at,
    )


def list_classifications(
    db: Session, company_id: uuid.UUID, *, limit: int, offset: int
) -> Page[ClassificationOut]:
    if db.get(Company, company_id) is None:
        raise not_found("company", company_id)
    stmt = (
        select(CompanyClassification)
        .where(CompanyClassification.company_id == company_id)
        .order_by(CompanyClassification.id.desc())
    )
    return paginate(db, stmt, limit, offset, lambda rows: [_classification(r) for r in rows])  # type: ignore[misc]


def list_listings(
    db: Session,
    *,
    company_id: uuid.UUID | None,
    ticker: str | None,
    mic: str | None,
    limit: int,
    offset: int,
) -> Page[ListingOut]:
    stmt = select(Security)
    if company_id is not None:
        stmt = stmt.where(Security.company_id == company_id)
    if ticker:
        stmt = stmt.where(func.upper(Security.ticker) == ticker.strip().upper())
    if mic:
        stmt = stmt.where(Security.exchange_mic == mic.strip().upper())
    stmt = stmt.order_by(Security.ticker, Security.exchange_mic, Security.id)
    return paginate(db, stmt, limit, offset, lambda rows: _listings(db, rows))


def get_listing(db: Session, security_id: uuid.UUID) -> ListingOut:
    s = db.get(Security, security_id)
    if s is None:
        raise not_found("listing", security_id)
    return _listings(db, [s])[0]
