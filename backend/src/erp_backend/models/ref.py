"""Versioned reference datasets (schema ``ref``; migration 0015, D58)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from erp_backend.models.base import Base


class ReferenceDataset(Base):
    """One immutable version of a reference table (e.g. Damodaran CRP, January edition).

    A *lineage* is (kind, edition date, region, variant); versions within it are numbered,
    each version after the first names the version it supersedes and a reason."""

    __tablename__ = "reference_datasets"
    __table_args__ = {"schema": "ref"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_code: Mapped[str] = mapped_column(ForeignKey("source.providers.code"))
    dataset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    edition_date: Mapped[date] = mapped_column(Date, nullable=False)
    region: Mapped[str | None] = mapped_column(String(32))
    variant: Mapped[str | None] = mapped_column(String(32))
    lineage_key: Mapped[str] = mapped_column(Text, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ref.reference_datasets.id"))
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source.imports.id"))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReferenceValue(Base):
    """One field of one record of a reference dataset (append-only).

    Exactly one of ``value`` (exact decimal, in ``unit``) and ``value_text`` is set."""

    __tablename__ = "reference_values"
    __table_args__ = {"schema": "ref"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ref.reference_datasets.id"))
    record_key: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    import_row: Mapped[int] = mapped_column(Integer, nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)


class StandardLineItem(Base):
    """A standard line item of one mapping version (migration 0005; read-only here)."""

    __tablename__ = "standard_line_items"
    __table_args__ = {"schema": "ref"}  # noqa: RUF012

    mapping_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    statement: Mapped[str] = mapped_column(String(2), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(64))
    period_type: Mapped[str] = mapped_column(String(8), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sign_convention: Mapped[str] = mapped_column(String(16), nullable=False)
    is_subtotal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    derived_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
