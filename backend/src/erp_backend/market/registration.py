"""Manual listing registration: check (read-only), then register explicitly (§4.3 step 3; D15, D56).

Identity model (unchanged, D56):

* **issuer** — ``market.companies``;
* **listing** — one ``market.securities`` row = one ticker on one exchange (MIC), with its
  trading currency and provider symbols. A cross-listed share (RY on XTSE and XNYS) is two
  listings of one issuer; ``isin`` + ``share_class`` + ``security_type`` describe the
  instrument a listing represents, and the same ISIN may only appear under one issuer.

Nothing is guessed. The analyst states ticker, MIC, trading currency, security type and how
the issuer is resolved (an existing company, an existing listing of the same issuer, an SEC
CIK, or an explicitly new issuer). Provider symbols are stored only when given; the
documented Yahoo rule is shown as a suggestion and a differing explicit symbol is a warning.
A proposal that may duplicate an issuer or listing is refused until resolved explicitly.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.market.exchanges import EXCHANGES
from erp_backend.market.listings import (
    ListingError,
    check_currency,
    find_listings,
    normalize_mic,
    normalize_ticker,
)
from erp_backend.market.yahoo import MIC_SUFFIX
from erp_backend.market.yahoo import provider_symbol as yahoo_symbol
from erp_backend.models import (
    Company,
    CompanyIdentifier,
    CorporateAction,
    ListingRegistration,
    PriceObservation,
    Provider,
    Security,
)

SECURITY_TYPES = ("common_equity", "preferred_equity", "depositary_receipt", "fund_unit", "other")
MIN_REASON = 10
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
_SHARE_CLASS = re.compile(r"^[A-Z0-9]{1,16}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_PROVIDER_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-^=]{0,31}$")


class RegistrationRefusedError(Exception):
    """Registration refused; nothing was written."""


def isin_is_valid(code: str) -> bool:
    """ISO 6166 format and check digit (letters → 10..35, then Luhn over the digit string)."""
    if not _ISIN.fullmatch(code):
        return False
    digits = "".join(str(int(c, 36)) for c in code)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class NewIssuer:
    legal_name: str
    country: str
    common_name: str | None = None
    reporting_currency: str | None = None


@dataclass(frozen=True)
class IssuerRef:
    """Exactly one of these must be set."""

    company_id: uuid.UUID | None = None
    same_issuer_as: tuple[str, str] | None = None  # (ticker, MIC) of an existing listing
    cik: int | None = None
    new: NewIssuer | None = None


@dataclass(frozen=True)
class ListingProposal:
    ticker: str
    mic: str
    currency: str
    security_type: str
    issuer: IssuerRef
    source_name: str
    reason: str
    share_class: str | None = None
    isin: str | None = None
    source_reference: str | None = None
    provider_symbols: Mapping[str, str] = field(default_factory=dict)
    confirm_new_issuer: bool = False


@dataclass(frozen=True)
class Finding:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class RegistrationCheck:
    listing: dict[str, Any]  # normalized proposal
    issuer: dict[str, Any]  # how the issuer resolved
    suggested_provider_symbols: dict[str, str]
    errors: tuple[Finding, ...]
    warnings: tuple[Finding, ...]
    company: Company | None = field(default=None, compare=False, repr=False)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "listing": self.listing,
            "issuer": self.issuer,
            "suggested_provider_symbols": self.suggested_provider_symbols,
            "errors": [e.as_dict() for e in self.errors],
            "warnings": [w.as_dict() for w in self.warnings],
            "ok": self.ok,
        }


@dataclass(frozen=True)
class RegistrationResult:
    security: Security
    company: Company
    company_created: bool
    registration_id: uuid.UUID
    check: RegistrationCheck


def _name_key(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (text or "").casefold())


def _label(company: Company) -> str:
    return f"{company.legal_name} ({company.id})"


class _Checker:
    def __init__(self, db: Session, p: ListingProposal) -> None:
        self.db = db
        self.p = p
        self.errors: list[Finding] = []
        self.warnings: list[Finding] = []

    def error(self, code: str, message: str) -> None:
        self.errors.append(Finding(code, message))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(Finding(code, message))

    def currency(self, text: str | None, what: str) -> str | None:
        if text is None or not text.strip():
            return None
        try:
            return check_currency(text.strip())
        except ListingError as exc:
            self.error("unsupported_currency", f"{what}: {exc}")
            return None

    def run(self) -> RegistrationCheck:
        p = self.p
        ticker = normalize_ticker(p.ticker)
        mic = normalize_mic(p.mic)
        if not _TICKER.fullmatch(ticker):
            self.error("invalid_ticker", f"ticker {p.ticker!r}: 1-16 of A-Z, 0-9, '.', '-'")
        exchange = EXCHANGES.get(mic)
        if exchange is None:
            self.error(
                "unsupported_exchange",
                f"exchange MIC {mic!r} is not in the registry ({', '.join(sorted(EXCHANGES))}); "
                "extend market/exchanges.py deliberately to add one",
            )
        currency = self.currency(p.currency, "trading currency")
        if p.currency is None or not p.currency.strip():
            self.error("missing_currency", "the trading currency is required (never inferred)")
        if currency and exchange and currency not in exchange.currencies:
            self.error(
                "currency_not_traded_on_exchange",
                f"{currency} is not a trading currency of {exchange.name} "
                f"({', '.join(exchange.currencies)})",
            )
        if p.security_type not in SECURITY_TYPES:
            self.error(
                "invalid_security_type",
                f"security type {p.security_type!r} is not one of: {', '.join(SECURITY_TYPES)}",
            )
        share_class = (p.share_class or "").strip().upper() or None
        if share_class is not None and not _SHARE_CLASS.fullmatch(share_class):
            self.error("invalid_share_class", f"share class {p.share_class!r}: 1-16 of A-Z, 0-9")
        isin = (p.isin or "").strip().upper() or None
        if isin is not None and not isin_is_valid(isin):
            self.error("invalid_isin", f"ISIN {p.isin!r} fails the ISO 6166 format or check digit")
        if not p.source_name.strip():
            self.error("missing_source", "a source supporting the registration is required")
        if len(p.reason.strip()) < MIN_REASON:
            self.error("reason_too_short", f"the reason must be at least {MIN_REASON} characters")

        symbols = self._provider_symbols(ticker, mic)
        suggested = {}
        if mic in MIC_SUFFIX and _TICKER.fullmatch(ticker):
            suggested["yahoo_chart"] = yahoo_symbol(ticker, mic)

        existing = find_listings(self.db, ticker, mic) if _TICKER.fullmatch(ticker) else []
        for s in existing:
            company = self.db.get(Company, s.company_id)
            self.error(
                "duplicate_listing",
                f"{ticker} {mic} is already registered (listing {s.id}, issuer "
                f"{_label(company) if company else s.company_id})",
            )

        company, issuer = self._issuer(ticker, isin)
        if company is not None:
            self._same_issuer_checks(company, mic, share_class, isin)
        if isin is not None:
            self._isin_owner(company, isin)

        listing = {
            "ticker": ticker,
            "exchange_mic": mic,
            "exchange_name": exchange.name if exchange else None,
            "trading_currency": currency,
            "security_type": p.security_type,
            "share_class": share_class,
            "isin": isin,
            "provider_symbols": symbols,
            "source_name": p.source_name.strip(),
            "source_reference": (p.source_reference or "").strip() or None,
            "reason": p.reason.strip(),
        }
        return RegistrationCheck(
            listing, issuer, suggested, tuple(self.errors), tuple(self.warnings), company
        )

    def _provider_symbols(self, ticker: str, mic: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for code, raw in sorted(self.p.provider_symbols.items()):
            symbol = raw.strip()
            if self.db.get(Provider, code) is None:
                self.error("unknown_provider", f"provider {code!r} is not in source.providers")
                continue
            if not _PROVIDER_SYMBOL.fullmatch(symbol):
                self.error("invalid_provider_symbol", f"{code} symbol {raw!r} is not valid")
                continue
            taken = self.db.scalars(
                select(Security).where(Security.provider_symbols[code].astext == symbol)
            ).all()
            for s in taken:
                self.error(
                    "provider_symbol_in_use",
                    f"{code} symbol {symbol} already maps to {s.ticker} {s.exchange_mic}",
                )
            if code == "yahoo_chart" and mic in MIC_SUFFIX and _TICKER.fullmatch(ticker):
                rule = yahoo_symbol(ticker, mic)
                if symbol != rule:
                    self.warn(
                        "provider_symbol_differs_from_rule",
                        f"yahoo_chart symbol {symbol} differs from the D15 rule ({rule}); "
                        "stored as given",
                    )
            out[code] = symbol
        return out

    def _issuer(self, ticker: str, isin: str | None) -> tuple[Company | None, dict[str, Any]]:
        ref = self.p.issuer
        modes = [
            m
            for m, v in (
                ("company_id", ref.company_id),
                ("same_issuer_as", ref.same_issuer_as),
                ("cik", ref.cik),
                ("new", ref.new),
            )
            if v is not None
        ]
        if len(modes) != 1:
            self.error(
                "issuer_resolution_required",
                "state the issuer exactly once: --company-id, --same-issuer-as TICKER:MIC, "
                f"--cik or --new-issuer (got {', '.join(modes) or 'none'})",
            )
            return None, {"mode": None}
        mode = modes[0]
        company: Company | None = None
        if ref.company_id is not None:
            company = self.db.get(Company, ref.company_id)
            if company is None:
                self.error("unknown_company", f"no company {ref.company_id}")
        elif ref.same_issuer_as is not None:
            t, m = ref.same_issuer_as
            found = find_listings(self.db, t, m)
            if len(found) != 1:
                self.error(
                    "unknown_listing" if not found else "ambiguous_listing",
                    f"{len(found)} listings match {normalize_ticker(t)} {normalize_mic(m)}",
                )
            else:
                company = self.db.get(Company, found[0].company_id)
        elif ref.cik is not None:
            ident = self.db.get(CompanyIdentifier, ("SEC_CIK", f"{ref.cik:010d}"))
            if ident is None:
                self.error("unknown_cik", f"no company with SEC CIK {ref.cik}")
            else:
                company = self.db.get(Company, ident.company_id)
        else:
            assert ref.new is not None
            return None, self._new_issuer(ref.new, ticker, isin)
        return company, {
            "mode": mode,
            "company_id": str(company.id) if company else None,
            "legal_name": company.legal_name if company else None,
        }

    def _new_issuer(self, new: NewIssuer, ticker: str, isin: str | None) -> dict[str, Any]:
        legal = new.legal_name.strip()
        country = new.country.strip().upper()
        if not legal:
            self.error("missing_legal_name", "a new issuer needs a legal name")
        if not _COUNTRY.fullmatch(country):
            self.error("invalid_country", f"country {new.country!r} is not ISO 3166-1 alpha-2")
        reporting = self.currency(new.reporting_currency, "reporting currency")
        candidates: dict[uuid.UUID, tuple[Company, list[str]]] = {}
        key = _name_key(legal)
        if key:
            for c in self.db.scalars(select(Company).order_by(Company.id)):
                if key in (_name_key(c.legal_name), _name_key(c.common_name)):
                    candidates.setdefault(c.id, (c, []))[1].append("same name")
        same_ticker = select(Security).where(func.upper(Security.ticker) == ticker)
        for s in self.db.scalars(same_ticker.order_by(Security.id)):
            owner = self.db.get(Company, s.company_id)
            if owner is not None:
                candidates.setdefault(owner.id, (owner, []))[1].append(
                    f"lists {s.ticker} on {s.exchange_mic or s.exchange_name}"
                )
        if candidates:
            described = "; ".join(
                f"{_label(c)}: {', '.join(why)}" for c, why in candidates.values()
            )
            if self.p.confirm_new_issuer:
                self.warn("possible_existing_issuer_dismissed", described)
            else:
                self.error(
                    "possible_existing_issuer",
                    f"may already exist — {described}. Use --company-id / --same-issuer-as, "
                    "or --confirm-new-issuer if it is a different issuer",
                )
        return {
            "mode": "new",
            "legal_name": legal,
            "common_name": (new.common_name or "").strip() or None,
            "country": country,
            "reporting_currency": reporting,
            "candidates": sorted(str(cid) for cid in candidates),
        }

    def _same_issuer_checks(
        self, company: Company, mic: str, share_class: str | None, isin: str | None
    ) -> None:
        on_exchange = self.db.scalars(
            select(Security)
            .where(Security.company_id == company.id, Security.exchange_mic == mic)
            .order_by(Security.id)
        ).all()
        for s in on_exchange:
            same_instrument = (isin is not None and s.isin == isin) or (
                s.share_class == share_class
                and (s.security_type is None or s.security_type == self.p.security_type)
            )
            if same_instrument:
                self.error(
                    "issuer_already_listed_on_exchange",
                    f"{company.legal_name} already has {s.ticker} on {mic} for the same "
                    "instrument; a different share class needs --share-class",
                )

    def _isin_owner(self, company: Company | None, isin: str) -> None:
        """One ISIN belongs to one issuer (it may be listed on several exchanges)."""
        owners = self.db.scalars(
            select(Security).where(Security.isin == isin).order_by(Security.id)
        ).all()
        for s in owners:
            if company is None or s.company_id != company.id:
                self.error(
                    "isin_belongs_to_other_issuer",
                    f"ISIN {isin} is already used by {s.ticker} {s.exchange_mic} of issuer "
                    f"{s.company_id}; register under that issuer or correct the ISIN",
                )


def check_listing(db: Session, proposal: ListingProposal) -> RegistrationCheck:
    """Validate a proposed listing. Reads the database only."""
    return _Checker(db, proposal).run()


def register_listing(
    db: Session, proposal: ListingProposal, *, user_id: uuid.UUID | None = None
) -> RegistrationResult:
    """Re-check under a per-listing lock and register. The caller commits."""
    lock_key = f"listing:{normalize_ticker(proposal.ticker)}:{normalize_mic(proposal.mic)}"
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(lock_key))))
    check = check_listing(db, proposal)
    if not check.ok:
        raise RegistrationRefusedError("; ".join(f"{e.code}: {e.message}" for e in check.errors))
    listing = check.listing
    company = check.company
    created = company is None
    if company is None:
        issuer = check.issuer
        company = Company(
            id=uuid.uuid4(),
            legal_name=issuer["legal_name"],
            common_name=issuer["common_name"],
            country=issuer["country"],
            reporting_currency=issuer["reporting_currency"],
        )
        db.add(company)
        db.flush()
    security = Security(
        id=uuid.uuid4(),
        company_id=company.id,
        ticker=listing["ticker"],
        exchange_name=listing["exchange_name"],
        exchange_mic=listing["exchange_mic"],
        trading_currency=listing["trading_currency"],
        is_primary=False,
        provider_symbols=listing["provider_symbols"],
        security_type=listing["security_type"],
        share_class=listing["share_class"],
        isin=listing["isin"],
    )
    db.add(security)
    db.flush()
    registration = ListingRegistration(
        id=uuid.uuid4(),
        security_id=security.id,
        company_id=company.id,
        company_created=created,
        proposal={"listing": listing, "issuer": check.issuer},
        warnings=[w.as_dict() for w in check.warnings],
        source_name=listing["source_name"],
        source_reference=listing["source_reference"],
        reason=listing["reason"],
        registered_by=user_id,
    )
    db.add(registration)
    audit.record(
        db,
        action="listing_registered",
        entity_type="security",
        entity_id=str(security.id),
        user_id=user_id,
        after={
            "registration_id": str(registration.id),
            "company_id": str(company.id),
            "company_created": created,
            **{k: v for k, v in listing.items() if k not in ("reason",)},
            "warnings": [w.as_dict() for w in check.warnings],
        },
        reason=listing["reason"],
    )
    db.flush()
    return RegistrationResult(security, company, created, registration.id, check)


def describe_listing(db: Session, security: Security) -> dict[str, Any]:
    """Listing metadata, issuer, registrations and stored-observation counts (read-only)."""
    company = db.get(Company, security.company_id)
    registrations = db.scalars(
        select(ListingRegistration)
        .where(ListingRegistration.security_id == security.id)
        .order_by(ListingRegistration.registered_at, ListingRegistration.id)
    ).all()

    def count(model: Any) -> int:
        return (
            db.scalar(
                select(func.count()).select_from(model).where(model.security_id == security.id)
            )
            or 0
        )

    return {
        "listing_id": str(security.id),
        "ticker": security.ticker,
        "exchange_mic": security.exchange_mic,
        "exchange_name": security.exchange_name,
        "trading_currency": security.trading_currency,
        "security_type": security.security_type,
        "share_class": security.share_class,
        "isin": security.isin,
        "is_primary": security.is_primary,
        "provider_symbols": dict(sorted((security.provider_symbols or {}).items())),
        "origin": "manual_registration" if registrations else "sec_ingestion",
        "issuer": {
            "company_id": str(security.company_id),
            "legal_name": company.legal_name if company else None,
            "country": company.country if company else None,
            "other_listings": [
                f"{s.ticker} {s.exchange_mic or s.exchange_name}"
                for s in db.scalars(
                    select(Security)
                    .where(Security.company_id == security.company_id, Security.id != security.id)
                    .order_by(Security.ticker, Security.exchange_mic)
                )
            ],
        },
        "registrations": [
            {
                "id": str(r.id),
                "registered_at": r.registered_at.isoformat() if r.registered_at else None,
                "registered_by": str(r.registered_by) if r.registered_by else None,
                "company_created": r.company_created,
                "source_name": r.source_name,
                "source_reference": r.source_reference,
                "reason": r.reason,
                "warnings": r.warnings,
            }
            for r in registrations
        ],
        "observations": {
            "prices": count(PriceObservation),
            "corporate_actions": count(CorporateAction),
        },
    }


def list_listings(db: Session, ticker: str | None = None) -> list[Security]:
    query = select(Security).order_by(Security.ticker, Security.exchange_mic, Security.id)
    if ticker:
        query = query.where(func.upper(Security.ticker) == normalize_ticker(ticker))
    return list(db.scalars(query))


def registered_ids(db: Session) -> set[uuid.UUID]:
    return set(db.scalars(select(ListingRegistration.security_id)))


__all__ = [
    "SECURITY_TYPES",
    "IssuerRef",
    "ListingProposal",
    "NewIssuer",
    "RegistrationCheck",
    "RegistrationRefusedError",
    "RegistrationResult",
    "check_listing",
    "describe_listing",
    "isin_is_valid",
    "list_listings",
    "register_listing",
    "registered_ids",
]
