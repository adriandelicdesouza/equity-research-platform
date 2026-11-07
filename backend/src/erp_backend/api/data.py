"""Read-only data API over M2 (D59): companies, datasets, conflicts, imports, market data,
rates/FX and reference datasets.

Every route is GET, requires ``company:read`` (admin, analyst and reviewer all hold it) and
reads through :func:`erp_backend.readonly.read_only_db` — a PostgreSQL ``READ ONLY``
transaction that the ORM also refuses to flush, always rolled back. Handlers only parse
parameters and call :mod:`erp_backend.read` services.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from erp_backend.auth.deps import AuthContext, require
from erp_backend.auth.rbac import Permission
from erp_backend.errors import ApiError
from erp_backend.read import companies, datasets, imports, market, reference
from erp_backend.read.common import MAX_LIMIT
from erp_backend.read.schemas import (
    CheckOut,
    ClassificationOut,
    CompanyDetailOut,
    CompanyOut,
    ConflictOut,
    CorporateActionOut,
    DatasetDetailOut,
    DatasetOut,
    FinancialValueOut,
    FxRateOut,
    ImportDetailOut,
    ImportOut,
    IntegrityOut,
    ListingOut,
    Page,
    PriceOut,
    RateObservationOut,
    RateSeriesOut,
    ReferenceDatasetDetailOut,
    ReferenceDatasetOut,
    ReferenceRecordOut,
    ResolutionOut,
    StatementOut,
)
from erp_backend.readonly import read_only_db

router = APIRouter(tags=["data (read-only)"])

Reader = Annotated[AuthContext, Depends(require(Permission.COMPANY_READ))]
ReadDb = Annotated[Session, Depends(read_only_db)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description=f"Page size (max {MAX_LIMIT})")]
Offset = Annotated[int, Query(ge=0, le=10_000_000)]
Code = Annotated[str, Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")]
Ccy = Annotated[str, Query(pattern=r"^[A-Za-z]{3}$")]
Year = Annotated[int, Query(ge=1900, le=2200)]
Revisions = Annotated[
    Literal["latest", "all"],
    Query(description="'latest': latest stored observation per date; 'all': every revision"),
]
Sort = Literal["date", "-date"]
DEFAULT_LIMIT = 50


def _range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ApiError(422, "validation_error", "'end' is before 'start'.")


# --------------------------------------------------------------------------- companies


@router.get("/companies", response_model=Page[CompanyOut])
def list_companies(
    db: ReadDb,
    _: Reader,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    sort: Literal["name", "-name", "created", "-created"] = "name",
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[CompanyOut]:
    """Search by name or exact ticker; filter by ISO country."""
    return companies.list_companies(db, q=q, country=country, sort=sort, limit=limit, offset=offset)


@router.get("/companies/{company_id}", response_model=CompanyDetailOut)
def get_company(company_id: uuid.UUID, db: ReadDb, _: Reader) -> CompanyDetailOut:
    return companies.get_company(db, company_id)


@router.get("/companies/{company_id}/classifications", response_model=Page[ClassificationOut])
def list_classifications(
    company_id: uuid.UUID, db: ReadDb, _: Reader, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
) -> Page[ClassificationOut]:
    return companies.list_classifications(db, company_id, limit=limit, offset=offset)


@router.get("/listings", response_model=Page[ListingOut])
def list_listings(
    db: ReadDb,
    _: Reader,
    company_id: uuid.UUID | None = None,
    ticker: Annotated[str | None, Query(min_length=1, max_length=16)] = None,
    mic: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9]{4}$")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[ListingOut]:
    """Exchange listings; a listing is identified by ticker + MIC (a ticker alone is not)."""
    return companies.list_listings(
        db, company_id=company_id, ticker=ticker, mic=mic, limit=limit, offset=offset
    )


@router.get("/listings/{listing_id}", response_model=ListingOut)
def get_listing(listing_id: uuid.UUID, db: ReadDb, _: Reader) -> ListingOut:
    return companies.get_listing(db, listing_id)


# --------------------------------------------------------------------------- datasets


@router.get("/companies/{company_id}/datasets", response_model=Page[DatasetOut])
def list_datasets(
    company_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    basis: Literal["standardized", "reported"] | None = None,
    status: Literal["draft", "locked", "discarded"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[DatasetOut]:
    """Dataset versions of a company, newest version first within each basis."""
    return datasets.list_datasets(
        db, company_id, basis=basis, status=status, limit=limit, offset=offset
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(dataset_id: uuid.UUID, db: ReadDb, _: Reader) -> DatasetDetailOut:
    return datasets.get_dataset(db, dataset_id)


@router.get("/datasets/{dataset_id}/values", response_model=Page[FinancialValueOut])
def list_values(
    dataset_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    statement: Literal["IS", "BS", "CF"] | None = None,
    line_item: Annotated[list[str] | None, Query(max_length=50)] = None,
    period_type: Literal["FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M"] | None = None,
    fiscal_year_from: Year | None = None,
    fiscal_year_to: Year | None = None,
    provenance: Literal["reported", "derived", "manual_import", "analyst"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[FinancialValueOut]:
    """Values of exactly this dataset version, with their source facts or import cells."""
    if fiscal_year_from and fiscal_year_to and fiscal_year_to < fiscal_year_from:
        raise ApiError(422, "validation_error", "fiscal_year_to is before fiscal_year_from.")
    return datasets.list_values(
        db, dataset_id, statement=statement, line_items=line_item, period_type=period_type,
        fiscal_year_from=fiscal_year_from, fiscal_year_to=fiscal_year_to,
        provenance=provenance, limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/datasets/{dataset_id}/statements/{statement}", response_model=StatementOut)
def get_statement(
    dataset_id: uuid.UUID,
    statement: Annotated[Literal["IS", "BS", "CF"], Path()],
    db: ReadDb,
    _: Reader,
    period_type: Literal["FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M"] = "FY",
    fiscal_year_from: Year | None = None,
    fiscal_year_to: Year | None = None,
) -> StatementOut:
    """One statement as a grid (line items in mapping order × periods) for one period type."""
    return datasets.get_statement(
        db, dataset_id, statement, period_type=period_type,
        fiscal_year_from=fiscal_year_from, fiscal_year_to=fiscal_year_to,
    )  # fmt: skip


@router.get("/datasets/{dataset_id}/checks", response_model=Page[CheckOut])
def list_checks(
    dataset_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    severity: Literal["error", "warning", "info"] | None = None,
    status: Annotated[str | None, Query(max_length=16, pattern=r"^[a-z_]+$")] = None,
    check_code: Code | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[CheckOut]:
    """Stored accounting-integrity check results of the dataset build (D51)."""
    return datasets.list_checks(
        db, dataset_id, severity=severity, status=status, check_code=check_code,
        limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/datasets/{dataset_id}/integrity", response_model=IntegrityOut)
def get_integrity(dataset_id: uuid.UUID, db: ReadDb, _: Reader) -> IntegrityOut:
    """Re-hash the stored values and checks against the dataset's recorded fingerprints."""
    return datasets.integrity(db, dataset_id)


