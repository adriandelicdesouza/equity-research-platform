"""Assumption registry, append-only revisions and approval (design §9.3; D14, Q9, Q10).

Every change is a new ``val.assumption_revisions`` row; the current value of a key is its
latest revision. One request is one scenario revision: all of its changes apply atomically.
Approval is a revision too, so who approved what, and when, stays in the history. Only
``approved`` values may enter a saved run.

**Inheritance (Phase 5b):** a scenario's *resolved* state overlays its ancestors' current
values, root first, with its own on top; a ``cleared`` revision in a child hides an inherited
value. Runs use the resolved state; revisions and approvals act on the scenario's own rows.

Units: ratios are fractions (``0.05`` = 5%), money is in the dataset's currency, counts are
integers. Values sourced from a stored rate or reference value must equal that source after
conversion at this boundary (percentage points → fractions).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from erp_engine.inputs import InstrumentKind, InstrumentTreatment
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.models import AssumptionRevision, Scenario, ValuationProject
from erp_backend.valuation import sources
from erp_backend.valuation.blocks import LEASE_TREATMENTS, Block
from erp_backend.valuation.common import (
    Actor,
    check_revision,
    require_active,
    write_access,
)
from erp_backend.valuation.errors import ValuationRefused

ENTITY = "valuation_assumption"
MAX_CHANGES = 400
D = Decimal
UNITS = {
    "ratio": "ratio",
    "money": "currency",
    "number": "number",
    "count": "count",
    "choice": "choice",
    "text": "text",
    "json": "json",
}


@dataclass(frozen=True)
class KeySpec:
    key: str
    kind: str  # ratio | money | number | count | choice | text | json
    label: str
    per_period: bool = False
    choices: tuple[str, ...] = ()
    low: Decimal | None = None
    high: Decimal | None = None
    sources: tuple[str, ...] = ("analyst",)
    source_required: bool = False
    market: bool = False  # analyst source needs as_of + currency
    methodologies: tuple[str, ...] = ("dcf_fcff", "dcf_fcfe")

    @property
    def unit(self) -> str:
        return UNITS[self.kind]


def _spec(key: str, kind: str, label: str, **kw: Any) -> KeySpec:
    for bound in ("low", "high"):
        if bound in kw and kw[bound] is not None:
            kw[bound] = D(kw[bound])
    return KeySpec(key, kind, label, **kw)


REF = ("analyst", "reference_value")
REGISTRY: dict[str, KeySpec] = {
    s.key: s
    for s in (
        # Forecast drivers, per year 1..N (fractions of revenue unless stated).
        _spec("revenue_growth", "ratio", "Revenue growth", per_period=True, low="-1", high="5"),
        _spec("ebit_margin", "ratio", "EBIT margin", per_period=True, low="-5", high="1"),
        _spec("tax_rate", "ratio", "Cash tax rate", per_period=True, low="0", high="1"),
        _spec("da_pct", "ratio", "D&A % revenue", per_period=True, low="0", high="2"),
        _spec("capex_pct", "ratio", "Capex % revenue", per_period=True, low="0", high="2"),
        _spec("nwc_pct", "ratio", "NWC % revenue", per_period=True, low="-2", high="2"),
        # Model settings.
        _spec("horizon_years", "count", "Forecast horizon (years)", low="1", high="20"),
        _spec(
            "convention",
            "choice",
            "Discounting convention",
            choices=("end_of_period", "mid_period"),
        ),
        _spec(
            "lease_treatment",
            "choice",
            "Lease treatment",
            choices=LEASE_TREATMENTS,
            methodologies=("dcf_fcff",),
        ),
        # Terminal value (D27, D35).
        _spec(
            "terminal_form",
            "choice",
            "Terminal value form",
            choices=("value_driver", "simple", "exit_multiple"),
        ),
        _spec("terminal_growth", "ratio", "Terminal growth g", low="-0.1", high="0.2"),
        _spec("ronic", "ratio", "RONIC / ROE on new investment", low="0.0001", high="5"),
        _spec("exit_multiple", "number", "Exit multiple", low="0.0001", high="200"),
        _spec(
            "exit_metric", "choice", "Exit multiple metric", choices=("ebitda", "ebit", "revenue")
        ),
        _spec(
            "exit_lease_basis",
            "choice",
            "Exit multiple lease basis",
            choices=("pre_lease", "post_lease"),
        ),
        _spec(
            "terminal_timing",
            "choice",
            "Terminal value timing",
            choices=("follow_convention", "end_of_period"),
        ),
        # Cost of equity (D21).
        _spec(
            "risk_free_rate",
            "ratio",
            "Risk-free rate",
            low="-0.05",
            high="0.3",
            sources=("analyst", "rate_observation"),
            source_required=True,
            market=True,
        ),
        _spec("erp_basis", "choice", "ERP basis", choices=("mature_plus_crp", "total_country_erp")),
        _spec(
            "mature_market_erp",
            "ratio",
            "Mature-market ERP",
            low="0",
            high="0.3",
            sources=REF,
            source_required=True,
        ),
        _spec(
            "total_country_erp",
            "ratio",
            "Total country ERP",
            low="0",
            high="0.4",
            sources=REF,
            source_required=True,
        ),
        _spec(
            "country_risk_premium",
            "ratio",
            "Country risk premium",
            low="0",
            high="0.5",
            sources=REF,
            source_required=True,
        ),
        _spec("crp_method", "choice", "CRP exposure method", choices=("lambda", "beta", "flat")),
        _spec("crp_lambda", "ratio", "CRP exposure λ", low="0", high="10"),
        _spec("crp_country", "text", "CRP country (ISO 3166-1 alpha-2)"),
        _spec("crp_reason", "text", "Reason for a non-zero CRP in the US/Canada"),
        _spec("specific_premium", "ratio", "Company-specific premium", low="-0.1", high="0.3"),
        _spec("specific_premium_rationale", "text", "Specific premium rationale"),
        _spec("beta_method", "choice", "Beta method", choices=("analyst_levered", "bottom_up")),
        _spec("beta_levered", "number", "Levered beta", low="-1", high="5", source_required=True),
        _spec(
            "beta_unlevered",
            "number",
            "Unlevered (industry) beta",
            low="0",
            high="5",
            sources=REF,
            source_required=True,
        ),
        _spec("relevering", "choice", "Relevering formula", choices=("hamada", "harris_pringle")),
        _spec("target_debt_to_equity", "ratio", "Target debt / equity", low="0", high="20"),
        _spec("marginal_tax_rate", "ratio", "Marginal tax rate", low="0", high="1"),
        # Cost of debt and weights.
        _spec(
            "cost_of_debt_method",
            "choice",
            "Cost of debt method",
            choices=("direct", "rf_plus_spread"),
        ),
        _spec(
            "pre_tax_cost_of_debt",
            "ratio",
            "Pre-tax cost of debt",
            low="0",
            high="1",
            source_required=True,
        ),
        _spec(
            "credit_spread",
            "ratio",
            "Credit spread",
            low="0",
            high="1",
            sources=REF,
            source_required=True,
        ),
        _spec(
            "capital_structure_basis",
            "choice",
            "WACC weights",
            choices=("market", "target"),
            methodologies=("dcf_fcff",),
        ),
        # FCFE financing (opening balances come from the dataset).
        _spec(
            "debt_forecast",
            "money",
            "Debt at year end",
            per_period=True,
            low="0",
            methodologies=("dcf_fcfe",),
        ),
        _spec(
            "cash_forecast",
            "money",
            "Cash at year end",
            per_period=True,
            low="0",
            methodologies=("dcf_fcfe",),
        ),
        _spec(
            "cash_yield", "ratio", "Yield on cash", low="0", high="0.5", methodologies=("dcf_fcfe",)
        ),
        # Bridge additions and dilution (Q10: instruments are scenario assumptions).
        _spec(
            "non_operating_assets",
            "money",
            "Non-operating assets",
            low="0",
            methodologies=("dcf_fcff",),
        ),
        _spec("other_claims", "json", "Other claims", methodologies=("dcf_fcff",)),
        _spec("instruments", "json", "Dilutive instruments"),
        _spec("share_adjustments", "json", "Share-count adjustments after B"),
    )
}
PER_YEAR_DRIVERS = ("revenue_growth", "ebit_margin", "tax_rate", "da_pct", "capex_pct", "nwc_pct")


@dataclass
class Change:
    key: str
    period_index: int | None = None
    value: str | None = None
    choice: str | None = None
    json: Any = None
    text: str | None = None
    clear: bool = False
    source: dict[str, Any] | None = None
    status: str = "approved"
    note: str | None = None


@dataclass(frozen=True)
class Current:
    """The current revision of one (key, period_index)."""

    row: AssumptionRevision
    inherited_from: uuid.UUID | None = None  # the ancestor scenario the value comes from

    @property
    def key(self) -> str:
        return self.row.key

    @property
    def period_index(self) -> int | None:
        return self.row.period_index

    @property
    def approved(self) -> bool:
        return self.row.status == "approved"

    def payload(self) -> Any:
        return self.row.value if self.row.value is not None else self.row.value_json


@dataclass
class State:
    scenario_revision: int
    items: dict[tuple[str, int | None], Current] = field(default_factory=dict)

    def get(self, key: str, period: int | None = None) -> Current | None:
        return self.items.get((key, period))

    def value(self, key: str, period: int | None = None) -> Any:
        cur = self.get(key, period)
        return None if cur is None else cur.payload()


def _refuse(
    code: str, message: str, change: Change | None = None, **extra: Any
) -> ValuationRefused:
    detail = {"key": change.key, "period_index": change.period_index} if change else {}
    return ValuationRefused(code, message, [{**detail, **extra}])


def _decimal(raw: str, change: Change) -> Decimal:
    if not isinstance(raw, str) or not re.fullmatch(r"-?\d+(\.\d+)?", raw.strip()):
        raise _refuse("invalid_value", "Values are plain decimal strings (e.g. '0.05').", change)
    try:
        return D(raw.strip())
    except InvalidOperation as exc:  # pragma: no cover - guarded by the pattern
        raise _refuse("invalid_value", "Not a decimal.", change) from exc


def _dec_field(item: dict[str, Any], name: str, change: Change, *, positive: bool = False) -> str:
    raw = item.get(name)
    value = _decimal(str(raw) if raw is not None else "", change)
    if positive and value <= 0:
        raise _refuse("invalid_value", f"{name} must be positive.", change)
    return str(value)


def _json_value(spec: KeySpec, data: Any, change: Change) -> Any:
    if not isinstance(data, list) or len(data) > 200:
        raise _refuse("invalid_value", f"{spec.key} must be a list (≤ 200 items).", change)
    out: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise _refuse("invalid_value", f"{spec.key} items must be objects.", change)
        if spec.key in {"instruments", "other_claims"}:
            ident = str(item.get("id") or "").strip()
            if not 1 <= len(ident) <= 64 or ident in ids:
                raise _refuse("invalid_value", "Each item needs a unique id (1–64 chars).", change)
            ids.add(ident)
        if spec.key == "instruments":
            kind, treatment = item.get("kind"), item.get("treatment")
            if kind not in {k.value for k in InstrumentKind} or treatment not in {
                t.value for t in InstrumentTreatment
            }:
                raise _refuse("invalid_value", "Unknown instrument kind or treatment.", change)
            entry: dict[str, Any] = {
                "id": ident,
                "kind": kind,
                "treatment": treatment,
                "count": _dec_field(item, "count", change, positive=True),
            }
            for opt in ("strike", "face_value"):
                if item.get(opt) is not None:
                    entry[opt] = _dec_field(item, opt, change)
            if item.get("exclusion_reason"):
                entry["exclusion_reason"] = str(item["exclusion_reason"])[:500]
            out.append(entry)
        elif spec.key == "other_claims":
            label = str(item.get("label") or "").strip()
            if not label:
                raise _refuse("invalid_value", "Each claim needs a label.", change)
            out.append(
                {"id": ident, "label": label[:200], "amount": _dec_field(item, "amount", change)}
            )
        else:  # share_adjustments
            try:
                when = date.fromisoformat(str(item.get("date")))
            except ValueError as exc:
                raise _refuse(
                    "invalid_value", "Each adjustment needs an ISO date.", change
                ) from exc
            source = str(item.get("source") or "").strip()
            if len(source) < 3:
                raise _refuse("invalid_value", "Each adjustment needs a source.", change)
            out.append(
                {
                    "date": when.isoformat(),
                    "shares": _dec_field(item, "shares", change),
                    "source": source[:500],
                }
            )
    return out


def _validate_value(spec: KeySpec, change: Change) -> tuple[Decimal | None, Any]:
    """Returns (numeric value, JSON value): exactly one is set."""
    if spec.kind == "choice":
        if change.choice not in spec.choices:
            raise _refuse(
                "invalid_value", f"{spec.key} must be one of {', '.join(spec.choices)}.", change
            )
        return None, change.choice
    if spec.kind == "text":
        text = (change.text or "").strip()
        if not 1 <= len(text) <= 2000:
            raise _refuse("invalid_value", f"{spec.key} needs 1–2000 characters.", change)
        if spec.key == "crp_country" and not re.fullmatch(r"[A-Z]{2}", text):
            raise _refuse("invalid_value", "crp_country is an ISO 3166-1 alpha-2 code.", change)
        return None, text
    if spec.kind == "json":
        return None, _json_value(spec, change.json, change)
    if change.value is None:
        raise _refuse("invalid_value", f"{spec.key} needs a value.", change)
    value = _decimal(change.value, change)
    if spec.kind == "count" and value != value.to_integral_value():
        raise _refuse("invalid_value", f"{spec.key} must be a whole number.", change)
    if (spec.low is not None and value < spec.low) or (spec.high is not None and value > spec.high):
        raise _refuse(
            "out_of_range",
            f"{spec.key} must be between {spec.low} and {spec.high} "
            "(ratios are fractions: 5% is 0.05).",
            change,
        )
    return value, None


def _validate_source(
    db: Session, spec: KeySpec, change: Change, value: Decimal | None
) -> tuple[str, dict[str, Any] | None]:
    """Returns (provenance kind, enriched source JSON)."""
    if change.source is None:
        if spec.source_required:
            raise _refuse(
                "source_required",
                f"{spec.key} needs a source ({' or '.join(spec.sources)}).",
                change,
            )
        return "analyst_assumption", None
    kind = change.source.get("type")
    if kind not in spec.sources:
        raise _refuse(
            "source_invalid", f"{spec.key} accepts sources: {', '.join(spec.sources)}.", change
        )
    resolved = sources.resolve(db, change.source, market=spec.market)
    assert resolved is not None
    if resolved.value is not None and value != resolved.value:
        raise _refuse(
            "source_mismatch",
            f"{spec.key} = {value} differs from its source ({resolved.value} after "
            f"{resolved.conversion or 'no conversion'}).",
            change,
            source_value=str(resolved.value),
        )
    return resolved.provenance_kind, resolved.record


MAX_DEPTH = 8  # scenario inheritance depth (root included)


def _latest_rows(db: Session, scenario_id: uuid.UUID) -> list[AssumptionRevision]:
    """Latest revision per (key, period) of one scenario, ``cleared`` rows included."""
    rank = (
        func.row_number()
        .over(
            partition_by=(
                AssumptionRevision.key,
                func.coalesce(AssumptionRevision.period_index, 0),
            ),
            order_by=AssumptionRevision.id.desc(),
        )
        .label("rank")
    )
    sub = (
        select(AssumptionRevision.id, rank)
        .where(AssumptionRevision.scenario_id == scenario_id)
        .subquery()
    )
    return list(
        db.scalars(
            select(AssumptionRevision)
            .join(sub, sub.c.id == AssumptionRevision.id)
            .where(sub.c.rank == 1)
            .order_by(AssumptionRevision.key, AssumptionRevision.period_index)
        ).all()
    )


def current_state(db: Session, scenario: Scenario) -> State:
    """The scenario's own latest revision per (key, period); cleared keys are absent."""
    state = State(scenario.revision_no)
    for r in _latest_rows(db, scenario.id):
        if r.status != "cleared":
            state.items[(r.key, r.period_index)] = Current(r)
    return state


