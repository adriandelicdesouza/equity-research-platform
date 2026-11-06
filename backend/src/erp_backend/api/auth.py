"""Login, logout and current-user endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from erp_backend import audit
from erp_backend.api.schemas import LoginRequest, MeOut, UserOut
from erp_backend.auth.deps import AppSettings, AuthContext, DbSession, authenticated, public
from erp_backend.auth.passwords import hash_password, needs_rehash, verify_password
from erp_backend.auth.ratelimit import SlidingWindowLimiter
from erp_backend.auth.rbac import permissions_for
from erp_backend.auth.sessions import create_session, revoke_session, utcnow
from erp_backend.errors import ApiError
from erp_backend.models import User, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=sorted(user.role_codes),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
    )


def me_out(user: User, session: UserSession) -> MeOut:
    return MeOut(
        user=user_out(user),
        permissions=sorted(permissions_for(user.role_codes)),
        csrf_token=session.csrf_token,
        session_expires_at=session.expires_at,
    )


def get_limiter(request: Request) -> SlidingWindowLimiter:
    limiter: SlidingWindowLimiter = request.app.state.login_limiter
    return limiter


@router.post("/login", response_model=MeOut, dependencies=[Depends(public())])
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    limiter: Annotated[SlidingWindowLimiter, Depends(get_limiter)],
) -> MeOut:
    username = body.username.strip().lower()
    ip = request.client.host if request.client else "unknown"
    keys = (f"ip-user:{ip}:{username}", f"user:{username}")
    if limiter.is_blocked(*keys):
        raise ApiError(429, "rate_limited", "Too many failed attempts. Try again later.")

    user = db.scalar(select(User).where(func.lower(User.username) == username))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active:
        limiter.record_failure(*keys)
        audit.record(
            db,
            action="login_failed",
            entity_type="user",
            entity_id=str(user.id) if user else None,
            after={"username": username, "ip": ip},
        )
        db.commit()
        raise ApiError(401, "invalid_credentials", "Invalid username or password.")

    limiter.reset(*keys)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    issued = create_session(
        db, user, settings, client_ip=ip, user_agent=request.headers.get("user-agent")
    )
    audit.record(
        db,
        action="login",
        entity_type="user",
        entity_id=str(user.id),
        user_id=user.id,
        after={"session_id": str(issued.session.id), "ip": ip},
    )
    db.commit()
    response.set_cookie(
        settings.cookie_name,
        issued.token,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return me_out(user, issued.session)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    db: DbSession,
    settings: AppSettings,
    ctx: Annotated[AuthContext, Depends(authenticated())],
) -> Response:
    revoke_session(db, ctx.session)
    audit.record(
        db,
        action="logout",
        entity_type="user",
        entity_id=str(ctx.user.id),
        user_id=ctx.user.id,
        after={"session_id": str(ctx.session.id)},
    )
    db.commit()
    response.delete_cookie(
        settings.cookie_name, path="/", secure=True, httponly=True, samesite="strict"
    )
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
def me(ctx: Annotated[AuthContext, Depends(authenticated())]) -> MeOut:
    return me_out(ctx.user, ctx.session)
