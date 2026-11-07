"""FastAPI dependencies for authentication and authorization.

Every route must declare exactly one access policy: ``public()``, ``authenticated()`` or
``require(permission)``. A test walks all routes and fails if any route declares none.
Unsafe methods additionally require the session's CSRF token in ``X-CSRF-Token``; the
Origin check for unsafe methods is enforced globally by ``OriginCheckMiddleware``.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from erp_backend.auth.rbac import PENDING_APPROVAL, Permission, permissions_for
from erp_backend.auth.sessions import resolve_session
from erp_backend.config import Settings, get_settings
from erp_backend.db import get_db
from erp_backend.errors import ApiError
from erp_backend.models import User, UserSession

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
POLICY_ATTR = "__erp_access_policy__"


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: UserSession
    permissions: frozenset[Permission]


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _authenticate(request: Request, db: Session, settings: Settings) -> AuthContext:
    token = request.cookies.get(settings.cookie_name)
    row = resolve_session(db, token, settings) if token else None
    if row is None:
        raise ApiError(401, "not_authenticated", "Authentication required.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(sent.encode(), row.csrf_token.encode()):
            raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token.")
    db.commit()  # persist last_seen_at refresh, if any
    return AuthContext(user=row.user, session=row, permissions=permissions_for(row.user.role_codes))


def _mark(fn: Callable[..., Any], policy: str) -> Callable[..., Any]:
    setattr(fn, POLICY_ATTR, policy)
    return fn


def public() -> Callable[..., None]:
    def dependency() -> None:
        return None

    return _mark(dependency, "public")


def authenticated() -> Callable[..., AuthContext]:
    def dependency(request: Request, db: DbSession, settings: AppSettings) -> AuthContext:
        return _authenticate(request, db, settings)

    return _mark(dependency, "authenticated")


def require(permission: Permission) -> Callable[..., AuthContext]:
    def dependency(request: Request, db: DbSession, settings: AppSettings) -> AuthContext:
        ctx = _authenticate(request, db, settings)
        if permission not in ctx.permissions:
            if permission in PENDING_APPROVAL:
                raise ApiError(
                    403,
                    "pending_approval",
                    f"Permission '{permission}' is not assigned to any role yet; the role "
                    "assignment awaits the owner's approval (P15).",
                )
            raise ApiError(403, "forbidden", f"Permission '{permission}' required.")
        return ctx

    return _mark(dependency, f"permission:{permission}")