def ancestors(db: Session, scenario: Scenario) -> list[Scenario]:
    """The parent chain, nearest first. Raises on a cycle or excessive depth (the service
    refuses both on write; this guards reads against corrupted data)."""
    out: list[Scenario] = []
    seen = {scenario.id}
    parent_id = scenario.parent_scenario_id
    while parent_id is not None:
        parent = db.get(Scenario, parent_id)
        if parent is None or parent.id in seen or len(out) + 1 >= MAX_DEPTH:
            raise ValuationRefused(
                "scenario_cycle", "The scenario's inheritance chain is invalid.", []
            )
        seen.add(parent.id)
        out.append(parent)
        parent_id = parent.parent_scenario_id
    return out


def resolved_state(db: Session, scenario: Scenario) -> State:
    """Values with inheritance applied: ancestors root first, then the scenario's own rows.
    Without a parent this equals ``current_state``."""
    chain = [*reversed(ancestors(db, scenario)), scenario]
    state = State(scenario.revision_no)
    for s in chain:
        inherited = s.id if s.id != scenario.id else None
        for r in _latest_rows(db, s.id):
            ident = (r.key, r.period_index)
            if r.status == "cleared":
                state.items.pop(ident, None)
            else:
                state.items[ident] = Current(r, inherited)
    return state


