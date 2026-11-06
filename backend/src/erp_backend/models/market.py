"""Companies, listings and identifiers (schema ``market``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
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
