"""Cross-route guarantees for every workflow action (D60): authentication, CSRF, Origin,
permission per the role matrix, and no mutation when refused."""

from __future__ import annotations

import uuid

import pytest
from erp_backend.api import actions, import_actions
from erp_backend.auth.rbac import PENDING_APPROVAL, ROLE_PERMISSIONS, Permission, Role
from erp_backend.main import create_app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tests.workflow_helpers import as_role

ID = str(uuid.uuid4())
# path → permission (the documented matrix; D60)
MATRIX = {
    "/api/companies/{company_id}/datasets/build": Permission.COMPANY_WRITE,
    "/api/companies/{company_id}/datasets/rebuild": Permission.COMPANY_WRITE,
    "/api/datasets/{dataset_id}/verify": Permission.COMPANY_READ,
    "/api/datasets/{dataset_id}/lock": Permission.DATASET_LOCK,
    "/api/datasets/{dataset_id}/discard": Permission.COMPANY_WRITE,
    "/api/datasets/{dataset_id}/conflicts/{conflict_key}/resolutions": Permission.COMPANY_WRITE,
    "/api/companies/{company_id}/classification/confirm": Permission.COMPANY_WRITE,
    "/api/imports/statements/validate": Permission.COMPANY_WRITE,
    "/api/imports/statements": Permission.DATA_IMPORT,
    "/api/imports/market/validate": Permission.COMPANY_WRITE,
    "/api/imports/market": Permission.DATA_IMPORT,
    "/api/imports/reference/validate": Permission.COMPANY_WRITE,
    "/api/imports/reference": Permission.DATA_IMPORT,
    "/api/listings/validate": Permission.COMPANY_WRITE,
    "/api/listings": Permission.COMPANY_WRITE,
    "/api/listings/{listing_id}/trading-currency": Permission.COMPANY_WRITE,
}


def _post_routes() -> list[APIRoute]:
    routes = [
        r
        for r in actions.router.routes + import_actions.router.routes
        if isinstance(r, APIRoute) and "POST" in r.methods
    ]
    return routes


def _url(path: str) -> str:
    return (
        path.replace("{company_id}", ID)
        .replace("{dataset_id}", ID)
        .replace("{listing_id}", ID)
        .replace("{conflict_key}", "0" * 16)
    )


def _permission_of(route: APIRoute) -> str:
    for dep in route.dependant.dependencies:
        policy = getattr(dep.call, "__erp_access_policy__", None)
        if policy:
            return str(policy)
    raise AssertionError(f"{route.path} has no access policy")


def test_matrix_covers_every_workflow_route_exactly() -> None:
    spec = create_app().openapi()
    mounted = {path for path, ops in spec["paths"].items() if "post" in ops}
    assert set(MATRIX) <= mounted
    for route in _post_routes():
        path = "/api" + route.path
        assert path in MATRIX, path
        assert _permission_of(route) == f"permission:{MATRIX[path]}", path
    assert len(_post_routes()) == len(MATRIX)


def test_matrix_against_roles() -> None:
    granted = {role: {p for p in MATRIX.values() if p in ROLE_PERMISSIONS[role]} for role in Role}
    assert granted[Role.REVIEWER] == {Permission.COMPANY_READ}
    assert granted[Role.ANALYST] == {Permission.COMPANY_READ, Permission.COMPANY_WRITE}
    assert granted[Role.ADMIN] == granted[Role.ANALYST]  # pending permissions excluded
    assert {p for p in MATRIX.values()} & PENDING_APPROVAL == PENDING_APPROVAL


def _snapshot(db: Session) -> dict[str, int]:
    tables = db.execute(
        text(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN "
            "('market', 'fin', 'source', 'ref')"
        )
    ).all()
    return {
        f"{s}.{t}": db.execute(text(f'SELECT count(*) FROM "{s}"."{t}"')).scalar_one()
        for s, t in tables
    }


@pytest.mark.parametrize("path", sorted(MATRIX))
def test_every_action_refuses_without_session_csrf_origin_or_permission(
    wf_client: TestClient, db: Session, path: str
) -> None:
    url = _url(path)
    before = _snapshot(db)
    r = wf_client.post(url, json={}, headers={"Origin": "https://testserver"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "not_authenticated"), url
    nobody = as_role(wf_client, db, "noroles", [])
    r = nobody.post(url, json={})
    assert r.status_code == 403 and r.json()["error"]["code"] in ("forbidden", "pending_approval")
    reviewer = as_role(wf_client, db, "rita", ["reviewer"])
    assert reviewer.post(url, json={}, csrf=None).json()["error"]["code"] == "csrf_failed"
    assert reviewer.post(url, json={}, origin=None).json()["error"]["code"] == "origin_rejected"
    r = reviewer.post(url, json={})
    if MATRIX[path] == Permission.COMPANY_READ:
        assert r.status_code == 404  # allowed through, then the unknown dataset is refused
    else:
        assert r.status_code == 403, (url, r.text)
    assert _snapshot(db) == before


def test_openapi_documents_the_actions(wf_client: TestClient) -> None:
    spec = wf_client.get("/api/openapi.json").json()
    for path in MATRIX:
        assert "post" in spec["paths"][path], path
    build = spec["paths"]["/api/companies/{company_id}/datasets/build"]["post"]
    names = {p["name"] for p in build.get("parameters", [])}
    assert "Idempotency-Key" in names
    assert db_free_count(spec) >= len(MATRIX)


def db_free_count(spec: dict[str, object]) -> int:
    return sum(1 for ops in spec["paths"].values() if "post" in ops)  # type: ignore[union-attr]


def test_refused_actions_are_logged_for_their_user(wf_client: TestClient, db: Session) -> None:
    from erp_backend.models import ApiAction

    analyst = as_role(wf_client, db, "ana", ["analyst"])
    r = analyst.post(f"/api/datasets/{ID}/discard", json={"reason": "Nothing to discard here."})
    assert r.status_code == 404
    count = db.scalar(
        select(func.count()).where(
            ApiAction.action == "dataset.discard", ApiAction.outcome == "refused"
        )
    )
    assert count == 1
