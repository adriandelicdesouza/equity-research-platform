from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from erp_backend.auth.rbac import Role


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    roles: list[str]
    is_active: bool
    last_login_at: datetime | None


class MeOut(BaseModel):
    user: UserOut
    permissions: list[str]
    csrf_token: str
    session_expires_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=1024)
    roles: list[Role] = Field(min_length=1)


class HealthOut(BaseModel):
    status: str
    database: str
    version: str
    engine_version: str
    environment: str
