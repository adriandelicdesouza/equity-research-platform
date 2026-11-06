"""User administration (admin only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from erp_backend import audit
from erp_backend.api.auth import user_out
from erp_backend.api.schemas import UserCreate, UserOut
from erp_backend.auth.deps import AuthContext, DbSession, require
from erp_backend.auth.passwords import hash_password
from erp_backend.auth.rbac import Permission
from erp_backend.errors import ApiError
from erp_backend.models import User, UserRole

router = APIRouter(prefix="/admin", tags=["admin"])

ManageUsers = Annotated[AuthContext, Depends(require(Permission.USER_MANAGE))]


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbSession, _: ManageUsers) -> list[UserOut]:
    return [user_out(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: DbSession, ctx: ManageUsers) -> UserOut:
    user = create_user_record(
        db,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        roles=[r.value for r in body.roles],
    )
    audit.record(
        db,
        action="user_created",
        entity_type="user",
        entity_id=str(user.id),
        user_id=ctx.user.id,
        after=user_out(user).model_dump(mode="json"),
    )
    db.commit()
    return user_out(user)


def create_user_record(
    db: DbSession, *, username: str, display_name: str, password: str, roles: list[str]
) -> User:
    name = username.strip().lower()
    if db.scalar(select(User.id).where(func.lower(User.username) == name)) is not None:
        raise ApiError(409, "username_taken", "A user with this username already exists.")
    user = User(
        username=name,
        display_name=display_name,
        password_hash=hash_password(password),
        is_active=True,
        roles=[UserRole(role_code=r) for r in sorted(set(roles))],
    )
    db.add(user)
    db.flush()
    return user