def _scenario_for_write(
    db: Session, actor: Actor, scenario_id: uuid.UUID, admin_reason: str | None
) -> tuple[Scenario, ValuationProject, Any]:
    scenario = db.scalars(
        select(Scenario).where(Scenario.id == scenario_id).with_for_update()
    ).first()
    if scenario is None:
        from erp_backend.errors import ApiError

        raise ApiError(404, "not_found", f"No scenario {scenario_id}.")
    project = db.get(ValuationProject, scenario.project_id)
    assert project is not None
    access = write_access(project, actor, admin_reason)
    require_active(project)
    require_active_scenario(scenario)
    return scenario, project, access


def require_active_scenario(scenario: Scenario) -> None:
    if scenario.status != "active":
        raise ValuationRefused(
            "scenario_archived", "The scenario is archived; restore it before changing it."
        )


def _snap(row: AssumptionRevision | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "value": str(row.value) if row.value is not None else row.value_json,
        "status": row.status,
        "source": row.source,
        "revision_id": row.id,
    }


def _previous(state: State, row: AssumptionRevision) -> dict[str, Any] | None:
    cur = state.get(row.key, row.period_index)
    return _snap(cur.row) if cur is not None else None


def revise(
    db: Session,
    actor: Actor,
    scenario_id: uuid.UUID,
    *,
    expected_revision: int,
    changes: list[Change],
    reason: str | None = None,
    admin_reason: str | None = None,
) -> State:
    scenario, project, access = _scenario_for_write(db, actor, scenario_id, admin_reason)
    check_revision(scenario.revision_no, expected_revision, "scenario")
    if not 1 <= len(changes) <= MAX_CHANGES:
        raise ValuationRefused("invalid_request", f"Send 1–{MAX_CHANGES} changes.")
    seen: set[tuple[str, int | None]] = set()
    before = current_state(db, scenario)
    new_rev = scenario.revision_no + 1
    rows: list[AssumptionRevision] = []
    for change in changes:
        spec = REGISTRY.get(change.key)
        if spec is None:
            raise _refuse("unknown_key", f"Unknown assumption key {change.key!r}.", change)
        if spec.per_period != (change.period_index is not None):
            raise _refuse(
                "invalid_period",
                f"{change.key} is "
                f"{'per year (period_index 1–20)' if spec.per_period else 'not per year'}.",
                change,
            )
        if change.period_index is not None and not 1 <= change.period_index <= 20:
            raise _refuse("invalid_period", "period_index must be 1–20.", change)
        ident = (change.key, change.period_index)
        if ident in seen:
            raise _refuse(
                "duplicate_change", "Each key/period may appear once per request.", change
            )
        seen.add(ident)
        if change.clear:
            if before.get(*ident) is None and (
                scenario.parent_scenario_id is None
                or resolved_state(db, scenario).get(*ident) is None
            ):
                raise _refuse("nothing_to_clear", f"{change.key} has no value.", change)
            rows.append(
                AssumptionRevision(
                    scenario_id=scenario.id,
                    scenario_revision_no=new_rev,
                    key=change.key,
                    period_index=change.period_index,
                    unit=spec.unit,
                    provenance_kind="analyst_assumption",
                    status="cleared",
                    note=change.note,
                    reason=reason,
                    created_by=actor.user_id,
                )
            )
            continue
        if change.status not in {"approved", "suggested"}:
            raise _refuse("invalid_status", "status is 'approved' or 'suggested'.", change)
        value, json_value = _validate_value(spec, change)
        provenance, source = _validate_source(db, spec, change, value)
        rows.append(
            AssumptionRevision(
                scenario_id=scenario.id,
                scenario_revision_no=new_rev,
                key=change.key,
                period_index=change.period_index,
                value=value,
                value_json=json_value,
                unit=spec.unit,
                provenance_kind=provenance,
                source=source,
                status=change.status,
                note=change.note,
                reason=reason,
                created_by=actor.user_id,
            )
        )
    db.add_all(rows)
    scenario.revision_no = new_rev
    scenario.updated_at = datetime.now(UTC)
    db.flush()
    audit.record(
        db,
        action="assumptions_revised",
        entity_type=ENTITY,
        entity_id=str(scenario.id),
        user_id=actor.user_id,
        before={
            "scenario_revision": new_rev - 1,
            "items": [
                {"key": r.key, "period_index": r.period_index, "previous": _previous(before, r)}
                for r in rows
            ],
        },
        after={
            "scenario_revision": new_rev,
            "project_id": str(project.id),
            "items": [
                {"key": r.key, "period_index": r.period_index, **(_snap(r) or {})} for r in rows
            ],
            **access.audit_fields(),
        },
        reason=reason or access.admin_reason,
    )
    return current_state(db, scenario)


