"""Fiscal periods and versioned standardized datasets (schema ``fin``; Blueprint §3.3)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from erp_backend.models.base import Base


class FiscalPeriodRow(Base):
    __tablename__ = "fiscal_periods"
    __table_args__ = {"schema": "fin"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_type: Mapped[str] = mapped_column(String(4), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    weeks: Mapped[int | None] = mapped_column(SmallInteger)


class FinancialDataset(Base):
    __tablename__ = "financial_datasets"
    __table_args__ = {"schema": "fin"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    basis: Mapped[str] = mapped_column(String(16), nullable=False, default="standardized")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fin.financial_datasets.id")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    mapping_version: Mapped[str] = mapped_column(String(16), nullable=False)
    mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    taxonomy: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    selection_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    facts_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    format_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    interim_coverage: Mapped[str] = mapped_column(String(8), nullable=False)
    value_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(Text)


class FinancialValue(Base):
    __tablename__ = "financial_values"
    __table_args__ = {"schema": "fin"}  # noqa: RUF012

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fin.financial_datasets.id"), primary_key=True
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fin.fiscal_periods.id"), primary_key=True
    )
    line_item_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    mapping_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    provenance_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    concept: Mapped[str] = mapped_column(String(256), nullable=False)
    concept_priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sign_multiplier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source_fact_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
