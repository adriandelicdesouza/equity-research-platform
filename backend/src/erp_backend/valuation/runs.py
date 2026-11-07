"""Previews, immutable runs and reproduction (design §4; Q4, Q7, Q8, Q11, Q12; approved
qualifications 1–4).

* **Preview** — resolve + compute on the caller's (read-only) session; nothing is stored. A
  draft dataset and unapproved assumptions are allowed and reported as gates.
* **Run** — the same resolution under a lock on the project row; any error or gate refuses the
  run. The run row, its trace, its input lineage, its decisions (acknowledgements, check and
  input overrides, administrator override — each with user, time, reason and run) and the
  audit event are written in the caller's transaction (``workflow.perform`` commits them with
  the action record).
* **Content idempotency (Q8)** — the identity is the SHA-256 of the canonical input document,
  which contains the project, scenario and its revision, methodology and method version,
  dataset identity, every input with its provenance, settings, acknowledgements, overrides and
  the engine version. An identical document in the same project returns the existing run.
* **Reproduction** — re-computes from the stored document only and compares output hashes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from erp_engine.version import ENGINE_VERSION, MODULE_VERSIONS
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.errors import ApiError
from erp_backend.models import (
    RunDecision,
    RunInput,
    RunTrace,
    ValuationProject,
    ValuationRun,
)
from erp_backend.observability import current_request_id
from erp_backend.valuation import projects
from erp_backend.valuation.canonical import sha256_of
from erp_backend.valuation.common import Actor, require_active, write_access
from erp_backend.valuation.compute import METHODS, Computation, compute
from erp_backend.valuation.errors import ValuationRefused
from erp_backend.valuation.resolver import Resolution, RunRequest, resolve

ENTITY = "valuation_run"


@dataclass(frozen=True)
class Preview:
    resolution: Resolution
    computation: Computation | None
    refusal: dict[str, Any] | None  # engine refusal, if the inputs were complete


@dataclass(frozen=True)
class RunResult:
    run: ValuationRun
    existing: bool


def preview(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    request: RunRequest,
    admin_reason: str | None = None,
) -> Preview:
    project = projects.get_project(db, project_id)
    write_access(project, actor, admin_reason)
    require_active(project)
    res = resolve(db, project, request)
    if res.document is None:
        return Preview(res, None, None)
    try:
        return Preview(res, compute(res.document), None)
    except ValuationRefused as exc:
        return Preview(
            res, None, {"code": exc.code, "message": exc.message, "details": exc.details}
        )


def _input_rows(run_id: uuid.UUID, document: dict[str, Any]) -> list[RunInput]:
    rows = []
    for key, item in sorted(document["inputs"].items()):
        value = item.get("value")
        rows.append(
            RunInput(
                run_id=run_id,
                input_key=key[:96],
                value=Decimal(value) if value is not None else None,
                value_json=item.get("json"),
                unit=item.get("unit", "number"),
                source_value=(
                    Decimal(item["source_value"]) if item.get("source_value") is not None else None
                ),
                source_unit=item.get("source_unit"),
                conversion=item.get("conversion"),
                provenance_kind=item.get("provenance_kind", "unknown"),
                source_type=item.get("source_type", "unknown"),
                source_ref=item.get("source_ref"),
                source_sha256=item.get("source_sha256"),
                as_of_date=date.fromisoformat(item["as_of"]) if item.get("as_of") else None,
                warnings=item.get("warnings", []),
            )
        )
    return rows


def _decisions(
    run_id: uuid.UUID, document: dict[str, Any], actor: Actor, admin_reason: str | None
) -> list[RunDecision]:
    out = [
        RunDecision(
            run_id=run_id,
            kind="classification_acknowledgement",
            code=a["code"],
            reason=a["reason"],
            detail={"classification": document["classification"]},
            user_id=actor.user_id,
        )
        for a in document["acknowledgements"]
    ]
    out += [
        RunDecision(
            run_id=run_id,
            kind="check_override",
            code=c["check_code"],
            reason=c["reason"],
            detail={
                "subject": c["subject"],
                "message": c["message"],
                "dataset_id": document["dataset"]["id"],
            },
            user_id=actor.user_id,
        )
        for c in document["check_overrides"]
    ]
    out += [
        RunDecision(
            run_id=run_id,
            kind="input_override",
            code=f"{o['input']}{'/' + o['line_item'] if o['line_item'] else ''}",
            reason=o["reason"],
            detail=o,
            user_id=actor.user_id,
        )
        for o in document["input_overrides"]
    ]
    if admin_reason is not None:
        out.append(
            RunDecision(
                run_id=run_id,
                kind="admin_override",
                code="project_owner",
                reason=admin_reason,
                detail={"project_id": document["project_id"]},
                user_id=actor.user_id,
            )
        )
    return out


def create_run(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    request: RunRequest,
    *,
    label: str | None = None,
    admin_reason: str | None = None,
    action_id: uuid.UUID | None = None,
) -> RunResult:
    project = projects.get_project(db, project_id, lock=True)
    access = write_access(project, actor, admin_reason)
    require_active(project)
    res = resolve(db, project, request)
    if res.blocking:
        raise res.refusal()
    assert res.document is not None and res.dataset is not None and res.scenario is not None
    document = res.document
    input_sha = sha256_of(document)
    existing = db.scalars(
        select(ValuationRun).where(
            ValuationRun.project_id == project.id,
            ValuationRun.input_sha256 == input_sha,
            ValuationRun.engine_version == ENGINE_VERSION,
        )
    ).first()
    if existing is not None:
        return RunResult(existing, True)
    comp = compute(document)
    if comp.blocks_save:
        first = comp.blocks_save[0]
        raise ValuationRefused("validation_blocked", first["message"], comp.blocks_save)
    run_id = uuid.uuid4()
    run = ValuationRun(
        id=run_id,
        project_id=project.id,
        scenario_id=res.scenario.id,
        scenario_revision_no=res.scenario.revision_no,
        company_id=project.company_id,
        listing_id=uuid.UUID(document["listing_id"]) if document["listing_id"] else None,
        methodology=request.methodology,
        method_version=METHODS[request.methodology]["version"],
        produces=METHODS[request.methodology]["produces"],
        dataset_id=res.dataset.id,
        dataset_version_no=res.dataset.version_no,
        dataset_basis=res.dataset.basis,
        dataset_content_sha256=res.dataset.content_sha256,
        block_id=res.block_id,
        classification=document["classification"],
        valuation_date=request.valuation_date,
        balance_sheet_date=date.fromisoformat(document["balance_sheet_period"]["end_date"]),
        currency=document["currency"],
        input_document=document,
        input_sha256=input_sha,
        engine_input_sha256=comp.engine_input_sha256,
        engine_version=ENGINE_VERSION,
        module_versions=dict(MODULE_VERSIONS),
        outputs=comp.outputs,
        primary_value=comp.primary_value,
        per_share_value=comp.per_share_value,
        validation=comp.validation,
        warnings=document["warnings"],
        output_sha256=comp.output_sha256,
        label=(label or "").strip() or None,
        action_id=action_id,
        request_id=current_request_id(),
        created_by=actor.user_id,
    )
    db.add(run)
    db.flush()
    db.add(
        RunTrace(
            run_id=run_id,
            trace=comp.trace,
            trace_sha256=sha256_of(comp.trace),
            node_count=len(comp.trace),
        )
    )
    db.add_all(_input_rows(run_id, document))
    db.add_all(_decisions(run_id, document, actor, access.admin_reason))
    db.flush()
    audit.record(
        db,
        action="run_created",
        entity_type=ENTITY,
        entity_id=str(run_id),
        user_id=actor.user_id,
        after={
            "project_id": str(project.id),
            "scenario_id": str(res.scenario.id),
            "scenario_revision": res.scenario.revision_no,
            "methodology": request.methodology,
            "dataset_id": str(res.dataset.id),
            "dataset_version_no": res.dataset.version_no,
            "valuation_date": request.valuation_date.isoformat(),
            "input_sha256": input_sha,
            "output_sha256": comp.output_sha256,
            "engine_version": ENGINE_VERSION,
            "primary_value": str(comp.primary_value),
            "per_share_value": str(comp.per_share_value),
            "acknowledgements": [a["code"] for a in document["acknowledgements"]],
            "check_overrides": [c["check_code"] for c in document["check_overrides"]],
            "input_overrides": len(document["input_overrides"]),
            "warnings": [w["code"] for w in document["warnings"]],
            **access.audit_fields(),
        },
        reason=access.admin_reason,
    )
    return RunResult(run, False)


@dataclass(frozen=True)
class Reproduction:
    matches: bool
    same_engine: bool
    stored_output_sha256: str
    recomputed_output_sha256: str | None
    stored_engine: dict[str, Any]
    current_engine: dict[str, Any]
    differences: list[dict[str, Any]]
    refusal: dict[str, Any] | None


def _diff(
    a: Any, b: Any, path: str = "", out: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    out = [] if out is None else out
    if len(out) >= 50:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k, out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append({"path": path, "stored": a, "recomputed": b})
    return out


def reproduce(run: ValuationRun) -> Reproduction:
    """Re-execute a run from its stored input document only (no database reads)."""
    stored_engine = {"version": run.engine_version, "modules": run.module_versions}
    current = {"version": ENGINE_VERSION, "modules": dict(MODULE_VERSIONS)}
    same = stored_engine == current
    try:
        comp = compute(run.input_document)
    except ValuationRefused as exc:
        return Reproduction(
            False,
            same,
            run.output_sha256,
            None,
            stored_engine,
            current,
            [],
            {"code": exc.code, "message": exc.message},
        )
    matches = comp.output_sha256 == run.output_sha256
    differences = [] if matches else _diff(run.outputs, comp.outputs)
    return Reproduction(
        matches,
        same,
        run.output_sha256,
        comp.output_sha256,
        stored_engine,
        current,
        differences,
        None,
    )


def get_run(db: Session, run_id: uuid.UUID) -> ValuationRun:
    run = db.get(ValuationRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", f"No valuation run {run_id}.")
    return run


def project_of(db: Session, run: ValuationRun) -> ValuationProject:
    project = db.get(ValuationProject, run.project_id)
    assert project is not None
    return project
