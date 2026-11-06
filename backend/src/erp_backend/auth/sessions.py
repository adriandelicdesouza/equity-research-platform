"""Server-side sessions.

The browser holds a random 256-bit token in a ``__Host-`` cookie; the database stores only
its SHA-256. Sessions have an absolute lifetime and an idle timeout (Blueprint §11.1).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from erp_backend.config import Settings
from erp_backend.models import User, UserSession

# Avoid a write on every request: refresh last_seen_at at most this often.
_TOUCH_INTERVAL = timedelta(seconds=60)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IssuedSession:
    token: str
    session: UserSession


def create_session(
    db: Session, user: User, settings: Settings, *, client_ip: str | None, user_agent: str | None
) -> IssuedSession:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    row = UserSession(
        token_hash=_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        client_ip=client_ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(row)
    db.flush()
    return IssuedSession(token=token, session=row)


def resolve_session(db: Session, token: str, settings: Settings) -> UserSession | None:
    """Return the live session for ``token`` or None (expired, idle, revoked, inactive user)."""
    row = db.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None or not row.user.is_active:
        return None
    now = utcnow()
    if now >= row.expires_at:
        return None
    if now >= row.last_seen_at + timedelta(minutes=settings.session_idle_minutes):
        return None
    if now - row.last_seen_at >= _TOUCH_INTERVAL:
        row.last_seen_at = now
    return row


def revoke_session(db: Session, row: UserSession) -> None:
    row.revoked_at = utcnow()


def revoke_all_for_user(db: Session, user: User) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
