"""Companies, listings and identifiers (schema ``market``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from erp_backend.models.base import Base, TimestampMixin


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    common_name: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    state_of_incorporation: Mapped[str | None] = mapped_column(String(8))
    sic_code: Mapped[str | None] = mapped_column(String(8))
    sic_description: Mapped[str | None] = mapped_column(Text)
    fiscal_year_end_mmdd: Mapped[str | None] = mapped_column(String(4))
    reporting_currency: Mapped[str | None] = mapped_column(String(3))
    accounting_standard: Mapped[str | None] = mapped_column(String(16))
    accounting_standard_inferred: Mapped[bool] = mapped_column(Boolean, default=True)
    financial_classification: Mapped[str | None] = mapped_column(String(32))
    interim_coverage: Mapped[str | None] = mapped_column(String(8))
    interim_coverage_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    interim_coverage_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")


class Security(Base):
    __tablename__ = "securities"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange_name: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_mic: Mapped[str | None] = mapped_column(String(4))
    trading_currency: Mapped[str | None] = mapped_column(String(3))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_symbols: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyIdentifier(Base):
    __tablename__ = "company_identifiers"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    scheme: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyClassification(Base):
    """Append-only classification history: suggestions and analyst confirmations (D53)."""

    __tablename__ = "company_classifications"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    classifier_version: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class _Observed:
    """Columns shared by append-only market observations (D54)."""

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.securities.id"))
    provider_code: Mapped[str] = mapped_column(ForeignKey("source.providers.code"))
    fetch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source.provider_fetches.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PriceObservation(_Observed, Base):
    __tablename__ = "price_observations"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric)
    high: Mapped[Decimal | None] = mapped_column(Numeric)
    low: Mapped[Decimal | None] = mapped_column(Numeric)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    close_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    adj_close_basis: Mapped[str] = mapped_column(String(32), nullable=False)


class CorporateAction(_Observed, Base):
    __tablename__ = "corporate_actions"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    ratio_numerator: Mapped[Decimal | None] = mapped_column(Numeric)
    ratio_denominator: Mapped[Decimal | None] = mapped_column(Numeric)
    amount: Mapped[Decimal | None] = mapped_column(Numeric)
    currency: Mapped[str | None] = mapped_column(String(3))
    currency_source: Mapped[str | None] = mapped_column(String(32))


class ProviderListing(_Observed, Base):
    __tablename__ = "provider_listings"
    __table_args__ = {"schema": "market"}  # noqa: RUF012

    provider_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    exchange_code: Mapped[str | None] = mapped_column(String(32))
    exchange_name: Mapped[str | None] = mapped_column(String(128))
    exchange_timezone: Mapped[str | None] = mapped_column(String(64))
    instrument_type: Mapped[str | None] = mapped_column(String(32))
    first_trade_date: Mapped[date | None] = mapped_column(Date)
    quote_price: Mapped[Decimal | None] = mapped_column(Numeric)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
