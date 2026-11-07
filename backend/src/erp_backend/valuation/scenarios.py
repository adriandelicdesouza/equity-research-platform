"""Scenarios: bull/bear/custom with inheritance, archive/restore and probability weights
(Phase 5b; design §9.3, Q9; MASTER_BRIEF §11.1, §14.1).

* A project has exactly one ``base`` scenario (created with the project, never archived, no
  parent). Other scenarios are ``bull``, ``bear`` or ``custom`` and may inherit from one parent
  in the same project; inherited values apply unless the child sets or clears the key
  (``assumptions.resolved_state``).
* The inheritance graph is acyclic and at most ``MAX_DEPTH`` deep; changing a parent re-checks
  both. A scenario with active children, or with a probability, cannot be archived.
* Probabilities are project-level: one atomic request sets the weights of the named active
  scenarios (each 0–1, exact sum 1) and clears the others; an empty request clears all.
* Every change is audited (entity ``valuation_scenario``; ``probabilities_set`` on the project)
  and runs through ``workflow.perform``. A scenario header change bumps the scenario revision,
  because a parent change alters the values its runs resolve.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.errors import ApiError
from erp_backend.models import Scenario, ValuationProject
from erp_backend.valuation import projects
from erp_backend.valuation.assumptions import MAX_DEPTH, ancestors, require_active_scenario
from erp_backend.valuation.common import (
    Actor,
    WriteAccess,
    check_revision,
    meaningful,
    require_active,
    write_access,
)
from erp_backend.valuation.errors import ValuationRefused

ENTITY = "valuation_scenario"
KINDS = ("bull", "bear", "custom")
MAX_SCENARIOS = 20
ONE = Decimal(1)


def snapshot(s: Scenario) -> dict[str, Any]:
    return {
        "name": s.name,
        "kind": s.kind,
        "parent_scenario_id": str(s.parent_scenario_id) if s.parent_scenario_id else None,
        "probability": str(s.probability) if s.probability is not None else None,
        "sort_order": s.sort_order,
        "status": s.status,
        "revision_no": s.revision_no,
    }


def _clean_name(name: str) -> str:
    text = name.strip()
    if not 1 <= len(text) <= 80:
        raise ValuationRefused("invalid_name", "A scenario name needs 1–80 characters.")
    return text


def _name_taken(db: Session, project_id: uuid.UUID, name: str, exclude: uuid.UUID | None) -> bool:
    stmt = select(Scenario.id).where(
        Scenario.project_id == project_id, func.lower(Scenario.name) == name.lower()
    )
    if exclude is not None:
        stmt = stmt.where(Scenario.id != exclude)
    return db.scalar(stmt) is not None


def _scenario(db: Session, scenario_id: uuid.UUID, *, lock: bool = False) -> Scenario:
    stmt = select(Scenario).where(Scenario.id == scenario_id)
    if lock:
        stmt = stmt.with_for_update()
    scenario = db.scalars(stmt).first()
    if scenario is None:
        raise ApiError(404, "not_found", f"No scenario {scenario_id}.")
    return scenario


def _project_for_write(
    db: Session, actor: Actor, project_id: uuid.UUID, admin_reason: str | None
) -> tuple[ValuationProject, WriteAccess]:
    project = projects.get_project(db, project_id, lock=True)
    access = write_access(project, actor, admin_reason)
    require_active(project)
    return project, access


def _check_parent(
    db: Session, project_id: uuid.UUID, parent_id: uuid.UUID, child_id: uuid.UUID | None
) -> Scenario:
    """The parent must be an active scenario of the same project; the resulting chain must be
    acyclic and at most ``MAX_DEPTH`` deep (counting the child)."""
    parent = db.get(Scenario, parent_id)
    if parent is None or parent.project_id != project_id:
        raise ValuationRefused(
            "invalid_parent", "The parent must be a scenario of the same project."
        )
    if parent.status != "active":
        raise ValuationRefused("invalid_parent", "The parent scenario is archived.")
    chain = [parent, *ancestors(db, parent)]
    if child_id is not None and any(s.id == child_id for s in chain):
        raise ValuationRefused(
            "scenario_cycle",
            "That parent would make the scenario inherit from itself.",
            [{"parent_scenario_id": str(parent_id)}],
        )
    if len(chain) + 1 > MAX_DEPTH:
        raise ValuationRefused(
            "scenario_cycle", f"Inheritance is limited to {MAX_DEPTH} levels.", []
        )
    return parent


def _children(db: Session, scenario_id: uuid.UUID) -> list[Scenario]:
    return list(
        db.scalars(
            select(Scenario).where(
                Scenario.parent_scenario_id == scenario_id, Scenario.status == "active"
            )
        ).all()
    )


def _audit(
    db: Session,
    scenario: Scenario,
    actor: Actor,
    action: str,
    before: dict[str, Any] | None,
    access: WriteAccess,
    reason: str | None = None,
) -> None:
    db.flush()
    audit.record(
        db,
        action=action,
        entity_type=ENTITY,
        entity_id=str(scenario.id),
        user_id=actor.user_id,
        before=before,
        after={
            **snapshot(scenario),
            "project_id": str(scenario.project_id),
            **access.audit_fields(),
        },
        reason=reason or access.admin_reason,
    )


def create_scenario(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    name: str,
    kind: str,
    parent_scenario_id: uuid.UUID | None = None,
    admin_reason: str | None = None,
) -> Scenario:
    project, access = _project_for_write(db, actor, project_id, admin_reason)
    if kind not in KINDS:
        raise ValuationRefused(
            "invalid_kind", "A new scenario is bull, bear or custom (the base exists).", []
        )
    clean = _clean_name(name)
    if _name_taken(db, project.id, clean, None):
        raise ValuationRefused("duplicate_name", f"This project already has a scenario {clean!r}.")
    count = db.scalar(select(func.count()).where(Scenario.project_id == project.id)) or 0
    if count >= MAX_SCENARIOS:
        raise ValuationRefused(
            "too_many_scenarios", f"A project has at most {MAX_SCENARIOS} scenarios.", []
        )
    if parent_scenario_id is not None:
        _check_parent(db, project.id, parent_scenario_id, None)
    order = db.scalar(
        select(func.coalesce(func.max(Scenario.sort_order), 0)).where(
            Scenario.project_id == project.id
        )
    )
    scenario = Scenario(
        id=uuid.uuid4(),
        project_id=project.id,
        name=clean,
        kind=kind,
        parent_scenario_id=parent_scenario_id,
        sort_order=int(order or 0) + 1,
        revision_no=0,
        status="active",
        created_by=actor.user_id,
    )
    db.add(scenario)
    _audit(db, scenario, actor, "scenario_created", None, access)
    return scenario


def update_scenario(
    db: Session,
    actor: Actor,
    scenario_id: uuid.UUID,
    *,
    expected_revision: int,
    changes: dict[str, Any],
    reason: str | None = None,
    admin_reason: str | None = None,
) -> Scenario:
    """Rename, reorder or change the parent (``parent_scenario_id`` None = no parent)."""
    scenario = _scenario(db, scenario_id, lock=True)
    _, access = _project_for_write(db, actor, scenario.project_id, admin_reason)
    require_active_scenario(scenario)
    check_revision(scenario.revision_no, expected_revision, "scenario")
    if not changes:
        raise ValuationRefused("invalid_request", "Nothing to change.", [])
    before = snapshot(scenario)
    if "name" in changes:
        clean = _clean_name(changes["name"])
        if _name_taken(db, scenario.project_id, clean, scenario.id):
            raise ValuationRefused("duplicate_name", f"Another scenario is named {clean!r}.")
        scenario.name = clean
    if "sort_order" in changes:
        order = int(changes["sort_order"])
        if not 0 <= order <= 1000:
            raise ValuationRefused("invalid_request", "sort_order must be 0–1000.", [])
        scenario.sort_order = order
    if "parent_scenario_id" in changes:
        parent_id = changes["parent_scenario_id"]
        if scenario.kind == "base" and parent_id is not None:
            raise ValuationRefused("invalid_parent", "The base scenario cannot inherit.", [])
        if parent_id == scenario.parent_scenario_id:
            raise ValuationRefused("parent_unchanged", "The scenario already has that parent.")
        if parent_id is not None:
            _check_parent(db, scenario.project_id, parent_id, scenario.id)
        scenario.parent_scenario_id = parent_id
    scenario.revision_no += 1
    scenario.updated_at = datetime.now(UTC)
    _audit(db, scenario, actor, "scenario_updated", before, access, reason)
    return scenario


def set_scenario_status(
    db: Session,
    actor: Actor,
    scenario_id: uuid.UUID,
    *,
    expected_revision: int,
    status: str,
    reason: str,
    admin_reason: str | None = None,
) -> Scenario:
    """Archive or restore a non-base scenario, with reason."""
    scenario = _scenario(db, scenario_id, lock=True)
    _, access = _project_for_write(db, actor, scenario.project_id, admin_reason)
    check_revision(scenario.revision_no, expected_revision, "scenario")
    text = meaningful(reason)
    if scenario.kind == "base":
        raise ValuationRefused("base_scenario", "The base scenario cannot be archived.", [])
    if scenario.status == status:
        raise ValuationRefused("status_unchanged", f"The scenario is already {status}.")
    if status == "archived":
        if _children(db, scenario.id):
            raise ValuationRefused(
                "scenario_has_children",
                "Active scenarios inherit from this one; re-parent or archive them first.",
                [{"scenario_id": str(c.id), "name": c.name} for c in _children(db, scenario.id)],
            )
        if scenario.probability is not None:
            raise ValuationRefused(
                "scenario_weighted",
                "The scenario carries a probability; set the project's weights without it first.",
                [],
            )
    elif scenario.parent_scenario_id is not None:
        parent = db.get(Scenario, scenario.parent_scenario_id)
        if parent is None or parent.status != "active":
            raise ValuationRefused(
                "invalid_parent", "Restore or change the parent scenario first.", []
            )
    before = snapshot(scenario)
    scenario.status = status
    scenario.revision_no += 1
    scenario.updated_at = datetime.now(UTC)
    action = "scenario_archived" if status == "archived" else "scenario_restored"
    _audit(db, scenario, actor, action, before, access, text)
    return scenario


def set_probabilities(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    expected_revision: int,
    weights: dict[uuid.UUID, Decimal],
    reason: str | None = None,
    admin_reason: str | None = None,
) -> ValuationProject:
    """Set the project's scenario weights atomically: the named active scenarios get their
    weight (0–1, exact sum 1); every other scenario's weight is cleared."""
    project, access = _project_for_write(db, actor, project_id, admin_reason)
    check_revision(project.revision_no, expected_revision)
    rows = list(
        db.scalars(
            select(Scenario).where(Scenario.project_id == project.id).with_for_update()
        ).all()
    )
    by_id = {s.id: s for s in rows}
    for sid, w in weights.items():
        s = by_id.get(sid)
        if s is None:
            raise ValuationRefused(
                "invalid_weights",
                f"Scenario {sid} is not in this project.",
                [{"scenario_id": str(sid)}],
            )
        if s.status != "active":
            raise ValuationRefused(
                "invalid_weights", f"Scenario {s.name!r} is archived.", [{"scenario_id": str(sid)}]
            )
        if not Decimal(0) <= w <= ONE:
            raise ValuationRefused(
                "invalid_weights",
                "Each probability is between 0 and 1.",
                [{"scenario_id": str(sid)}],
            )
    total = sum(weights.values(), Decimal(0))
    if weights and total != ONE:
        raise ValuationRefused(
            "weights_not_one",
            f"The probabilities sum to {total}, not exactly 1.",
            [{"sum": str(total)}],
        )
    before = {str(s.id): (str(s.probability) if s.probability is not None else None) for s in rows}
    for s in rows:
        new = weights.get(s.id)
        if new != s.probability:
            s.probability = new
            s.updated_at = datetime.now(UTC)
    project.revision_no += 1
    project.updated_by = actor.user_id
    project.updated_at = datetime.now(UTC)
    db.flush()
    audit.record(
        db,
        action="probabilities_set",
        entity_type=projects.ENTITY,
        entity_id=str(project.id),
        user_id=actor.user_id,
        before={"revision_no": project.revision_no - 1, "probabilities": before},
        after={
            "revision_no": project.revision_no,
            "probabilities": {str(k): str(v) for k, v in weights.items()},
            **access.audit_fields(),
        },
        reason=reason or access.admin_reason,
    )
    return project
