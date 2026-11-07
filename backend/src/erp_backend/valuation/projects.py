"""Valuation projects: lifecycle, ownership and the pinned dataset (design §3; Q3, Q4).

Every change is audited in the same transaction (``audit.record``) with before/after values
and, for an administrator acting on another user's project, ``admin_override`` + reason.
Callers run these functions through ``workflow.perform`` (one transaction, action record).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.auth.rbac import Permission, permissions_for
from erp_backend.errors import ApiError
from erp_backend.models import (
    Company,
    FinancialDataset,
    Scenario,
    Security,
    User,
    ValuationProject,
)
from erp_backend.valuation.common import (
    Actor,
    check_revision,
    meaningful,
    require_active,
    write_access,
)
from erp_backend.valuation.errors import ValuationRefused

ENTITY = "valuation_project"


def snapshot(p: ValuationProject) -> dict[str, Any]:
    return {
        "name": p.name,
        "thesis": p.thesis,
        "listing_id": str(p.listing_id) if p.listing_id else None,
        "dataset_id": str(p.dataset_id),
        "status": p.status,
        "owner_user_id": str(p.owner_user_id),
        "revision_no": p.revision_no,
    }


def get_project(db: Session, project_id: uuid.UUID, *, lock: bool = False) -> ValuationProject:
    stmt = select(ValuationProject).where(ValuationProject.id == project_id)
    if lock:
        stmt = stmt.with_for_update()
    project = db.scalars(stmt).first()
    if project is None:
        raise ApiError(404, "not_found", f"No valuation project {project_id}.")
    return project


def _clean_name(name: str) -> str:
    text = name.strip()
    if not 1 <= len(text) <= 120:
        raise ValuationRefused("invalid_name", "A project name needs 1–120 characters.")
    return text


def _name_taken(db: Session, company_id: uuid.UUID, name: str, exclude: uuid.UUID | None) -> bool:
    stmt = select(ValuationProject.id).where(
        ValuationProject.company_id == company_id,
        ValuationProject.status == "active",
        func.lower(func.btrim(ValuationProject.name)) == name.lower(),
    )
    if exclude is not None:
        stmt = stmt.where(ValuationProject.id != exclude)
    return db.scalar(stmt) is not None


def _check_dataset(db: Session, company_id: uuid.UUID, dataset_id: uuid.UUID) -> FinancialDataset:
    dataset = db.get(FinancialDataset, dataset_id)
    if dataset is None or dataset.company_id != company_id:
        raise ValuationRefused(
            "dataset_mismatch", f"Dataset {dataset_id} is not a dataset of this company."
        )
    if dataset.status == "discarded":
        raise ValuationRefused("dataset_discarded", f"Dataset {dataset_id} was discarded.")
    return dataset


def _check_listing(db: Session, company_id: uuid.UUID, listing_id: uuid.UUID | None) -> None:
    if listing_id is None:
        return
    listing = db.get(Security, listing_id)
    if listing is None or listing.company_id != company_id:
        raise ValuationRefused(
            "listing_mismatch", f"Listing {listing_id} is not a listing of this company."
        )


def create_project(
    db: Session,
    actor: Actor,
    *,
    company_id: uuid.UUID,
    name: str,
    dataset_id: uuid.UUID,
    listing_id: uuid.UUID | None = None,
    thesis: str | None = None,
) -> ValuationProject:
    """Create a project owned by the actor, pinned to one dataset version (a draft is allowed
    for previews; runs need it locked), with its ``base`` scenario."""
    if db.get(Company, company_id) is None:
        raise ApiError(404, "not_found", f"No company {company_id}.")
    clean = _clean_name(name)
    dataset = _check_dataset(db, company_id, dataset_id)
    _check_listing(db, company_id, listing_id)
    if _name_taken(db, company_id, clean, None):
        raise ValuationRefused(
            "duplicate_name", f"This company already has an active project named {clean!r}."
        )
    project = ValuationProject(
        id=uuid.uuid4(),
        company_id=company_id,
        listing_id=listing_id,
        name=clean,
        thesis=thesis,
        status="active",
        owner_user_id=actor.user_id,
        dataset_id=dataset.id,
        revision_no=1,
        created_by=actor.user_id,
    )
    db.add(project)
    db.flush()
    base = Scenario(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Base",
        kind="base",
        sort_order=0,
        revision_no=0,
        status="active",
        created_by=actor.user_id,
    )
    db.add(base)
    db.flush()
    audit.record(
        db,
        action="project_created",
        entity_type=ENTITY,
        entity_id=str(project.id),
        user_id=actor.user_id,
        after={
            **snapshot(project),
            "company_id": str(company_id),
            "dataset_status": dataset.status,
            "dataset_version_no": dataset.version_no,
            "base_scenario_id": str(base.id),
        },
    )
    return project


def _touch(project: ValuationProject, actor: Actor) -> None:
    project.revision_no += 1
    project.updated_by = actor.user_id
    project.updated_at = datetime.now(UTC)


def _audit_change(
    db: Session,
    project: ValuationProject,
    actor: Actor,
    action: str,
    before: dict[str, Any],
    access: Any,
    reason: str | None = None,
) -> None:
    db.flush()
    audit.record(
        db,
        action=action,
        entity_type=ENTITY,
        entity_id=str(project.id),
        user_id=actor.user_id,
        before=before,
        after={**snapshot(project), **access.audit_fields()},
        reason=reason,
    )


def update_metadata(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    expected_revision: int,
    changes: dict[str, Any],
    admin_reason: str | None = None,
) -> ValuationProject:
    """Change name, thesis or default listing (only the fields present in ``changes``)."""
    project = get_project(db, project_id, lock=True)
    access = write_access(project, actor, admin_reason)
    require_active(project)
    check_revision(project.revision_no, expected_revision)
    before = snapshot(project)
    if "name" in changes:
        clean = _clean_name(changes["name"])
        if _name_taken(db, project.company_id, clean, project.id):
            raise ValuationRefused("duplicate_name", f"Another active project is named {clean!r}.")
        project.name = clean
    if "thesis" in changes:
        project.thesis = changes["thesis"]
    if "listing_id" in changes:
        _check_listing(db, project.company_id, changes["listing_id"])
        project.listing_id = changes["listing_id"]
    _touch(project, actor)
    _audit_change(db, project, actor, "project_updated", before, access, access.admin_reason)
    return project


def rebase(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    expected_revision: int,
    dataset_id: uuid.UUID,
    content_sha256: str,
    reason: str,
    admin_reason: str | None = None,
) -> ValuationProject:
    """Pin another dataset version of the same company (explicit, with reason). Existing
    runs keep the dataset they used."""
    project = get_project(db, project_id, lock=True)
    access = write_access(project, actor, admin_reason)
    require_active(project)
    check_revision(project.revision_no, expected_revision)
    text = meaningful(reason)
    dataset = _check_dataset(db, project.company_id, dataset_id)
    if dataset.content_sha256 != content_sha256:
        raise ValuationRefused(
            "dataset_hash_mismatch",
            "The dataset's content hash differs from the one you confirmed.",
            [{"dataset_id": str(dataset_id), "content_sha256": dataset.content_sha256}],
        )
    if dataset.id == project.dataset_id:
        raise ValuationRefused("dataset_unchanged", "The project is already pinned to it.")
    before = snapshot(project)
    project.dataset_id = dataset.id
    _touch(project, actor)
    _audit_change(db, project, actor, "project_rebased", before, access, text)
    return project


def set_status(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    expected_revision: int,
    status: str,
    reason: str,
    admin_reason: str | None = None,
) -> ValuationProject:
    """Archive (``status='archived'``) or restore (``'active'``) a project, with reason."""
    project = get_project(db, project_id, lock=True)
    access = write_access(project, actor, admin_reason)
    check_revision(project.revision_no, expected_revision)
    text = meaningful(reason)
    if project.status == status:
        raise ValuationRefused("status_unchanged", f"The project is already {status}.")
    if status == "active" and _name_taken(db, project.company_id, project.name, project.id):
        raise ValuationRefused(
            "duplicate_name", "Another active project has this name; rename it first."
        )
    before = snapshot(project)
    project.status = status
    project.status_reason = text
    _touch(project, actor)
    action = "project_archived" if status == "archived" else "project_restored"
    _audit_change(db, project, actor, action, before, access, text)
    return project


def transfer_owner(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    expected_revision: int,
    user_id: uuid.UUID,
    reason: str,
) -> ValuationProject:
    """Administrator-only: give the project to another user who holds ``valuation:write``."""
    if not actor.is_admin:
        raise ApiError(403, "forbidden", "Only an administrator may transfer project ownership.")
    project = get_project(db, project_id, lock=True)
    check_revision(project.revision_no, expected_revision)
    text = meaningful(reason)
    target = db.get(User, user_id)
    if target is None or not target.is_active:
        raise ValuationRefused("invalid_owner", f"No active user {user_id}.")
    if Permission.VALUATION_WRITE not in permissions_for(target.role_codes):
        raise ValuationRefused("invalid_owner", "The new owner must hold valuation:write.")
    if target.id == project.owner_user_id:
        raise ValuationRefused("owner_unchanged", "That user already owns the project.")
    before = snapshot(project)
    project.owner_user_id = target.id
    _touch(project, actor)
    db.flush()
    audit.record(
        db,
        action="project_owner_changed",
        entity_type=ENTITY,
        entity_id=str(project.id),
        user_id=actor.user_id,
        before=before,
        after={**snapshot(project), "admin_override": True},
        reason=text,
    )
    return project


def base_scenario(db: Session, project_id: uuid.UUID) -> Scenario:
    scenario = db.scalars(
        select(Scenario).where(Scenario.project_id == project_id, Scenario.kind == "base")
    ).one()
    return scenario
