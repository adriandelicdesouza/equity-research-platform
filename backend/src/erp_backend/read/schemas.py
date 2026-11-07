"""Response schemas of the read-only data API (D59).

Conventions:

* Exact numbers are ``Decimal`` and serialize as **JSON strings** (``"4.15"``), never
  binary floats. Every amount carries its unit (and currency where it is money).
* Lists are wrapped in :class:`Page` (``items`` + ``total``/``limit``/``offset``).
* Provenance is explicit on every financial value, observation and reference record;
  internal filesystem paths of archived files are never exposed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(description="Number of matching records (before limit/offset)")
    limit: int
    offset: int


# --------------------------------------------------------------------------- companies


class ListingOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    ticker: str
    exchange_mic: str | None
    exchange_name: str
    trading_currency: str | None = Field(description="Declared by an analyst; never inferred")
    is_primary: bool
    security_type: str | None
    share_class: str | None
    isin: str | None
    provider_symbols: dict[str, str]
    origin: str = Field(description="'sec' (created by SEC ingestion) or 'manual_registration'")
    registration_source: str | None


class IdentifierOut(BaseModel):
    scheme: str
    value: str


class ClassificationOut(BaseModel):
    id: int
    kind: str = Field(description="'suggested' (system) or 'confirmed' (analyst)")
    classification: str
    classifier_version: str | None
    reason: str | None
    evidence: dict[str, Any]
    created_at: datetime


class ClassificationStatusOut(BaseModel):
    effective: str = Field(description="Confirmed classification, or 'needs_review'")
    status: str = Field(description="'confirmed', 'suggested' or 'none'")
    latest_suggestion: ClassificationOut | None
    latest_confirmation: ClassificationOut | None


class CompanyOut(BaseModel):
    id: uuid.UUID
    legal_name: str
    common_name: str | None
    country: str | None
    reporting_currency: str | None
    fiscal_year_end_mmdd: str | None
    accounting_standard: str | None
    accounting_standard_inferred: bool
    financial_classification: str
    status: str
    tickers: list[str]


class CompanyDetailOut(CompanyOut):
    sic_code: str | None
    sic_description: str | None
    state_of_incorporation: str | None
    interim_coverage: str | None
    identifiers: list[IdentifierOut]
    listings: list[ListingOut]
    classification: ClassificationStatusOut
    created_at: datetime


# --------------------------------------------------------------------------- datasets


class DatasetOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    basis: str = Field(description="'standardized' (SEC) or 'reported' (file imports)")
    version_no: int
    status: str = Field(description="'draft', 'locked' or 'discarded'")
    parent_dataset_id: uuid.UUID | None
    currency: str
    mapping_version: str
    taxonomy: str
    selection_policy: str
    facts_as_of: date
    interim_coverage: str
    value_count: int
    format_version: int
    content_sha256: str
    input_sha256: str
    created_at: datetime
    locked_at: datetime | None
    discarded_at: datetime | None
    status_reason: str | None


class DatasetDetailOut(DatasetOut):
    source_summary: dict[str, Any]
    check_counts: dict[str, int] = Field(description="Validation results by status")
    conflict_counts: dict[str, int] = Field(description="Conflicts by status")
    open_material_conflicts: int


class PeriodOut(BaseModel):
    fiscal_year: int
    period_type: str
    start_date: date
    end_date: date
    weeks: int | None


class FactSourceOut(BaseModel):
    fact_id: int
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    accession_no: str
    form: str
    filed_date: date
    period_start: date | None
    period_end: date


class ImportSourceOut(BaseModel):
    import_id: uuid.UUID
    import_value_id: int | None
    original_filename: str | None
    file_sha256: str | None
    locator: str | None
    source_document: str | None
    source_page: str | None


class FinancialValueOut(BaseModel):
    line_item_code: str
    label: str | None
    statement: str
    period: PeriodOut
    value: Decimal | None
    unit: str
    scale: int
    status: str
    provenance_kind: str = Field(
        description="'reported' (filing), 'derived' (documented rule), 'manual_import'"
    )
    concept: str
    sign_multiplier: int
    derivation: dict[str, Any] = Field(description="Selection/derivation detail of the build")
    fact_sources: list[FactSourceOut]
    import_source: ImportSourceOut | None


class StatementCellOut(BaseModel):
    value: Decimal | None
    provenance_kind: str
    status: str


class StatementRowOut(BaseModel):
    line_item_code: str
    label: str | None
    is_subtotal: bool
    value_type: str | None
    unit: str | None
    cells: list[StatementCellOut | None] = Field(description="One cell per period column")


class StatementOut(BaseModel):
    dataset_id: uuid.UUID
    basis: str
    version_no: int
    status: str
    currency: str
    statement: str
    periods: list[PeriodOut]
    rows: list[StatementRowOut]


class CheckOut(BaseModel):
    id: int
    check_code: str
    subject: str
    period: PeriodOut | None
    severity: str
    status: str
    expected: Decimal | None
    actual: Decimal | None
    difference: Decimal | None
    tolerance: Decimal | None
    unit: str | None
    message: str
    inputs: dict[str, Any]


class IntegrityOut(BaseModel):
    dataset_id: uuid.UUID
    status: str
    stored_values_match: bool | None = Field(
        description="Stored rows re-serialize to content_sha256 (None for discarded)"
    )
    stored_checks_match: bool | None = Field(
        description="Stored validation rows hash to validation_sha256"
    )
    note: str


# --------------------------------------------------------------------------- conflicts


class CandidateOut(BaseModel):
    source: str = Field(description="'reported_fact' or 'import_value'")
    candidate_id: int | None
    value: Decimal | None
    role: str | None
    concept: str | None
    form: str | None
    filed: str | None
    accession: str | None
    detail: dict[str, Any]


class ResolutionOut(BaseModel):
    id: int
    conflict_key: str
    action: str
    candidate_source: str
    candidate_id: int | None
    value: Decimal | None
    source_reference: str | None
    reason: str
    line_item_code: str
    period_type: str
    period_start: date
    period_end: date
    detected_in_dataset_id: uuid.UUID
    created_at: datetime


class ConflictOut(BaseModel):
    id: int
    dataset_id: uuid.UUID
    conflict_key: str
    kind: str
    line_item_code: str
    period: PeriodOut
    is_material: bool
    absolute_difference: Decimal
    relative_difference: Decimal
    threshold: Decimal
    materiality_basis: str
    status: str
    candidates: list[CandidateOut]
    policy_candidate_id: int | None
    resolution: ResolutionOut | None
    detail: dict[str, Any]


# --------------------------------------------------------------------------- imports


class ImportOut(BaseModel):
    id: uuid.UUID
    category: str = Field(description="'statement', 'market' or 'reference'")
    template_version: str
    provider_code: str
    file_kind: str
    original_filename: str
    file_sha256: str
    bytes: int
    source_name: str
    source_reference: str | None
    status: str = Field(description="'complete' or 'partial'")
    record_counts: dict[str, int] = Field(
        description="accepted/duplicate/conflict(ing)/unmapped/ambiguous/rejected, as recorded"
    )
    skipped_count: int
    imported_at: datetime


class ImportDetailOut(ImportOut):
    summary: dict[str, Any]
    skipped: list[dict[str, Any]]
    skipped_truncated: bool
    stored_objects: dict[str, Any]


# --------------------------------------------------------------------------- market data


class OriginOut(BaseModel):
    kind: str = Field(description="'provider_fetch' or 'file_import'")
    fetch_id: uuid.UUID | None = None
    import_id: uuid.UUID | None = None
    import_row: int | None = None


class PriceOut(BaseModel):
    observation_id: int
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    close_basis: str
    adj_close: Decimal | None
    adj_close_basis: str
    volume: int | None
    currency: str
    provider_code: str
    origin: OriginOut
    revisions: int = Field(description="Observations stored for this date in this series")


class CorporateActionOut(BaseModel):
    id: int
    kind: str
    ex_date: date
    ratio_numerator: Decimal | None
    ratio_denominator: Decimal | None
    amount: Decimal | None
    currency: str | None
    currency_source: str | None
    provider_code: str
    origin: OriginOut


class RateSeriesOut(BaseModel):
    code: str
    provider_code: str
    provider_series_id: str
    currency: str
    rate_type: str
    tenor: str
    tenor_months: int | None
    unit: str
    quote_convention: str
    description: str
    observation_count: int
    first_date: date | None
    last_date: date | None


class RateObservationOut(BaseModel):
    observation_id: int
    series_code: str
    obs_date: date
    value: Decimal
    unit: str
    provider_code: str
    fetch_id: uuid.UUID
    revisions: int


class FxRateOut(BaseModel):
    observation_id: int
    base: str
    quote: str
    direction: str = Field(description="e.g. 'CAD per 1 USD'")
    obs_date: date
    rate: Decimal
    provider_code: str
    provider_series_id: str
    fetch_id: uuid.UUID
    revisions: int


# --------------------------------------------------------------------------- reference


class ReferenceDatasetOut(BaseModel):
    id: uuid.UUID
    provider_code: str
    kind: str
    template_version: str
    edition_date: date
    region: str | None
    variant: str | None
    lineage_key: str
    version_no: int
    is_current: bool = Field(description="Latest version of its lineage")
    supersedes_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    reason: str | None
    record_count: int
    content_sha256: str
    import_id: uuid.UUID
    created_at: datetime


class ReferenceDatasetDetailOut(ReferenceDatasetOut):
    source_name: str
    source_reference: str | None
    original_filename: str
    file_sha256: str
    import_status: str


class ReferenceValueOut(BaseModel):
    record_key: str
    field: str
    value: Decimal | None
    value_text: str | None
    unit: str
    import_row: int
    locator: str


class ReferenceRecordOut(BaseModel):
    record_key: str
    import_row: int
    locator: str
    fields: dict[str, ReferenceValueOut]
