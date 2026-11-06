from datetime import timedelta

from erp_backend.auth.sessions import utcnow
from erp_backend.models import AuditEvent, UserSession
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .conftest import ORIGIN, PASSWORD, login, make_user


def test_login_sets_hardened_host_cookie(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    r = client.post(
        "/api/auth/login",
        json={"username": "Alice", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-erp_session=")
    for attr in ("HttpOnly", "Secure", "Path=/", "SameSite=strict"):
        assert attr in cookie
    assert "Domain=" not in cookie
    body = r.json()
    assert body["user"]["username"] == "alice"
    assert "valuation:write" in body["permissions"]
    assert body["csrf_token"]


def test_session_token_is_stored_hashed(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    login(client, "alice")
    token = client.cookies.get("__Host-erp_session")
    row = db.scalars(select(UserSession)).one()
    assert token and row.token_hash != token.encode()
    assert len(row.token_hash) == 32


def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: TestClient, db: Session
) -> None:
    make_user(db, "alice", ["analyst"])
    a = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "nope-nope-nope"},
        headers={"Origin": ORIGIN},
    )
    b = client.post(
        "/api/auth/login",
        json={"username": "ghost", "password": "nope-nope-nope"},
        headers={"Origin": ORIGIN},
    )
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    actions = db.scalars(select(AuditEvent.action)).all()
    assert actions.count("login_failed") == 2


def test_login_rate_limited_after_max_attempts(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    for _ in range(3):  # ERP_LOGIN_MAX_ATTEMPTS=3 in tests
        client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-wrong"},
            headers={"Origin": ORIGIN},
        )
    r = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 429


def test_login_rejected_without_or_with_foreign_origin(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    payload = {"username": "alice", "password": PASSWORD}
    assert client.post("/api/auth/login", json=payload).status_code == 403
    assert (
        client.post(
            "/api/auth/login", json=payload, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/auth/login", json=payload, headers={"Referer": f"{ORIGIN}/login"}
        ).status_code
        == 200
    )


def test_me_requires_session(client: TestClient) -> None:
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"


def test_unsafe_request_requires_csrf_token(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    body = login(client, "alice")
    assert client.post("/api/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    assert (
        client.post(
            "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "forged"}
        ).status_code
        == 403
    )
    r = client.post(
        "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": str(body["csrf_token"])}
    )
    assert r.status_code == 204


def test_logout_revokes_session(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    body = login(client, "alice")
    token = client.cookies.get("__Host-erp_session")
    client.post(
        "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": str(body["csrf_token"])}
    )
    client.cookies.set("__Host-erp_session", token or "")
    assert client.get("/api/auth/me").status_code == 401
    assert db.scalars(select(UserSession)).one().revoked_at is not None


def test_idle_timeout_expires_session(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    login(client, "alice")
    db.execute(update(UserSession).values(last_seen_at=utcnow() - timedelta(minutes=61)))
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_absolute_lifetime_expires_session(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    login(client, "alice")
    db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_successful_login_and_logout_are_audited(client: TestClient, db: Session) -> None:
    make_user(db, "alice", ["analyst"])
    body = login(client, "alice")
    client.post(
        "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": str(body["csrf_token"])}
    )
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)).all()
    actions = [e.action for e in events]
    assert "login" in actions and "logout" in actions
    assert all(e.request_id for e in events if e.action in {"login", "logout"})
