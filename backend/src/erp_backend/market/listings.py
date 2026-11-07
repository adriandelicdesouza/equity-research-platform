"""Listing (security) identification and the analyst-declared trading currency (§4.3; D15, D55).

A listing is identified by ticker + exchange MIC — a ticker alone is never enough (RY
trades on XNYS and XTSE). The trading currency is a property of the listing that an
analyst declares; it is never inferred from a file or a provider payload. Once set it is
not changed by this function (a currency change would invalidate stored prices).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.market.model import ParseError, validate_currency
from erp_backend.models import Security

MIC = re.compile(r"^[A-Z0-9]{4}$")
# Quotation units that look like ISO codes but are not currencies of account
# (pence sterling, South African cents, Israeli agorot).
MINOR_UNIT_CODES = frozenset({"GBX", "ZAC", "ILA"})


class ListingError(ValueError):
    pass


def normalize_ticker(text: str) -> str:
    return text.strip().upper()


def normalize_mic(text: str) -> str:
    return text.strip().upper()


def find_listings(db: Session, ticker: str, mic: str) -> list[Security]:
    """Every security matching ticker + MIC (case-insensitive). More than one is ambiguous."""
    return list(
        db.scalars(
            select(Security)
            .where(
                func.upper(Security.ticker) == normalize_ticker(ticker),
                Security.exchange_mic == normalize_mic(mic),
            )
            .order_by(Security.id)
        )
    )


def check_currency(code: str) -> str:
    """ISO 4217 alphabetic code in major units, exactly as written (no case folding)."""
    try:
        text = validate_currency(code)
    except ParseError as exc:
        raise ListingError(str(exc)) from exc
    assert text is not None
    if text in MINOR_UNIT_CODES:
        raise ListingError(f"{text} is a minor-unit quotation code, not a major currency")
    return text


def set_trading_currency(
    db: Session,
    security: Security,
    currency: str,
    *,
    reason: str,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Declare a listing's trading currency. Returns False if it was already that value."""
    if not reason.strip():
        raise ListingError("A reason is required")
    code = check_currency(currency)
    if security.trading_currency == code:
        return False
    if security.trading_currency is not None:
        raise ListingError(
            f"{security.ticker} {security.exchange_mic} already trades in "
            f"{security.trading_currency}; changing a listing's currency is not supported"
        )
    security.trading_currency = code
    audit.record(
        db,
        action="listing_currency_set",
        entity_type="security",
        entity_id=str(security.id),
        user_id=user_id,
        before={"trading_currency": None},
        after={"trading_currency": code},
        reason=reason.strip(),
    )
    db.flush()
    return True
