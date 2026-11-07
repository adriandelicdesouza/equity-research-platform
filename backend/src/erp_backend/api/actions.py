"""Workflow actions API (M3 phase 2, D60).

Every action is a POST that:

* needs a logged-in session, the action's permission (see ``docs/operations/workflow-api.md``
  for the role matrix; ``data:import`` and ``dataset:lock`` are held by no role until the
  owner approves — P15), the session's CSRF token in ``X-CSRF-Token`` and an allowed
  ``Origin`` (existing ``OriginCheckMiddleware``);
* validates its body and parameters, then calls the **existing** domain service, which
  enforces the business rules (reasons, conflict gates, append-only records) and writes the
  ``audit_events`` row;
* is recorded in ``audit.api_actions`` (succeeded or refused) and returns an ``action_id``
  whose result stays inspectable at ``GET /api/actions/{id}``;
* accepts an optional ``Idempotency-Key`` header: a retry with the same key replays the
  first response instead of acting twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import select

from erp_backend.auth.deps import AuthContext, DbSession, authenticated, require
from erp_backend.auth.rbac import Permission
from erp_backend.errors import ApiError
from erp_backend.models import ApiAction
from erp_backend.read.common import MAX_LIMIT, paginate
from erp_backend.read.schemas import Page
from erp_backend.readonly import read_only_db

router = APIRouter(tags=["workflow actions"])

Authenticated = Annotated[AuthContext, Depends(authenticated())]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        pattern=r"^[A-Za-z0-9_.:-]{8,128}$",
        description="Optional; a retry with the same key replays the first response.",
    ),
]


def requires(permission: Permission) -> Any:
    return Annotated[AuthContext, Depends(require(permission))]


class ActionRecordOut(BaseModel):
    id: uuid.UUID
    action: str
    target: str | None
    outcome: str
    status_code: int
    idempotency_key: str | None
    created_at: datetime
    response: dict[str, Any]


def _record_out(r: ApiAction) -> ActionRecordOut:
    return ActionRecordOut(
        id=r.id, action=r.action, target=r.target, outcome=r.outcome, status_code=r.status_code,
        idempotency_key=r.idempotency_key, created_at=r.created_at, response=r.response,
    )  # fmt: skip


@router.get("/actions", response_model=Page[ActionRecordOut])
def list_actions(
    ctx: Authenticated,
    db: Annotated[Any, Depends(read_only_db)],
    action: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ActionRecordOut]:
    """Your own workflow actions, newest first (results and refusals)."""
    stmt = select(ApiAction).where(ApiAction.user_id == ctx.user.id)
    if action:
        stmt = stmt.where(ApiAction.action == action)
    stmt = stmt.order_by(ApiAction.created_at.desc(), ApiAction.id)
    return paginate(db, stmt, limit, offset, lambda rows: [_record_out(r) for r in rows])


@router.get("/actions/{action_id}", response_model=ActionRecordOut)
def get_action(
    action_id: uuid.UUID, ctx: Authenticated, db: Annotated[Any, Depends(read_only_db)]
) -> ActionRecordOut:
    """One action's stored result. Visible to its user, and to administrators."""
    row = db.get(ApiAction, action_id)
    if row is None or (
        row.user_id != ctx.user.id and Permission.USER_MANAGE not in ctx.permissions
    ):
        raise ApiError(404, "not_found", f"No action {action_id}.")
    return _record_out(row)


__all__ = ["DbSession", "IdempotencyKey", "requires", "router"]