@router.get("/datasets/{dataset_id}/conflicts", response_model=Page[ConflictOut])
def list_conflicts(
    dataset_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    material: bool | None = None,
    status: Annotated[str | None, Query(max_length=32, pattern=r"^[a-z_]+$")] = None,
    kind: Annotated[str | None, Query(max_length=16, pattern=r"^[a-z_]+$")] = None,
    line_item: Code | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[ConflictOut]:
    """Conflicts detected by this dataset build, with candidates and applied resolution."""
    return datasets.list_conflicts(
        db, dataset_id, material=material, status=status, kind=kind, line_item=line_item,
        limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/companies/{company_id}/conflict-resolutions", response_model=Page[ResolutionOut])
def list_resolutions(
    company_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    conflict_key: Annotated[str | None, Query(max_length=32, pattern=r"^[0-9a-f]+$")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[ResolutionOut]:
    """Append-only resolution history of a company (newest first)."""
    return datasets.list_resolutions(
        db, company_id, conflict_key=conflict_key, limit=limit, offset=offset
    )


# --------------------------------------------------------------------------- imports


@router.get("/imports", response_model=Page[ImportOut])
def list_imports(
    db: ReadDb,
    _: Reader,
    category: Literal["statement", "market", "reference"] | None = None,
    template: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z0-9_.]+$")] = None,
    status: Literal["complete", "partial"] | None = None,
    file_sha256: Annotated[str | None, Query(pattern=r"^[0-9a-fA-F]{64}$")] = None,
    imported_from: date | None = None,
    imported_to: date | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[ImportOut]:
    """Ingested files (newest first). Archive paths are never returned."""
    _range(imported_from, imported_to)
    return imports.list_imports(
        db, category=category, template=template, status=status, file_sha256=file_sha256,
        imported_from=imported_from, imported_to=imported_to, limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/imports/{import_id}", response_model=ImportDetailOut)
def get_import(import_id: uuid.UUID, db: ReadDb, _: Reader) -> ImportDetailOut:
    return imports.get_import(db, import_id)


# --------------------------------------------------------------------------- market data


@router.get("/listings/{listing_id}/prices", response_model=Page[PriceOut])
def list_prices(
    listing_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    provider: Code | None = None,
    close_basis: Literal["raw", "split_adjusted", "unverified"] | None = None,
    start: date | None = None,
    end: date | None = None,
    revisions: Revisions = "latest",
    sort: Sort = "date",
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[PriceOut]:
    """One price series (one provider, one close basis). 409 when the choice is ambiguous."""
    _range(start, end)
    return market.list_prices(
        db, listing_id, provider=provider, close_basis=close_basis, start=start, end=end,
        revisions=revisions, descending=sort == "-date", limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/listings/{listing_id}/corporate-actions", response_model=Page[CorporateActionOut])
def list_corporate_actions(
    listing_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    kind: Literal["split", "cash_dividend"] | None = None,
    provider: Code | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[CorporateActionOut]:
    _range(start, end)
    return market.list_corporate_actions(
        db, listing_id, kind=kind, provider=provider, start=start, end=end,
        limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/rates/series", response_model=Page[RateSeriesOut])
def list_rate_series(
    db: ReadDb,
    _: Reader,
    currency: Ccy | None = None,
    provider: Code | None = None,
    rate_type: Literal["par_yield", "benchmark_bond_yield"] | None = None,
    tenor: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z]{1,8}$")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[RateSeriesOut]:
    """Registered rate series: identity = provider series, rate type, currency, tenor."""
    return market.list_rate_series(
        db, currency=currency, provider=provider, rate_type=rate_type, tenor=tenor,
        limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/rates/series/{code}", response_model=RateSeriesOut)
def get_rate_series(
    code: Annotated[str, Path(pattern=r"^[A-Z0-9_]{1,32}$")], db: ReadDb, _: Reader
) -> RateSeriesOut:
    return market.get_rate_series(db, code)


@router.get("/rates/series/{code}/observations", response_model=Page[RateObservationOut])
def list_rate_observations(
    code: Annotated[str, Path(pattern=r"^[A-Z0-9_]{1,32}$")],
    db: ReadDb,
    _: Reader,
    start: date | None = None,
    end: date | None = None,
    revisions: Revisions = "latest",
    sort: Sort = "date",
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[RateObservationOut]:
    """Observations of one series, in percentage points as published."""
    _range(start, end)
    return market.list_rate_observations(
        db, code, start=start, end=end, revisions=revisions, descending=sort == "-date",
        limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/fx/rates", response_model=Page[FxRateOut])
def list_fx(
    db: ReadDb,
    _: Reader,
    base: Ccy,
    quote: Ccy,
    provider: Code | None = None,
    start: date | None = None,
    end: date | None = None,
    revisions: Revisions = "latest",
    sort: Sort = "date",
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[FxRateOut]:
    """Stored FX rates as *quote per 1 base*. The reverse direction is never derived."""
    _range(start, end)
    return market.list_fx(
        db, base=base, quote=quote, provider=provider, start=start, end=end,
        revisions=revisions, descending=sort == "-date", limit=limit, offset=offset,
    )  # fmt: skip


# --------------------------------------------------------------------------- reference


@router.get("/reference/datasets", response_model=Page[ReferenceDatasetOut])
def list_reference_datasets(
    db: ReadDb,
    _: Reader,
    kind: Literal["implied_erp", "country_risk", "industry_betas", "rating_spreads"] | None = None,
    edition_date: date | None = None,
    region: Annotated[str | None, Query(pattern=r"^[a-z_]{1,32}$")] = None,
    variant: Literal["large_cap", "small_cap"] | None = None,
    current_only: bool = False,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[ReferenceDatasetOut]:
    """Reference dataset versions; ``current_only`` keeps the latest version per lineage."""
    return reference.list_reference_datasets(
        db, kind=kind, edition_date=edition_date, region=region, variant=variant,
        current_only=current_only, limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/reference/datasets/{dataset_id}", response_model=ReferenceDatasetDetailOut)
def get_reference_dataset(
    dataset_id: uuid.UUID, db: ReadDb, _: Reader
) -> ReferenceDatasetDetailOut:
    return reference.get_reference_dataset(db, dataset_id)


@router.get("/reference/datasets/{dataset_id}/records", response_model=Page[ReferenceRecordOut])
def list_reference_records(
    dataset_id: uuid.UUID,
    db: ReadDb,
    _: Reader,
    record_key: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    field: Annotated[list[str] | None, Query(max_length=20)] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> Page[ReferenceRecordOut]:
    """Records of exactly this version (reproducible), in file order."""
    return reference.list_reference_records(
        db, dataset_id, record_key=record_key, fields=field, limit=limit, offset=offset
    )