def approve(
    db: Session,
    actor: Actor,
    scenario_id: uuid.UUID,
    *,
    expected_revision: int,
    items: list[tuple[str, int | None]],
    reason: str | None = None,
    admin_reason: str | None = None,
) -> State:
    """Approve current ``suggested`` values unchanged (a new ``approved`` revision each)."""
    scenario, project, access = _scenario_for_write(db, actor, scenario_id, admin_reason)
    check_revision(scenario.revision_no, expected_revision, "scenario")
    if not 1 <= len(items) <= MAX_CHANGES or len(set(items)) != len(items):
        raise ValuationRefused("invalid_request", "Name 1–400 distinct key/period items.")
    state = current_state(db, scenario)
    new_rev = scenario.revision_no + 1
    rows: list[AssumptionRevision] = []
    approved_of: list[int] = []
    for key, period in items:
        cur = state.get(key, period)
        if cur is None or cur.row.status != "suggested":
            raise ValuationRefused(
                "not_suggested",
                f"{key}{'' if period is None else f'[{period}]'} has no suggested value.",
                [{"key": key, "period_index": period}],
            )
        old = cur.row
        approved_of.append(old.id)
        rows.append(
            AssumptionRevision(
                scenario_id=scenario.id,
                scenario_revision_no=new_rev,
                key=key,
                period_index=period,
                value=old.value,
                value_json=old.value_json,
                unit=old.unit,
                provenance_kind=old.provenance_kind,
                source=old.source,
                status="approved",
                note=old.note,
                reason=reason,
                created_by=actor.user_id,
            )
        )
    db.add_all(rows)
    scenario.revision_no = new_rev
    scenario.updated_at = datetime.now(UTC)
    db.flush()
    audit.record(
        db,
        action="assumptions_approved",
        entity_type=ENTITY,
        entity_id=str(scenario.id),
        user_id=actor.user_id,
        before={"scenario_revision": new_rev - 1},
        after={
            "scenario_revision": new_rev,
            "project_id": str(project.id),
            "items": [
                {
                    "key": r.key,
                    "period_index": r.period_index,
                    "approved_revision_of": old_id,
                }
                for r, old_id in zip(rows, approved_of, strict=True)
            ],
            **access.audit_fields(),
        },
        reason=reason or access.admin_reason,
    )
    return current_state(db, scenario)


