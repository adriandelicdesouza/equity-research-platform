"""Valuation projects, assumptions and immutable runs (schema ``val``; migration 0017).

Mutable configuration: ``valuation_projects`` and ``scenarios`` (UPDATE allowed and audited;
DELETE refused by trigger). Everything a result depends on is append-only: assumption
revisions, runs, run traces, run inputs and run decisions (UPDATE/DELETE/TRUNCATE refused
for every role, owner included).
"""

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
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from erp_backend.models.base import Base

_VAL = {"schema": "val"}


class ValuationProject(Base):
    __tablename__ = "valuation_projects"
    __table_args__ = _VAL

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("market.securities.id"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    thesis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fin.financial_datasets.id"))
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(Text)


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = _VAL

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.valuation_projects.id"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("val.scenarios.id"))
    probability: Mapped[Decimal | None] = mapped_column(Numeric)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssumptionRevision(Base):
    """One change to one assumption of one scenario (append-only). The current state of a key
    is its latest revision; approval is a revision too."""

    __tablename__ = "assumption_revisions"
    __table_args__ = _VAL

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.scenarios.id"))
    scenario_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(48), nullable=False)
    period_index: Mapped[int | None] = mapped_column(SmallInteger)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    value_json: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ValuationRun(Base):
    """One immutable methodology execution with its fully materialized input document."""

    __tablename__ = "valuation_runs"
    __table_args__ = _VAL

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.valuation_projects.id"))
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.scenarios.id"))
    scenario_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market.companies.id"))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("market.securities.id"))
    methodology: Mapped[str] = mapped_column(String(32), nullable=False)
    method_version: Mapped[str] = mapped_column(String(16), nullable=False)
    produces: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fin.financial_datasets.id"))
    dataset_version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    block_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ref.valuation_blocks.id"))
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    balance_sheet_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    input_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    module_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    primary_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    per_share_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    validation: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunTrace(Base):
    __tablename__ = "run_traces"
    __table_args__ = _VAL

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.valuation_runs.id"), primary_key=True)
    trace: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)


class RunInput(Base):
    """Queryable lineage index of a run's input document (one row per resolved input)."""

    __tablename__ = "run_inputs"
    __table_args__ = _VAL

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.valuation_runs.id"), primary_key=True)
    input_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    value_json: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    source_value: Mapped[Decimal | None] = mapped_column(Numeric)
    source_unit: Mapped[str | None] = mapped_column(String(16))
    conversion: Mapped[str | None] = mapped_column(String(32))
    provenance_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class RunDecision(Base):
    """An explicit, reasoned decision a run depends on (append-only): a classification
    acknowledgement, a dataset-check override, an input override or an administrator acting
    on another user's project. User and time are recorded with the affected run."""

    __tablename__ = "run_decisions"
    __table_args__ = _VAL

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("val.valuation_runs.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
