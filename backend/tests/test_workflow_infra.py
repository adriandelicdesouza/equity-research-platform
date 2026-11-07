"""Workflow-action infrastructure (D60): action log, idempotency, refusal rollback,
pending permissions. Synthetic data on ``erp_test``; each test rolled back."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from erp_backend.auth.deps import AuthContext
from erp_backend.auth.rbac import PENDING_APPROVAL, Permission
from erp_backend.datasets import DatasetError
from erp_backend.errors import ApiError
from erp_backend.models import ApiAction, Company, User
from erp_backend.workflow import ActionOut, perform, request_fingerprint
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import make_user
from tests.workflow_helpers import as_role


class Out(BaseModel):
    company_id: uuid.UUID


def ctx_for(db: Session, username: str = "wf-user") -> AuthContext:
    make_user(db, username, ["analyst"])
    user = db.scalars(select(User).where(User.username == username)).one()
    return AuthContext(user=user, session=None, permissions=frozenset())  # type: ignore[arg-type]


def create(db: Session, name: str) -> Any:
    def run() -> tuple[int, BaseModel]:
        c = Company(legal_name=name)
        db.add(c)
        db.flush()
        return 201, Out(company_id=c.id)

    return run


def companies(db: Session, name: str) -> int:
    return db.scalar(select(func.count()).where(Company.legal_name == name)) or 0


def test_success_commits_change_and_action_record(db: Session) -> None:
    ctx = ctx_for(db)
    r = perform(
        db,
        ctx,
        action="test.create",
        request={"n": 1},
        idempotency_key=None,
        target="t",
        run=create(db, "WF Co"),
    )
    body = ActionOut.model_validate_json(r.body)
    assert r.status_code == 201 and companies(db, "WF Co") == 1
    row = db.get(ApiAction, body.action_id)
    assert row is not None and (row.outcome, row.status_code, row.target) == ("succeeded", 201, "t")
    assert row.request_sha256 == request_fingerprint("test.create", {"n": 1})


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DatasetError("Dataset is locked; only drafts can change"), 409, "action_refused"),
        (ApiError(422, "validation_error", "bad"), 422, "validation_error"),
    ],
)
def test_refusal_rolls_back_and_is_recorded(
    db: Session, error: Exception, status: int, code: str
) -> None:
    ctx = ctx_for(db)

    def run() -> tuple[int, BaseModel]:
        db.add(Company(legal_name="Must not persist"))
        db.flush()
        raise error

    r = perform(db, ctx, action="test.fail", request={}, idempotency_key=None, target=None, run=run)
    body = json.loads(r.body)
    assert r.status_code == status and body["error"]["code"] == code
    assert companies(db, "Must not persist") == 0
    row = db.get(ApiAction, uuid.UUID(body["error"]["action_id"]))
    assert row is not None and row.outcome == "refused" and row.status_code == status


def test_unexpected_error_rolls_back_and_propagates(db: Session) -> None:
    ctx = ctx_for(db)

    def run() -> tuple[int, BaseModel]:
        db.add(Company(legal_name="Boom Co"))
        db.flush()
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        perform(db, ctx, action="test.boom", request={}, idempotency_key=None, target=None, run=run)
    assert companies(db, "Boom Co") == 0


def test_idempotent_retry_replays_without_acting_again(db: Session) -> None:
    ctx = ctx_for(db)
    key = "retry-key-0001"
    first = perform(
        db,
        ctx,
        action="test.create",
        request={"n": 1},
        idempotency_key=key,
        target=None,
        run=create(db, "Once Co"),
    )
    second = perform(
        db,
        ctx,
        action="test.create",
        request={"n": 1},
        idempotency_key=key,
        target=None,
        run=create(db, "Once Co"),
    )
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.status_code == first.status_code
    assert json.loads(second.body) == json.loads(first.body)
    assert companies(db, "Once Co") == 1
    with pytest.raises(ApiError) as exc:
        perform(
            db,
            ctx,
            action="test.create",
            request={"n": 2},
            idempotency_key=key,
            target=None,
            run=create(db, "Other Co"),
        )
    assert exc.value.code == "idempotency_key_reused"
    assert companies(db, "Other Co") == 0


def test_keys_are_per_user(db: Session) -> None:
    a, b = ctx_for(db, "wf-a"), ctx_for(db, "wf-b")
    for ctx in (a, b):
        perform(
            db,
            ctx,
            action="test.create",
            request={},
            idempotency_key="shared-key-1",
            target=None,
            run=create(db, "Per User Co"),
        )
    assert companies(db, "Per User Co") == 2


def test_api_actions_are_append_only(owner_engine: Engine, db: Session) -> None:
    with pytest.raises(DBAPIError, match="permission denied"), db.begin_nested():
        db.execute(text("DELETE FROM audit.api_actions"))
    with owner_engine.connect() as conn, conn.begin():
        conn.execute(
            text(
                "INSERT INTO audit.api_actions (id, user_id, action, request_sha256, status_code, "
                "outcome, response) VALUES (gen_random_uuid(), gen_random_uuid(), 'x', :h, 200, "
                "'succeeded', '{}')"
            ),
            {"h": "a" * 64},
        )
        for statement in (
            "UPDATE audit.api_actions SET outcome = 'refused'",
            "DELETE FROM audit.api_actions",
            "TRUNCATE audit.api_actions",
        ):
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text(statement))
            nested.rollback()
        conn.rollback()


def test_action_log_endpoints_show_own_actions(
    wf_client: TestClient,
    db: Session,
) -> None:
    actor = as_role(wf_client, db, "logreader", ["analyst"])
    user = db.scalars(select(User).where(User.username == "logreader")).one()
    ctx = AuthContext(user=user, session=None, permissions=frozenset())  # type: ignore[arg-type]
    r = perform(
        db,
        ctx,
        action="test.create",
        request={},
        idempotency_key=None,
        target="x",
        run=create(db, "Logged Co"),
    )
    action_id = ActionOut.model_validate_json(r.body).action_id
    page = actor.client.get("/api/actions").json()
    assert page["total"] == 1 and page["items"][0]["id"] == str(action_id)
    assert actor.client.get(f"/api/actions/{action_id}").json()["outcome"] == "succeeded"
    other = as_role(wf_client, db, "someoneelse", ["reviewer"])
    assert other.client.get(f"/api/actions/{action_id}").status_code == 404
    assert other.client.get("/api/actions").json()["total"] == 0


def test_pending_permissions_are_held_by_no_role() -> None:
    from erp_backend.auth.rbac import ROLE_PERMISSIONS

    for perms in ROLE_PERMISSIONS.values():
        assert not PENDING_APPROVAL & perms
    assert (
        Permission.DATA_IMPORT in PENDING_APPROVAL and Permission.DATASET_LOCK in PENDING_APPROVAL
    )
