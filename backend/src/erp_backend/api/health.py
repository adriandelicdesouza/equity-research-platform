from __future__ import annotations

from erp_engine import ENGINE_VERSION
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from erp_backend import __version__
from erp_backend.api.schemas import HealthOut
from erp_backend.auth.deps import AppSettings, DbSession, public

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, dependencies=[Depends(public())])
def health(db: DbSession, settings: AppSettings) -> HealthOut | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # pragma: no cover - exercised via container health checks
        database = "unavailable"
    body = HealthOut(
        status="ok" if database == "ok" else "degraded",
        database=database,
        version=__version__,
        engine_version=ENGINE_VERSION,
        environment=settings.env,
    )
    if database != "ok":
        return JSONResponse(body.model_dump(), status_code=503)
    return body
