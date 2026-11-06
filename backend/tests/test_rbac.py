from erp_backend.auth.deps import POLICY_ATTR
from erp_backend.auth.rbac import ROLE_PERMISSIONS, Permission, Role
from erp_backend.main import create_app
from erp_backend.models import AuditEvent
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from .conftest import ORIGIN, login, make_user


def _policies(dependant) -> list[str]:  # type: ignore[no-untyped-def]
    found = []
    for dep in dependant.dependencies:
        policy = getattr(dep.call, POLICY_ATTR, None)
        if policy:
            found.append(policy)
        found.extend(_policies(dep))
    return found


def _api_routes(routes) -> list[APIRoute]:  # type: ignore[no-untyped-def]
    """Flatten routes, descending into included routers (FastAPI wraps them)."""
    found: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif getattr(r, "original_router", None) is not None:
            found.extend(_api_routes(r.original_router.routes))
    return found


def test_every_route_declares_exactly_one_access_policy() -> None:
    routes = _api_routes(create_app().routes)
    paths = {r.path for r in routes}
    assert {"/health", "/auth/login", "/auth/logout", "/auth/me", "/admin/users"} <= paths
    for route in routes:
        policies = _policies(route.dependant)
        assert len(policies) == 1, f"{route.methods} {route.path}: {policies}"


def test_role_permission_matrix() -> None:
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)
    assert Permission.USER_MANAGE not in ROLE_PERMISSIONS[Role.ANALYST]
    reviewer = ROLE_PERMISSIONS[Role.REVIEWER]
    assert all(p.value.endswith(":read") for p in reviewer)


def test_reviewer_and_analyst_cannot_manage_users(client: TestClient, db: Session) -> None:
    for name, role in (("rita", "reviewer"), ("andy", "analyst")):
        make_user(db, name, [role])
        login(client, name)
        assert client.get("/api/admin/users").status_code == 403


def test_admin_creates_user_with_audit(client: TestClient, db: Session) -> None:
    make_user(db, "root", ["admin"])
    body = login(client, "root")
    r = client.post(
        "/api/admin/users",
        json={
            "username": "newbie",
            "display_name": "New",
            "password": "a-long-password-1",
            "roles": ["reviewer"],
        },
        headers={"Origin": ORIGIN, "X-CSRF-Token": str(body["csrf_token"])},
    )
    assert r.status_code == 201, r.text
    assert r.json()["roles"] == ["reviewer"]
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "user_created")).one()
    assert event.after and event.after["username"] == "newbie"
    assert "password_hash" not in event.after
    assert "a-long-password-1" not in str(event.after)
    dup = client.post(
        "/api/admin/users",
        json={
            "username": "NEWBIE",
            "display_name": "Dup",
            "password": "a-long-password-1",
            "roles": ["reviewer"],
        },
        headers={"Origin": ORIGIN, "X-CSRF-Token": str(body["csrf_token"])},
    )
    assert dup.status_code == 409


def test_short_password_rejected(client: TestClient, db: Session) -> None:
    make_user(db, "root", ["admin"])
    body = login(client, "root")
    r = client.post(
        "/api/admin/users",
        json={"username": "x-user", "display_name": "X", "password": "short", "roles": ["analyst"]},
        headers={"Origin": ORIGIN, "X-CSRF-Token": str(body["csrf_token"])},
    )
    assert r.status_code == 422
