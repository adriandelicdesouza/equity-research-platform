"""Typed sources of assumption values and their conversion at the input boundary (design §5).

* ``rate_observation`` — a stored ``market.rate_observations`` row (percentage points, D58),
  converted to a fraction: ``value / 100`` (exact).
* ``reference_value`` — one field of one record of a stored ``ref.reference_datasets``
  version (Damodaran, D58), converted by its stored unit (``percent`` = percentage points).
* ``analyst`` — a written source reference (with ``as_of`` / ``currency`` where the
  assumption is a market input).

Stored rows are only read, never changed; the resolved source is recorded with the published
value, unit and conversion so a run can show where every number came from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from erp_engine.core.numbers import make_context
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend.models import RateObservationRow, RateSeries, ReferenceDataset, ReferenceValue
from erp_backend.valuation.errors import ValuationRefused

HUNDRED = Decimal(100)
NUMERIC_REFERENCE_UNITS = {"percent", "ratio", "multiple", "count"}


@dataclass(frozen=True)
class Resolved:
    """A source resolved to a value in the engine's units, with its lineage."""

    source_type: str  # analyst | rate_observation | reference_value
    provenance_kind: str  # analyst_assumption | provider_market | reference
    value: Decimal | None  # converted value (None for analyst sources: the analyst's value)
    published_value: Decimal | None
    published_unit: str | None
    conversion: str | None
    as_of: date | None
    currency: str | None
    source_ref: str
    source_sha256: str | None
    superseded: bool
    record: dict[str, Any]  # enriched source JSON to store with the assumption / run input


def _refuse(code: str, message: str, **detail: Any) -> ValuationRefused:
    return ValuationRefused(code, message, [detail] if detail else [])


def pp_to_fraction(value: Decimal) -> Decimal:
    with localcontext(make_context()):
        return value / HUNDRED


def rate_observation(db: Session, observation_id: int) -> Resolved:
    obs = db.get(RateObservationRow, observation_id)
    if obs is None:
        raise _refuse(
            "source_not_found",
            f"No rate observation {observation_id}.",
            observation_id=observation_id,
        )
    series = db.get(RateSeries, obs.series_code)
    if series is None:  # pragma: no cover - FK guarantees it
        raise _refuse("source_not_found", f"No rate series {obs.series_code}.")
    newer = db.scalar(
        select(RateObservationRow.id).where(
            RateObservationRow.series_code == obs.series_code,
            RateObservationRow.obs_date == obs.obs_date,
            RateObservationRow.id > obs.id,
        )
    )
    return Resolved(
        source_type="rate_observation",
        provenance_kind="provider_market",
        value=pp_to_fraction(obs.value),
        published_value=obs.value,
        published_unit=series.unit,
        conversion="pp_to_fraction",
        as_of=obs.obs_date,
        currency=series.currency,
        source_ref=f"rate_observation:{obs.id}",
        source_sha256=None,
        superseded=newer is not None,
        record={
            "type": "rate_observation",
            "observation_id": obs.id,
            "series_code": obs.series_code,
            "provider_code": obs.provider_code,
            "obs_date": obs.obs_date.isoformat(),
            "published_value": str(obs.value),
            "published_unit": series.unit,
            "conversion": "pp_to_fraction",
            "currency": series.currency,
            "fetch_id": str(obs.fetch_id),
        },
    )


def reference_value(db: Session, dataset_id: uuid.UUID, record_key: str, field: str) -> Resolved:
    ds = db.get(ReferenceDataset, dataset_id)
    if ds is None:
        raise _refuse("source_not_found", f"No reference dataset {dataset_id}.")
    row = db.scalars(
        select(ReferenceValue).where(
            ReferenceValue.dataset_id == dataset_id,
            ReferenceValue.record_key == record_key,
            ReferenceValue.field == field,
        )
    ).first()
    if row is None:
        raise _refuse(
            "source_not_found",
            f"No field {field!r} of record {record_key!r} in reference dataset {dataset_id}.",
            record_key=record_key,
            field=field,
        )
    if row.value is None or row.unit not in NUMERIC_REFERENCE_UNITS:
        raise _refuse(
            "source_unit_unsupported",
            f"Reference field {field!r} is not numeric in a supported unit ({row.unit}).",
            unit=row.unit,
        )
    conversion = "pp_to_fraction" if row.unit == "percent" else None
    value = pp_to_fraction(row.value) if conversion else row.value
    newer = db.scalar(select(ReferenceDataset.id).where(ReferenceDataset.supersedes_id == ds.id))
    return Resolved(
        source_type="reference_value",
        provenance_kind="reference",
        value=value,
        published_value=row.value,
        published_unit=row.unit,
        conversion=conversion,
        as_of=ds.edition_date,
        currency=None,
        source_ref=f"reference_value:{ds.id}:{record_key}:{field}",
        source_sha256=ds.content_sha256,
        superseded=newer is not None,
        record={
            "type": "reference_value",
            "reference_dataset_id": str(ds.id),
            "record_key": record_key,
            "field": field,
            "dataset_kind": ds.dataset_kind,
            "edition_date": ds.edition_date.isoformat(),
            "version_no": ds.version_no,
            "content_sha256": ds.content_sha256,
            "published_value": str(row.value),
            "published_unit": row.unit,
            "conversion": conversion,
            "locator": row.locator,
        },
    )


def analyst(source: dict[str, Any], *, market: bool) -> Resolved:
    reference = str(source.get("reference") or "").strip()
    if len(reference) < 3:
        raise _refuse("source_required", "An analyst source needs a written reference.")
    as_of = source.get("as_of")
    currency = source.get("currency")
    if market and (as_of is None or currency is None):
        raise _refuse(
            "source_required", "A market input entered by an analyst needs 'as_of' and 'currency'."
        )
    as_of_date = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    record: dict[str, Any] = {"type": "analyst", "reference": reference}
    if as_of_date is not None:
        record["as_of"] = as_of_date.isoformat()
    if currency is not None:
        record["currency"] = str(currency)
    return Resolved(
        source_type="analyst",
        provenance_kind="analyst_assumption",
        value=None,
        published_value=None,
        published_unit=None,
        conversion=None,
        as_of=as_of_date,
        currency=str(currency) if currency is not None else None,
        source_ref=f"analyst:{reference[:200]}",
        source_sha256=None,
        superseded=False,
        record=record,
    )


def resolve(db: Session, source: dict[str, Any] | None, *, market: bool) -> Resolved | None:
    """Resolve a stored or submitted source dict; ``None`` when no source was given."""
    if source is None:
        return None
    kind = source.get("type")
    if kind == "rate_observation":
        return rate_observation(db, int(source["observation_id"]))
    if kind == "reference_value":
        return reference_value(
            db,
            uuid.UUID(str(source["reference_dataset_id"])),
            str(source["record_key"]),
            str(source["field"]),
        )
    if kind == "analyst":
        return analyst(source, market=market)
    raise _refuse("source_invalid", f"Unknown source type {kind!r}.")
