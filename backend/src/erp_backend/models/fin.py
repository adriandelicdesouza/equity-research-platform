"""Fiscal periods and versioned standardized datasets (schema ``fin``; Blueprint §3.3)."""

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
    validation_sha256: Mapped[str | None] = mapped_column(String(64))
    resolution_cutoff_id: Mapped[int | None] = mapped_column(BigInteger)
    # Highest source.statement_import_values id a ``reported`` (import) dataset used (D57).
    import_cutoff_id: Mapped[int | None] = mapped_column(BigInteger)
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
    # Set exactly when provenance_kind = 'manual_import' (D57; migration 0013).
    import_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    import_value_id: Mapped[int | None] = mapped_column(BigInteger)


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = {"schema": "fin"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fin.financial_datasets.id"))
    check_code: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    period_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fin.fiscal_periods.id"))
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expected: Mapped[Decimal | None] = mapped_column(Numeric)
    actual: Mapped[Decimal | None] = mapped_column(Numeric)
    difference: Mapped[Decimal | None] = mapped_column(Numeric)
    tolerance: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ConflictResolution(Base):
    """Append-only analyst decision on a conflict fingerprint (D52)."""

    __tablename__ = "conflict_resolutions"
    __table_args__ = {"schema": "fin"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    conflict_key: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_fact_id: Mapped[int | None] = mapped_column(BigInteger)
    # Which id space candidate_fact_id refers to: reported_fact (NULL) or import_value (D57).
    candidate_source: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    source_reference: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detected_in_dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fin.financial_datasets.id")
    )
    line_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    period_type: Mapped[str] = mapped_column(String(4), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DataConflict(Base):
    """A conflict detected by one dataset build (immutable; kept after discard)."""

    __tablename__ = "data_conflicts"
    __table_args__ = {"schema": "fin"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fin.financial_datasets.id"))
    conflict_key: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    line_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fin.fiscal_periods.id"))
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    policy_fact_id: Mapped[int | None] = mapped_column(BigInteger)
    is_material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    absolute_difference: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    relative_difference: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    materiality_basis: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution_id: Mapped[int | None] = mapped_column(ForeignKey("fin.conflict_resolutions.id"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
