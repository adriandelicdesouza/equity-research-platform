"""Provider registry and append-only fetch log (schema ``source``)."""

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
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from erp_backend.models.base import Base


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_limit_per_second: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ProviderFetch(Base):
    """One HTTP retrieval. The payload itself lives in the raw archive (content-addressed)."""

    __tablename__ = "provider_fetches"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("source.providers.code"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    http_status: Mapped[int | None] = mapped_column(Integer)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(128))
    payload_path: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    stored_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))


class SecTicker(Base):
    """One row of an archived SEC ticker-map snapshot."""

    __tablename__ = "sec_tickers"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    fetch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source.provider_fetches.id"), primary_key=True
    )
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    cik: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(64))


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    accession_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    filed_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_of_report: Mapped[date | None] = mapped_column(Date)
    primary_document: Mapped[str | None] = mapped_column(Text)
    fetch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source.provider_fetches.id"))


class ReportedFact(Base):
    __tablename__ = "reported_facts"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    taxonomy: Mapped[str] = mapped_column(String(32), nullable=False)
    concept: Mapped[str] = mapped_column(String(256), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str | None] = mapped_column(String(4))
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    filed_date: Mapped[date] = mapped_column(Date, nullable=False)
    accession_no: Mapped[str] = mapped_column(String(20), nullable=False)
    frame: Mapped[str | None] = mapped_column(String(16))
    fetch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source.provider_fetches.id"))


class SourceImport(Base):
    """One ingested file (Blueprint §3.3 ``source.imports``; append-only, migration 0011).

    The exact file bytes are kept in the raw archive (``stored_path``, verified against
    ``file_sha256`` on read). ``summary`` holds the validation counts; ``skipped`` lists every
    record that was not stored and why."""

    __tablename__ = "imports"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("source.providers.code"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    skipped: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    imported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
