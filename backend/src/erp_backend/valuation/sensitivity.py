"""Sensitivity grids over a stored run (Phase 5b; design §9.5; MASTER_BRIEF §11.3).

Every cell is a full engine execution (``compute``) of the run's **stored** input document with
one or two inputs replaced, so a grid never reads current data and is as reproducible as the
run. Cells that the engine refuses or whose validation blocks a save (e.g. ``g ≥ WACC``) are
stored as refused with their code, never as a number.

Kinds: ``one_way`` (x), ``two_way`` (x × y) and ``tornado`` (low/high per variable). At most
``MAX_CELLS`` cells, computed synchronously; larger grids are refused (``grid_too_large``) until
a job queue is approved. An identical configuration on the same run and engine version returns
the stored result (content idempotency).

Variables (absolute values unless ``_shift``; shifts are added to every forecast year):

* ``wacc`` (FCFF) / ``cost_of_equity`` (FCFE) — the discount rate; RONIC and the roll-forward
  cost of equity keep the run's values;
* ``terminal_g`` — terminal growth;
* ``exit_multiple`` — runs whose terminal form is ``exit_multiple`` only;
* ``revenue_growth_shift``, ``ebit_margin_shift``, ``capex_pct_shift`` — driver shifts.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from erp_engine.core.numbers import make_context
from erp_engine.version import ENGINE_VERSION
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.models import SensitivityRun, ValuationRun
from erp_backend.valuation import projects
from erp_backend.valuation.canonical import jsonable, sha256_of
from erp_backend.valuation.common import Actor, require_active, write_access
from erp_backend.valuation.compute import compute
from erp_backend.valuation.errors import ValuationRefused

ENTITY = "valuation_sensitivity"
MAX_CELLS = 100
MAX_AXIS = 25
MAX_TORNADO = 10
D = Decimal


@dataclass(frozen=True)
class Variable:
    name: str
    label: str
    low: Decimal
    high: Decimal
    shift: bool
    methodologies: tuple[str, ...] = ("dcf_fcff", "dcf_fcfe")
    driver: str | None = None  # per-year assumption key for shifts


VARIABLES: dict[str, Variable] = {
    v.name: v
    for v in (
        Variable("wacc", "WACC", D("0.0001"), D("1"), False, ("dcf_fcff",)),
        Variable("cost_of_equity", "Cost of equity", D("0.0001"), D("1"), False, ("dcf_fcfe",)),
        Variable("terminal_g", "Terminal growth", D("-0.1"), D("0.2"), False),
        Variable("exit_multiple", "Exit multiple", D("0.0001"), D("200"), False),
        Variable(
            "revenue_growth_shift",
            "Revenue growth (shift)",
            D("-1"),
            D("1"),
            True,
            driver="revenue_growth",
        ),
        Variable(
            "ebit_margin_shift",
            "EBIT margin (shift)",
            D("-1"),
            D("1"),
            True,
            driver="ebit_margin",
        ),
        Variable(
            "capex_pct_shift",
            "Capex % revenue (shift)",
            D("-1"),
            D("1"),
            True,
            driver="capex_pct",
        ),
    )
}
METRICS = {
    "per_share": ("per_share", ("dcf_fcff", "dcf_fcfe")),
    "equity_value": ("equity_value_v", ("dcf_fcff", "dcf_fcfe")),
    "enterprise_value": ("primary", ("dcf_fcff",)),
}


@dataclass(frozen=True)
class Axis:
    variable: str
    values: tuple[Decimal, ...]


@dataclass(frozen=True)
class TornadoItem:
    variable: str
    low: Decimal
    high: Decimal


@dataclass(frozen=True)
class SensitivityRequest:
    kind: str  # one_way | two_way | tornado
    metric: str
    x: Axis | None = None
    y: Axis | None = None
    tornado: tuple[TornadoItem, ...] = ()
    label: str | None = None


def _refuse(code: str, message: str, **detail: Any) -> ValuationRefused:
    return ValuationRefused(code, message, [detail] if detail else [])


def _dec(raw: str) -> Decimal:
    try:
        value = D(raw)
    except (InvalidOperation, TypeError) as exc:
        raise _refuse("invalid_value", f"{raw!r} is not a decimal.") from exc
    if not value.is_finite():
        raise _refuse("invalid_value", f"{raw!r} is not finite.")
    return value


def _variable(name: str, methodology: str, document: dict[str, Any]) -> Variable:
    var = VARIABLES.get(name)
    if var is None:
        raise _refuse("unknown_variable", f"Unknown sensitivity variable {name!r}.", variable=name)
    if methodology not in var.methodologies:
        raise _refuse(
            "variable_not_applicable",
            f"{var.label} does not apply to {methodology} runs.",
            variable=name,
        )
    if name == "exit_multiple":
        form = document["inputs"].get("assumption:terminal_form", {}).get("json")
        if form != "exit_multiple":
            raise _refuse(
                "variable_not_applicable",
                "The run's terminal value is not an exit multiple.",
                variable=name,
            )
    return var


def _check_value(var: Variable, value: Decimal) -> None:
    if not var.low <= value <= var.high:
        raise _refuse(
            "out_of_range",
            f"{var.label} values must be between {var.low} and {var.high}.",
            variable=var.name,
            value=str(value),
        )


def _check_axis(axis: Axis, methodology: str, document: dict[str, Any]) -> Variable:
    var = _variable(axis.variable, methodology, document)
    if not 1 <= len(axis.values) <= MAX_AXIS:
        raise _refuse("invalid_request", f"Each axis has 1–{MAX_AXIS} values.", variable=var.name)
    if len(set(axis.values)) != len(axis.values):
        raise _refuse("invalid_request", "Axis values must be distinct.", variable=var.name)
    for v in axis.values:
        _check_value(var, v)
    return var


def _apply(document: dict[str, Any], var: Variable, value: Decimal) -> Decimal | None:
    """Replace one input in a copied document; returns a discount-rate override, if any."""
    inputs = document["inputs"]
    if var.name in ("wacc", "cost_of_equity"):
        return value
    if var.name == "terminal_g":
        inputs["assumption:terminal_growth"]["value"] = str(value)
    elif var.name == "exit_multiple":
        inputs["assumption:exit_multiple"]["value"] = str(value)
    else:
        assert var.driver is not None
        horizon = int(D(inputs["assumption:horizon_years"]["value"]))
        with localcontext(make_context()):
            for k in range(1, horizon + 1):
                item = inputs[f"assumption:{var.driver}[{k}]"]
                item["value"] = str(D(item["value"]) + value)
    return None


def _base_value(document: dict[str, Any], outputs: dict[str, Any], var: Variable) -> Decimal:
    if var.name == "wacc":
        return D(outputs["cost_of_capital"]["wacc"])
    if var.name == "cost_of_equity":
        return D(outputs["cost_of_capital"]["cost_of_equity"])
    if var.name == "terminal_g":
        return D(document["inputs"]["assumption:terminal_growth"]["value"])
    if var.name == "exit_multiple":
        return D(document["inputs"]["assumption:exit_multiple"]["value"])
    return D(0)


def _metric(outputs: dict[str, Any], metric: str, per_share: Decimal, primary: Decimal) -> str:
    """The metric at full engine precision: per-share and primary values come from the
    computation (as the run's ``per_share_value`` / ``primary_value`` columns do)."""
    if metric == "per_share":
        return str(per_share)
    if metric == "enterprise_value":
        return str(primary)
    return str(outputs[METRICS[metric][0]])


def _cell(
    run: ValuationRun, metric: str, changes: list[tuple[Variable, Decimal]]
) -> dict[str, Any]:
    document = copy.deepcopy(run.input_document)
    rate: Decimal | None = None
    for var, value in changes:
        override = _apply(document, var, value)
        rate = override if override is not None else rate
    try:
        comp = compute(document, rate)
    except ValuationRefused as exc:
        detail = exc.details[0] if exc.details else {}
        return {
            "value": None,
            "refused_code": detail.get("engine_code") or exc.code,
            "message": exc.message,
        }
    if comp.blocks_save:
        first = comp.blocks_save[0]
        return {"value": None, "refused_code": first["code"], "message": first["message"]}
    return {
        "value": _metric(comp.outputs, metric, comp.per_share_value, comp.primary_value),
        "refused_code": None,
        "message": None,
    }


def _config(req: SensitivityRequest) -> dict[str, Any]:
    def axis(a: Axis | None) -> dict[str, Any] | None:
        return None if a is None else {"variable": a.variable, "values": [str(v) for v in a.values]}

    config: dict[str, Any] = jsonable(
        {
            "kind": req.kind,
            "metric": req.metric,
            "x": axis(req.x),
            "y": axis(req.y),
            "tornado": [
                {"variable": t.variable, "low": str(t.low), "high": str(t.high)}
                for t in req.tornado
            ],
        }
    )
    return config


def _plan(
    run: ValuationRun, req: SensitivityRequest
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the request; return the cell plan (coordinates + changes) and the base point."""
    doc, method = run.input_document, run.methodology
    if req.metric not in METRICS:
        raise _refuse("invalid_request", f"Unknown metric {req.metric!r}.")
    if method not in METRICS[req.metric][1]:
        raise _refuse("variable_not_applicable", f"{req.metric} is not an output of {method} runs.")
    base: dict[str, Any] = {
        "value": _metric(run.outputs, req.metric, run.per_share_value, run.primary_value)
    }
    plan: list[dict[str, Any]] = []
    if req.kind in ("one_way", "two_way"):
        if req.x is None or (req.kind == "two_way") != (req.y is not None) or req.tornado:
            raise _refuse("invalid_request", "one_way needs x; two_way needs x and y.")
        xv = _check_axis(req.x, method, doc)
        base["x"] = str(_base_value(doc, run.outputs, xv))
        if req.y is None:
            plan = [{"x": str(x), "changes": [(xv, x)]} for x in req.x.values]
        else:
            yv = _check_axis(req.y, method, doc)
            if yv.name == xv.name:
                raise _refuse("invalid_request", "x and y must be different variables.")
            if {xv.name, yv.name} == {"wacc", "cost_of_equity"}:  # pragma: no cover - by method
                raise _refuse("invalid_request", "Only one discount-rate variable per grid.")
            base["y"] = str(_base_value(doc, run.outputs, yv))
            if len(req.x.values) * len(req.y.values) > MAX_CELLS:
                raise _refuse(
                    "grid_too_large",
                    f"At most {MAX_CELLS} cells are computed synchronously.",
                    cells=len(req.x.values) * len(req.y.values),
                )
            plan = [
                {"x": str(x), "y": str(y), "changes": [(xv, x), (yv, y)]}
                for y in req.y.values
                for x in req.x.values
            ]
    elif req.kind == "tornado":
        if not 1 <= len(req.tornado) <= MAX_TORNADO or req.x is not None or req.y is not None:
            raise _refuse("invalid_request", f"A tornado names 1–{MAX_TORNADO} variables.")
        names = [t.variable for t in req.tornado]
        if len(set(names)) != len(names):
            raise _refuse("invalid_request", "Each tornado variable may appear once.")
        base["variables"] = {}
        for t in req.tornado:
            var = _variable(t.variable, method, doc)
            _check_value(var, t.low)
            _check_value(var, t.high)
            if t.low >= t.high:
                raise _refuse("invalid_request", "low must be below high.", variable=var.name)
            base["variables"][var.name] = str(_base_value(doc, run.outputs, var))
            plan += [
                {"variable": var.name, "side": "low", "x": str(t.low), "changes": [(var, t.low)]},
                {
                    "variable": var.name,
                    "side": "high",
                    "x": str(t.high),
                    "changes": [(var, t.high)],
                },
            ]
    else:
        raise _refuse("invalid_request", f"Unknown kind {req.kind!r}.")
    if len(plan) > MAX_CELLS:
        raise _refuse("grid_too_large", f"At most {MAX_CELLS} cells.", cells=len(plan))
    return plan, base


def _tornado_ranking(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per variable: low/high values, swing |high − low| and its share of the largest swing,
    largest first (refused sides give no swing and sort last). Computed here so the UI only
    renders (D25)."""
    by: dict[str, dict[str, Any]] = {}
    for c in cells:
        by.setdefault(c["variable"], {})[c["side"]] = c["value"]
    swings: dict[str, Decimal | None] = {}
    with localcontext(make_context()):
        for v, r in by.items():
            lo, hi = r.get("low"), r.get("high")
            swings[v] = abs(D(hi) - D(lo)) if lo is not None and hi is not None else None
        known = [w for w in swings.values() if w is not None]
        largest = max(known) if known else None
        shares = {
            v: (str(w / largest) if w is not None and largest else None) for v, w in swings.items()
        }
    order = sorted(by, key=lambda v: (swings[v] is None, -(swings[v] or D(0))))
    return [
        {
            "variable": v,
            "low": by[v].get("low"),
            "high": by[v].get("high"),
            "swing": None if swings[v] is None else str(swings[v]),
            "share": shares[v],
        }
        for v in order
    ]


def create(
    db: Session,
    actor: Actor,
    run_id: uuid.UUID,
    req: SensitivityRequest,
    *,
    admin_reason: str | None = None,
    action_id: uuid.UUID | None = None,
) -> tuple[SensitivityRun, bool]:
    """Compute and store a grid (or return the identical stored one). Returns (row, existing)."""
    run = db.get(ValuationRun, run_id)
    if run is None:
        from erp_backend.errors import ApiError

        raise ApiError(404, "not_found", f"No valuation run {run_id}.")
    project = projects.get_project(db, run.project_id, lock=True)
    access = write_access(project, actor, admin_reason)
    require_active(project)
    if run.engine_version != ENGINE_VERSION:
        raise _refuse(
            "engine_version_mismatch",
            f"The run was computed with engine {run.engine_version}; this server runs "
            f"{ENGINE_VERSION}. Create a new run first.",
        )
    plan, base = _plan(run, req)
    config = _config(req)
    config_sha = sha256_of(config)
    existing = db.scalars(
        select(SensitivityRun).where(
            SensitivityRun.run_id == run.id,
            SensitivityRun.config_sha256 == config_sha,
            SensitivityRun.engine_version == ENGINE_VERSION,
        )
    ).first()
    if existing is not None:
        return existing, True
    cells = []
    for p in plan:
        coords = {k: v for k, v in p.items() if k != "changes"}
        cells.append({**coords, **_cell(run, req.metric, p["changes"])})
    refused = sum(1 for c in cells if c["refused_code"] is not None)
    if req.kind == "tornado":
        base["ranking"] = _tornado_ranking(cells)
    result_sha = sha256_of({"config": config, "base": base, "cells": cells})
    row = SensitivityRun(
        id=uuid.uuid4(),
        run_id=run.id,
        project_id=project.id,
        kind=req.kind,
        metric=req.metric,
        config=config,
        config_sha256=config_sha,
        base=base,
        cells=cells,
        cell_count=len(cells),
        refused_count=refused,
        result_sha256=result_sha,
        engine_version=ENGINE_VERSION,
        label=(req.label or "").strip() or None,
        action_id=action_id,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="sensitivity_created",
        entity_type=ENTITY,
        entity_id=str(row.id),
        user_id=actor.user_id,
        after={
            "run_id": str(run.id),
            "project_id": str(project.id),
            "kind": req.kind,
            "metric": req.metric,
            "config_sha256": config_sha,
            "result_sha256": result_sha,
            "cells": len(cells),
            "refused": refused,
            "engine_version": ENGINE_VERSION,
            **access.audit_fields(),
        },
        reason=access.admin_reason,
    )
    return row, False


def variables() -> list[dict[str, Any]]:
    return [
        {
            "variable": v.name,
            "label": v.label,
            "low": str(v.low),
            "high": str(v.high),
            "shift": v.shift,
            "methodologies": list(v.methodologies),
        }
        for v in VARIABLES.values()
    ]
