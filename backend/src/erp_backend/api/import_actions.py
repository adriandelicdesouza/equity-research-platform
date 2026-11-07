"""Import workflow actions: preview (non-persistent) and ingestion (D60; D55, D57, D58).

The file is sent as the **raw request body** (``Content-Type: text/csv``; at most 20 MB),
with its original name in ``filename`` — no multipart dependency is needed.

* ``POST /api/imports/{kind}/validate`` — preview. Runs on a **read-only** session (the
  same mechanism as the data API), so it cannot persist anything; it is not recorded as an
  action. Permission ``company:write``.
* ``POST /api/imports/{kind}`` — ingestion. Requires ``expect_sha256`` (the hash printed by
  the preview), keeps every service refusal (``allow_partial``, ``allow_conflicts``,
  ``supersede`` + ``reason``), archives the exact bytes and is audited and action-logged.
  Permission ``data:import`` — **held by no role until the owner approves (P15)**.

Archive paths are never returned. The API container must be able to write the raw archive
(``raw_root``) for ingestion; without it the request is refused with ``503``.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from erp_backend.api.actions import (
    DatasetActionOut,
    IdempotencyKey,
    _dataset_result,
    requires,
)
from erp_backend.auth.deps import AppSettings, DbSession
from erp_backend.auth.rbac import Permission
from erp_backend.config import Settings
from erp_backend.errors import ApiError
from erp_backend.market import file_import as market_import
from erp_backend.market.tabular import MAX_BYTES
from erp_backend.readonly import read_only_db
from erp_backend.reference import damodaran
from erp_backend.statements import file_import as statement_import
from erp_backend.workflow import ActionOut, perform

router = APIRouter(prefix="/imports", tags=["workflow actions: imports"])

ReadDb = Annotated[Session, Depends(read_only_db)]
FileName = Annotated[
    str, Query(min_length=1, max_length=255, pattern=r"^[^/\\\x00]+$", description="Original name")
]
Sha256 = Annotated[str, Query(pattern=r"^[0-9a-fA-F]{64}$", description="From the preview")]
Source = Annotated[str, Query(min_length=1, max_length=500, description="Where the data came from")]
SourceRef = Annotated[str | None, Query(max_length=1000)]
MarketTemplate = Literal["market_prices.v1", "corporate_actions.v1"]
ReferenceTemplate = Literal[
    "damodaran_implied_erp.v1",
    "damodaran_country_risk.v1",
    "damodaran_industry_betas.v1",
    "damodaran_rating_spreads.v1",
]
Region = Literal["us", "europe", "japan", "aus_nz_canada", "emerging", "china", "india", "global"]
Variant = Literal["large_cap", "small_cap"]


async def file_body(request: Request) -> bytes:
    """The raw upload, bounded before and after reading."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES:
        raise ApiError(413, "file_too_large", f"Files are limited to {MAX_BYTES // 2**20} MB.")
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise ApiError(413, "file_too_large", f"Files are limited to {MAX_BYTES // 2**20} MB.")
    if not data:
        raise ApiError(422, "validation_error", "The request body (the file) is empty.")
    return data


Upload = Annotated[bytes, Depends(file_body)]


def _writable_archive(settings: Settings) -> None:
    if not os.access(settings.raw_root, os.W_OK):
        raise ApiError(
            503,
            "archive_unavailable",
            "The raw archive is not writable by the API process; file ingestion is not "
            "available here (see docs/operations/workflow-api.md).",
        )


def _preview(fn: Any) -> JSONResponse:
    try:
        report = fn()
    except (
        statement_import.ImportRefusedError,
        market_import.ImportRefusedError,
        damodaran.ImportRefusedError,
    ) as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    return JSONResponse(report.as_dict())


class StatementIngestOut(BaseModel):
    import_id: str | None
    status: str
    inserted: int
    duplicates: int
    conflicting: int
    skipped: int
    dataset: DatasetActionOut | None


class MarketIngestOut(BaseModel):
    import_id: str | None
    status: str
    inserted: int
    already_present: int
    skipped: int


class ReferenceIngestOut(BaseModel):
    import_id: str | None
    status: str
    dataset_id: str | None
    version_no: int | None
    stored_records: int


def _common(filename: str, data: bytes, **params: Any) -> dict[str, Any]:
    return {"filename": filename, "file": data, **params}


# --------------------------------------------------------------------------- statements


@router.post("/statements/validate")
def validate_statements(
    filename: FileName,
    data: Upload,
    db: ReadDb,
    settings: AppSettings,
    _: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
) -> JSONResponse:
    """Preview a ``financial_statements.v1`` file: every record's status, the file hash.
    Read-only."""
    return _preview(
        lambda: statement_import.validate(
            db,
            filename=filename,
            data=data,
            mappings_root=settings.mappings_root,
            default_mapping_version=settings.mapping_version,
        )
    )


