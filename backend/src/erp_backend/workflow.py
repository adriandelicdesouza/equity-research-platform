"""Running workflow actions requested through the API (D60).

:func:`perform` wraps one call to an existing domain service:

1. **Idempotency:** with an ``Idempotency-Key`` header, a key already used by this user for
   the *same* action and request is answered by replaying the stored response (header
   ``Idempotent-Replayed: true``) — the service is not called again. The same key with a
   different request is refused (``422 idempotency_key_reused``).
2. **Transaction:** the service runs in the request's session. On success the domain change,
   its ``audit_events`` row (written by the service) and the ``audit.api_actions`` record
   are committed together. On a refusal (a domain error or ``ApiError``) everything is
   rolled back, then the refusal alone is recorded and committed — nothing else persists.
3. **Errors:** domain refusals map to the standard envelope — ``409 action_refused`` for a
   state conflict (e.g. locking a dataset with open material conflicts), ``422`` for
   invalid input. Valuation refusals (``ValuationRefused``) keep their specific code and
   ``details`` (``409 dataset_not_locked``, …) so the UI can explain them. Unexpected errors
   roll back and surface as the generic ``500``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from erp_backend.auth.deps import AuthContext
from erp_backend.classifications import ClassificationError
from erp_backend.datasets import DatasetError
from erp_backend.errors import ApiError
from erp_backend.market.file_import import ImportRefusedError as MarketImportRefused
from erp_backend.market.listings import ListingError
from erp_backend.market.registration import RegistrationRefusedError
from erp_backend.models import ApiAction
from erp_backend.observability import current_request_id
from erp_backend.reference.damodaran import ImportRefusedError as ReferenceImportRefused
from erp_backend.statements.file_import import ImportRefusedError as StatementImportRefused
from erp_backend.valuation.errors import ValuationRefused

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
# Domain refusals: the request was understood but the platform's rules forbid it now.
DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    DatasetError,
    ClassificationError,
    MarketImportRefused,
    StatementImportRefused,
    ReferenceImportRefused,
    RegistrationRefusedError,
    ListingError,
)


class ActionOut(BaseModel):
    action_id: uuid.UUID
    action: str
    result: dict[str, Any]


def request_fingerprint(action: str, payload: Any) -> str:
    """SHA-256 over the action name and a canonical JSON of the request (bytes → their hash)."""

    def default(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"sha256": hashlib.sha256(value).hexdigest()}
        return str(value)

    canonical = json.dumps([action, payload], sort_keys=True, default=default)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _record(
    db: Session,
    ctx: AuthContext,
    *,
    action_id: uuid.UUID,
    action: str,
    key: str | None,
    fingerprint: str,
    target: str | None,
    status: int,
    outcome: str,
    response: dict[str, Any],
) -> ApiAction:
    row = ApiAction(
        id=action_id,
        user_id=ctx.user.id,
        action=action,
        idempotency_key=key,
        request_sha256=fingerprint,
        target=target,
        status_code=status,
        outcome=outcome,
        response=response,
        request_id=current_request_id(),
    )
    db.add(row)
    return row


def _replay(db: Session, ctx: AuthContext, key: str, action: str, fp: str) -> JSONResponse | None:
    previous = db.scalars(
        select(ApiAction).where(ApiAction.user_id == ctx.user.id, ApiAction.idempotency_key == key)
    ).first()
    if previous is None:
        return None
    if previous.action != action or previous.request_sha256 != fp:
        raise ApiError(
            422,
            "idempotency_key_reused",
            "This Idempotency-Key was already used for a different request; use a new key.",
        )
    return JSONResponse(
        previous.response, status_code=previous.status_code, headers={REPLAY_HEADER: "true"}
    )


def perform(
    db: Session,
    ctx: AuthContext,
    *,
    action: str,
    request: Any,
    idempotency_key: str | None,
    target: str | None,
    run: Callable[[], tuple[int, BaseModel]],
    action_id: uuid.UUID | None = None,
) -> JSONResponse:
    fingerprint = request_fingerprint(action, request)
    key = (idempotency_key or "").strip() or None
    if key is not None:
        replay = _replay(db, ctx, key, action, fingerprint)
        if replay is not None:
            return replay
    action_id = action_id or uuid.uuid4()  # a caller may pre-assign it to reference it
    try:
        status, result = run()
    except ValuationRefused as exc:
        error = ApiError(409, exc.code, exc.message, exc.details or None)
    except DOMAIN_ERRORS as exc:
        error = ApiError(409, "action_refused", str(exc))
    except ApiError as exc:
        error = exc
    except Exception:
        db.rollback()
        raise
    else:
        body = {
            "action_id": str(action_id),
            "action": action,
            "result": result.model_dump(mode="json"),
        }
        _record(db, ctx, action_id=action_id, action=action, key=key, fingerprint=fingerprint,
                target=target, status=status, outcome="succeeded", response=body)  # fmt: skip
        try:
            db.commit()
        except IntegrityError as exc:  # a concurrent request with the same key won the race
            db.rollback()
            raise ApiError(
                409, "idempotency_conflict", "A request with this Idempotency-Key is in progress."
            ) from exc
        return JSONResponse(body, status_code=status)

    db.rollback()  # nothing of the refused action persists
    body = {"error": {"code": error.code, "message": error.message, "action_id": str(action_id)}}
    if error.details:
        body["error"]["details"] = error.details  # type: ignore[index]
    _record(db, ctx, action_id=action_id, action=action, key=key, fingerprint=fingerprint,
            target=target, status=error.status_code, outcome="refused", response=body)  # fmt: skip
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return JSONResponse(body, status_code=error.status_code)
