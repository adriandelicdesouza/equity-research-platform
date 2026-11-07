"""Shared helpers for workflow-action API tests (D60).

Tests run on the conftest ``db`` session (a savepoint-joined transaction on ``erp_test``,
rolled back after each test), so actions that commit only release a savepoint. Reads that
normally use a READ ONLY session are routed to the same test session here (read-only
enforcement itself is covered by ``test_data_api.py``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from erp_backend.auth.rbac import ROLE_PERMISSIONS, Permission, Role
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import ORIGIN, login, make_user


class Actor:
    """A logged-in client plus its CSRF token."""

    def __init__(self, client: TestClient, csrf: str) -> None:
        self.client = client
        self.csrf = csrf

    def post(
        self,
        url: str,
        *,
        json: Any = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        key: str | None = None,
        csrf: str | None = "default",
        origin: str | None = ORIGIN,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if origin is not None:
            headers["Origin"] = origin
        if csrf is not None:
            headers["X-CSRF-Token"] = self.csrf if csrf == "default" else csrf
        if key is not None:
            headers["Idempotency-Key"] = key
        if content is not None:
            headers["Content-Type"] = "text/csv"
            return self.client.post(url, content=content, params=params, headers=headers)
        return self.client.post(url, json=json, params=params, headers=headers)


def as_role(client: TestClient, db: Session, username: str, roles: list[str]) -> Actor:
    make_user(db, username, roles)
    body = login(client, username)
    return Actor(client, str(body["csrf_token"]))


def grant_pending(monkeypatch: pytest.MonkeyPatch, role: Role, *perms: Permission) -> None:
    """Test-only: simulate the owner's approval of a pending permission for ``role``."""
    monkeypatch.setitem(ROLE_PERMISSIONS, role, ROLE_PERMISSIONS[role] | frozenset(perms))