@router.post("/statements", response_model=ActionOut, status_code=201)
def ingest_statements(
    filename: FileName,
    expect_sha256: Sha256,
    source: Source,
    data: Upload,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.DATA_IMPORT),  # type: ignore[valid-type]
    source_reference: SourceRef = None,
    allow_partial: bool = False,
    allow_conflicts: bool = False,
    build_dataset: bool = True,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Ingest a previewed statement file and build the next ``reported`` draft.
    **Pending approval (P15).**"""

    def run() -> tuple[int, BaseModel]:
        _writable_archive(settings)
        r = statement_import.ingest(
            db,
            filename=filename,
            data=data,
            source_name=source,
            expected_sha256=expect_sha256,
            raw_root=settings.raw_root,
            mappings_root=settings.mappings_root,
            default_mapping_version=settings.mapping_version,
            source_reference=source_reference,
            allow_partial=allow_partial,
            allow_conflicts=allow_conflicts,
            build_dataset=build_dataset,
            user_id=ctx.user.id,
        )
        out = StatementIngestOut(
            import_id=str(r.import_id) if r.import_id else None,
            status=r.status,
            inserted=r.inserted,
            duplicates=r.duplicates,
            conflicting=r.conflicting,
            skipped=r.skipped,
            dataset=_dataset_result(r.dataset.dataset, r.dataset.created) if r.dataset else None,
        )
        return (201 if r.import_id else 200), out

    request = _common(
        filename,
        data,
        source=source,
        source_reference=source_reference,
        allow_partial=allow_partial,
        allow_conflicts=allow_conflicts,
        build_dataset=build_dataset,
        expect_sha256=expect_sha256.lower(),
    )
    return perform(
        db,
        ctx,
        action="import.statements",
        request=request,
        idempotency_key=idempotency_key,
        target=expect_sha256.lower(),
        run=run,
    )


# --------------------------------------------------------------------------- market data


@router.post("/market/validate")
def validate_market(
    template: MarketTemplate,
    filename: FileName,
    data: Upload,
    db: ReadDb,
    _: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
) -> JSONResponse:
    """Preview a ``market_prices.v1`` or ``corporate_actions.v1`` file. Read-only."""
    return _preview(
        lambda: market_import.validate(db, filename=filename, data=data, template=template)
    )


@router.post("/market", response_model=ActionOut, status_code=201)
def ingest_market(
    template: MarketTemplate,
    filename: FileName,
    expect_sha256: Sha256,
    source: Source,
    data: Upload,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.DATA_IMPORT),  # type: ignore[valid-type]
    source_reference: SourceRef = None,
    allow_partial: bool = False,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Ingest a previewed price or corporate-action file (append-only). **Pending (P15).**"""

    def run() -> tuple[int, BaseModel]:
        _writable_archive(settings)
        r = market_import.ingest(
            db,
            filename=filename,
            data=data,
            template=template,
            source_name=source,
            expected_sha256=expect_sha256,
            raw_root=settings.raw_root,
            source_reference=source_reference,
            allow_partial=allow_partial,
            user_id=ctx.user.id,
        )
        out = MarketIngestOut(
            import_id=str(r.import_id) if r.import_id else None,
            status=r.status,
            inserted=r.inserted,
            already_present=r.already_present,
            skipped=r.skipped,
        )
        return (201 if r.import_id else 200), out

    request = _common(
        filename,
        data,
        template=template,
        source=source,
        source_reference=source_reference,
        allow_partial=allow_partial,
        expect_sha256=expect_sha256.lower(),
    )
    return perform(
        db,
        ctx,
        action="import.market",
        request=request,
        idempotency_key=idempotency_key,
        target=expect_sha256.lower(),
        run=run,
    )


# --------------------------------------------------------------------------- reference data


@router.post("/reference/validate")
def validate_reference(
    template: ReferenceTemplate,
    filename: FileName,
    edition_date: date,
    data: Upload,
    db: ReadDb,
    _: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    region: Region | None = None,
    variant: Variant | None = None,
) -> JSONResponse:
    """Preview a Damodaran template file against its lineage's current version. Read-only."""
    return _preview(
        lambda: damodaran.validate(
            db,
            filename=filename,
            data=data,
            template=template,
            edition_date=edition_date,
            region=region,
            variant=variant,
        )
    )


@router.post("/reference", response_model=ActionOut, status_code=201)
def ingest_reference(
    template: ReferenceTemplate,
    filename: FileName,
    edition_date: date,
    expect_sha256: Sha256,
    source: Source,
    data: Upload,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.DATA_IMPORT),  # type: ignore[valid-type]
    region: Region | None = None,
    variant: Variant | None = None,
    source_reference: SourceRef = None,
    allow_partial: bool = False,
    supersede: bool = False,
    reason: Annotated[str | None, Query(min_length=10, max_length=2000)] = None,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Ingest a previewed Damodaran file as a new dataset version; a changed lineage needs
    ``supersede=true`` and a ``reason``. **Pending approval (P15).**"""

    def run() -> tuple[int, BaseModel]:
        _writable_archive(settings)
        r = damodaran.ingest(
            db,
            filename=filename,
            data=data,
            template=template,
            edition_date=edition_date,
            source_name=source,
            expected_sha256=expect_sha256,
            raw_root=settings.raw_root,
            region=region,
            variant=variant,
            source_reference=source_reference,
            allow_partial=allow_partial,
            supersede=supersede,
            reason=reason,
            user_id=ctx.user.id,
        )
        out = ReferenceIngestOut(
            import_id=str(r.import_id) if r.import_id else None,
            status=r.status,
            dataset_id=str(r.dataset_id) if r.dataset_id else None,
            version_no=r.version_no,
            stored_records=r.stored_records,
        )
        return (201 if r.import_id else 200), out

    request = _common(
        filename,
        data,
        template=template,
        edition_date=edition_date,
        region=region,
        variant=variant,
        source=source,
        source_reference=source_reference,
        allow_partial=allow_partial,
        supersede=supersede,
        reason=reason,
        expect_sha256=expect_sha256.lower(),
    )
    return perform(
        db,
        ctx,
        action="import.reference",
        request=request,
        idempotency_key=idempotency_key,
        target=expect_sha256.lower(),
        run=run,
    )
