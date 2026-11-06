"""Audit recording. Call inside the same transaction as the change being audited."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from erp_backend.models import AuditEvent
from erp_backend.observability import current_request_id


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    user_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before=before,
        after=after,
        reason=reason,
        request_id=current_request_id(),
    )
    db.add(event)
    return event
