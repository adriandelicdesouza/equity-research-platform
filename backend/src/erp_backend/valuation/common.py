"""Shared rules for valuation services: actors, ownership, reasons (design §3, §8; Q3)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from erp_backend.auth.rbac import Role
from erp_backend.errors import ApiError
from erp_backend.models import User, ValuationProject
from erp_backend.valuation.errors import ValuationRefused

MIN_REASON = 10


@dataclass(frozen=True)
class Actor:
    """The acting user as valuation services see it."""

    user_id: uuid.UUID
    is_admin: bool

    @classmethod
    def of(cls, user: User) -> Actor:
        return cls(user.id, Role.ADMIN.value in user.role_codes)


def meaningful(reason: str | None, what: str = "reason") -> str:
    """A reason of at least 10 characters containing letters (the D52 rule)."""
    text = (reason or "").strip()
    if len(text) < MIN_REASON or not re.search(r"[A-Za-z]", text):
        raise ValuationRefused(
            "reason_required",
            f"A {what} of at least {MIN_REASON} characters, with words, is required.",
            [{"field": what}],
        )
    return text


@dataclass(frozen=True)
class WriteAccess:
    """How the actor may write a project: as its owner, or as an administrator overriding
    ownership with a recorded reason (Q3)."""

    admin_override: bool
    admin_reason: str | None

    def audit_fields(self) -> dict[str, object]:
        if not self.admin_override:
            return {}
        return {"admin_override": True, "admin_reason": self.admin_reason}


def write_access(project: ValuationProject, actor: Actor, admin_reason: str | None) -> WriteAccess:
    """Owner-only editing; an administrator may act on another user's project with a reason.

    Raises ``403 not_owner`` for other users and ``403 admin_reason_required`` for an
    administrator who gives no (meaningful) reason."""
    if project.owner_user_id == actor.user_id:
        return WriteAccess(False, None)
    if not actor.is_admin:
        raise ApiError(403, "not_owner", "Only the project owner may change this project.")
    text = (admin_reason or "").strip()
    if len(text) < MIN_REASON or not re.search(r"[A-Za-z]", text):
        raise ApiError(
            403,
            "admin_reason_required",
            "An administrator acting on another user's project must give a reason "
            f"(admin_reason, at least {MIN_REASON} characters).",
        )
    return WriteAccess(True, text)


def require_active(project: ValuationProject) -> None:
    if project.status != "active":
        raise ValuationRefused(
            "project_archived", "The project is archived; restore it before changing it."
        )


def check_revision(current: int, expected: int, what: str = "project") -> None:
    if current != expected:
        raise ValuationRefused(
            "revision_conflict",
            f"The {what} changed since you loaded it (revision {current}, you sent {expected}); "
            "reload and retry.",
            [{"current_revision": current, "expected_revision": expected}],
        )
