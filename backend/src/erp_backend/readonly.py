"""Read-only database sessions for the data API (D59).

Defence in depth, independent of which HTTP methods exist:

1. **PostgreSQL:** every read session starts its transaction with ``SET TRANSACTION READ
   ONLY``, so the server rejects INSERT/UPDATE/DELETE/DDL, whatever the application code
   does (and the append-only triggers and role grants still apply underneath).
2. **ORM:** a session marked read-only refuses to flush pending objects and refuses any
   INSERT/UPDATE/DELETE statement before it reaches the database
   (:class:`ReadOnlyViolationError`).
3. **Bounded work:** a per-transaction ``statement_timeout`` caps every query.

The session always ends with a rollback; nothing it does can be committed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.orm import ORMExecuteState, Session

READ_ONLY_KEY = "erp_read_only"
STATEMENT_TIMEOUT_MS = 10_000


class ReadOnlyViolationError(RuntimeError):
    """A write was attempted through a read-only session."""


def is_read_only(session: Session) -> bool:
    return bool(session.info.get(READ_ONLY_KEY))


@event.listens_for(Session, "before_flush")
def _refuse_flush(session: Session, *_: Any) -> None:
    if is_read_only(session) and (session.new or session.dirty or session.deleted):
        raise ReadOnlyViolationError("Read-only session: refusing to flush changes")


@event.listens_for(Session, "do_orm_execute")
def _refuse_dml(state: ORMExecuteState) -> None:
    if is_read_only(state.session) and (state.is_insert or state.is_update or state.is_delete):
        raise ReadOnlyViolationError("Read-only session: refusing a data-modifying statement")


def make_read_only(session: Session, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> Session:
    """Mark ``session`` read-only and open a READ ONLY transaction (must be its first work)."""
    session.info[READ_ONLY_KEY] = True
    session.execute(text("SET TRANSACTION READ ONLY"))
    session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
    return session


def read_only_db() -> Iterator[Session]:
    """FastAPI dependency: a fresh read-only session per request, always rolled back."""
    from erp_backend.db import _session_factory

    session = _session_factory()()
    try:
        yield make_read_only(session)
    finally:
        session.rollback()
        session.close()
