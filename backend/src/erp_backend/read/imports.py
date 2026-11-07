"""File-import history (read-only). The archive path of the stored file is never exposed."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend.models import (
    CorporateAction,
    PriceObservation,
    ReferenceDataset,
    SourceImport,
    StatementImportValue,
)
from erp_backend.read.common import not_found, paginate
from erp_backend.read.schemas import ImportDetailOut, ImportOut, Page

CATEGORY_PREFIX = {
    "statement": ("financial_statements.",),
    "market": ("market_prices.", "corporate_actions."),
    "reference": ("damodaran_",),
}
COUNT_KEYS = ("accepted", "duplicate", "conflict", "conflicting", "unmapped", "ambiguous",
              "rejected", "storable", "records", "warnings")  # fmt: skip
MAX_SKIPPED_SHOWN = 200


def category_of(template: str) -> str:
    for category, prefixes in CATEGORY_PREFIX.items():
        if template.startswith(prefixes):
            return category
    return "other"


def _import(r: SourceImport) -> ImportOut:
    summary = r.summary or {}
    return ImportOut(
        id=r.id,
        category=category_of(r.template_version),
        template_version=r.template_version,
        provider_code=r.provider_code,
        file_kind=r.kind,
        original_filename=r.original_filename,
        file_sha256=r.file_sha256,
        bytes=r.bytes,
        source_name=r.source_name,
        source_reference=r.source_reference,
        status=r.status,
        record_counts={k: int(summary[k]) for k in COUNT_KEYS if isinstance(summary.get(k), int)},
        skipped_count=len(r.skipped or []),
        imported_at=r.imported_at,
    )


def list_imports(
    db: Session,
    *,
    category: str | None,
    template: str | None,
    status: str | None,
    file_sha256: str | None,
    imported_from: date | None,
    imported_to: date | None,
    limit: int,
    offset: int,
) -> Page[ImportOut]:
    stmt = select(SourceImport)
    if category:
        prefixes = CATEGORY_PREFIX[category]
        from sqlalchemy import or_

        stmt = stmt.where(or_(*(SourceImport.template_version.startswith(p) for p in prefixes)))
    if template:
        stmt = stmt.where(SourceImport.template_version == template)
    if status:
        stmt = stmt.where(SourceImport.status == status)
    if file_sha256:
        stmt = stmt.where(SourceImport.file_sha256 == file_sha256.lower())
    if imported_from:
        stmt = stmt.where(func.date(SourceImport.imported_at) >= imported_from)
    if imported_to:
        stmt = stmt.where(func.date(SourceImport.imported_at) <= imported_to)
    stmt = stmt.order_by(SourceImport.imported_at.desc(), SourceImport.id)
    return paginate(db, stmt, limit, offset, lambda rows: [_import(r) for r in rows])


def _stored_objects(db: Session, r: SourceImport) -> dict[str, Any]:
    category = category_of(r.template_version)
    if category == "statement":
        count = db.scalar(select(func.count()).where(StatementImportValue.import_id == r.id))
        return {"statement_values": count or 0}
    if category == "market":
        return {
            "price_observations": db.scalar(
                select(func.count()).where(PriceObservation.import_id == r.id)
            )
            or 0,
            "corporate_actions": db.scalar(
                select(func.count()).where(CorporateAction.import_id == r.id)
            )
            or 0,
        }
    if category == "reference":
        rows: Sequence[ReferenceDataset] = db.scalars(
            select(ReferenceDataset).where(ReferenceDataset.import_id == r.id)
        ).all()
        return {
            "reference_datasets": [
                {"id": str(d.id), "lineage_key": d.lineage_key, "version_no": d.version_no}
                for d in rows
            ]
        }
    return {}


def get_import(db: Session, import_id: uuid.UUID) -> ImportDetailOut:
    r = db.get(SourceImport, import_id)
    if r is None:
        raise not_found("import", import_id)
    skipped = list(r.skipped or [])
    return ImportDetailOut(
        **_import(r).model_dump(),
        summary=r.summary or {},
        skipped=skipped[:MAX_SKIPPED_SHOWN],
        skipped_truncated=len(skipped) > MAX_SKIPPED_SHOWN,
        stored_objects=_stored_objects(db, r),
    )