def required_keys(state: State, methodology: str, block: Block) -> list[tuple[str, int | None]]:
    """Every (key, period) the run needs, given the block and the current settings."""
    need: list[tuple[str, int | None]] = []
    per_year = set(PER_YEAR_DRIVERS)
    if methodology == "dcf_fcfe":
        per_year |= {"debt_forecast", "cash_forecast"}
    horizon_raw = state.value("horizon_years")
    horizon = int(horizon_raw) if isinstance(horizon_raw, Decimal) else 0
    for key in block.required_assumptions:
        if key in per_year:
            need.extend((key, k) for k in range(1, horizon + 1))
        else:
            need.append((key, None))

    def when(key: str, value: Any, *extra: str) -> None:
        if state.value(key) == value:
            need.extend((e, None) for e in extra)

    when("terminal_form", "value_driver", "ronic")
    when("terminal_form", "exit_multiple", "exit_multiple", "exit_metric")
    when("erp_basis", "mature_plus_crp", "mature_market_erp")
    when("erp_basis", "total_country_erp", "total_country_erp")
    when("beta_method", "analyst_levered", "beta_levered")
    when("beta_method", "bottom_up", "beta_unlevered", "relevering", "target_debt_to_equity")
    when("cost_of_debt_method", "direct", "pre_tax_cost_of_debt")
    when("cost_of_debt_method", "rf_plus_spread", "credit_spread")
    if methodology == "dcf_fcff":
        when("capital_structure_basis", "target", "target_debt_to_equity")
    if state.get("country_risk_premium") is not None:
        need.extend([("crp_method", None), ("crp_country", None)])
        when("crp_method", "lambda", "crp_lambda")
    sp = state.value("specific_premium")
    if isinstance(sp, Decimal) and sp != 0:
        need.append(("specific_premium_rationale", None))
    seen: set[tuple[str, int | None]] = set()
    return [k for k in need if not (k in seen or seen.add(k))]  # type: ignore[func-returns-value]


def applicable(key: str, methodology: str) -> bool:
    spec = REGISTRY.get(key)
    return spec is not None and methodology in spec.methodologies


def scenario_for_project(
    db: Session, project: ValuationProject, scenario_id: uuid.UUID
) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None or scenario.project_id != project.id:
        from erp_backend.errors import ApiError

        raise ApiError(404, "not_found", f"No scenario {scenario_id} in this project.")
    return scenario
