"""Versioned reference datasets (Damodaran; D58), read-only.

A dataset id names one immutable version, so ``/reference/datasets/{id}/records`` is
reproducible forever. ``is_current`` and ``superseded_by_id`` describe the lineage as it
stands now; neither changes the content of any version.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from erp_backend.models import ReferenceDataset, ReferenceValue, SourceImport
from erp_backend.read.common import not_found, paginate
from erp_backend.read.schemas import (
    Page,
    ReferenceDatasetDetailOut,
    ReferenceDatasetOut,
    ReferenceRecordOut,
    ReferenceValueOut,
)


def _lineage_state(
    db: Session, rows: Sequence[ReferenceDataset]
) -> tuple[dict[str, int], dict[uuid.UUID, uuid.UUID]]:
    keys = {d.lineage_key for d in rows}
    if not keys:
        return {}, {}
    latest = dict(
        db.execute(
            select(ReferenceDataset.lineage_key, func.max(ReferenceDataset.version_no))
            .where(ReferenceDataset.lineage_key.in_(keys))
            .group_by(ReferenceDataset.lineage_key)
        ).all()
    )
    ids = [d.id for d in rows]
    successors: dict[uuid.UUID, uuid.UUID] = dict(
        db.execute(  # type: ignore[arg-type]
            select(ReferenceDataset.supersedes_id, ReferenceDataset.id).where(
                ReferenceDataset.supersedes_id.in_(ids)
            )
        ).all()
    )
    return latest, successors


def _out(
    d: ReferenceDataset, latest: dict[str, int], successors: dict[uuid.UUID, uuid.UUID]
) -> ReferenceDatasetOut:
    return ReferenceDatasetOut(
        id=d.id,
        provider_code=d.provider_code,
        kind=d.dataset_kind,
        template_version=d.template_version,
        edition_date=d.edition_date,
        region=d.region,
        variant=d.variant,
        lineage_key=d.lineage_key,
        version_no=d.version_no,
        is_current=latest.get(d.lineage_key) == d.version_no,
        supersedes_id=d.supersedes_id,
        superseded_by_id=successors.get(d.id),
        reason=d.reason,
        record_count=d.record_count,
        content_sha256=d.content_sha256,
        import_id=d.import_id,
        created_at=d.created_at,
    )


def list_reference_datasets(
    db: Session,
    *,
    kind: str | None,
    edition_date: date | None,
    region: str | None,
    variant: str | None,
    current_only: bool,
    limit: int,
    offset: int,
) -> Page[ReferenceDatasetOut]:
    stmt = select(ReferenceDataset)
    if kind:
        stmt = stmt.where(ReferenceDataset.dataset_kind == kind)
    if edition_date:
        stmt = stmt.where(ReferenceDataset.edition_date == edition_date)
    if region:
        stmt = stmt.where(ReferenceDataset.region == region)
    if variant:
        stmt = stmt.where(ReferenceDataset.variant == variant)
    if current_only:
        newer = aliased(ReferenceDataset)
        stmt = stmt.where(
            ~select(newer.id)
            .where(
                newer.lineage_key == ReferenceDataset.lineage_key,
                newer.version_no > ReferenceDataset.version_no,
            )
            .exists()
        )
    stmt = stmt.order_by(
        ReferenceDataset.dataset_kind,
        ReferenceDataset.edition_date.desc(),
        ReferenceDataset.lineage_key,
        ReferenceDataset.version_no.desc(),
    )

    def mapper(rows: Sequence[ReferenceDataset]) -> list[ReferenceDatasetOut]:
        latest, successors = _lineage_state(db, rows)
        return [_out(d, latest, successors) for d in rows]

    return paginate(db, stmt, limit, offset, mapper)


def get_reference_dataset(db: Session, dataset_id: uuid.UUID) -> ReferenceDatasetDetailOut:
    d = db.get(ReferenceDataset, dataset_id)
    if d is None:
        raise not_found("reference dataset", dataset_id)
    imp = db.get(SourceImport, d.import_id)
    assert imp is not None  # FK
    latest, successors = _lineage_state(db, [d])
    return ReferenceDatasetDetailOut(
        **_out(d, latest, successors).model_dump(),
        source_name=imp.source_name,
        source_reference=imp.source_reference,
        original_filename=imp.original_filename,
        file_sha256=imp.file_sha256,
        import_status=imp.status,
    )


def list_reference_records(
    db: Session,
    dataset_id: uuid.UUID,
    *,
    record_key: str | None,
    fields: list[str] | None,
    limit: int,
    offset: int,
) -> Page[ReferenceRecordOut]:
    """Records of one version, paginated by record (all requested fields of each record)."""
    if db.get(ReferenceDataset, dataset_id) is None:
        raise not_found("reference dataset", dataset_id)
    keys = (
        select(ReferenceValue.record_key, func.min(ReferenceValue.import_row).label("row"))
        .where(ReferenceValue.dataset_id == dataset_id)
        .group_by(ReferenceValue.record_key)
    )
    if record_key:
        keys = keys.where(ReferenceValue.record_key == record_key)
    keys = keys.order_by(func.min(ReferenceValue.import_row), ReferenceValue.record_key)

    def mapper(rows: Sequence[tuple[str, int]]) -> list[ReferenceRecordOut]:
        wanted = [k for k, _ in rows]
        stmt = select(ReferenceValue).where(
            ReferenceValue.dataset_id == dataset_id, ReferenceValue.record_key.in_(wanted)
        )
        if fields:
            stmt = stmt.where(ReferenceValue.field.in_(fields))
        grouped: dict[str, dict[str, ReferenceValueOut]] = defaultdict(dict)
        first: dict[str, ReferenceValue] = {}
        for v in db.scalars(stmt.order_by(ReferenceValue.field)) if wanted else []:
            first.setdefault(v.record_key, v)
            grouped[v.record_key][v.field] = ReferenceValueOut(
                record_key=v.record_key,
                field=v.field,
                value=v.value,
                value_text=v.value_text,
                unit=v.unit,
                import_row=v.import_row,
                locator=v.locator,
            )
        return [
            ReferenceRecordOut(
                record_key=k,
                import_row=row,
                locator=first[k].locator if k in first else "",
                fields=grouped.get(k, {}),
            )
            for k, row in rows
        ]

    return paginate(db, keys, limit, offset, mapper, scalars=False)
