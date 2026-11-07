"""Application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from erp_backend import __version__
from erp_backend.api import actions, admin, auth, data, health
from erp_backend.auth.ratelimit import SlidingWindowLimiter
from erp_backend.config import Settings, get_settings
from erp_backend.errors import install_error_handlers
from erp_backend.observability import RequestIdMiddleware, configure_logging
from erp_backend.security import OriginCheckMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Equity Research Platform API",
        version=__version__,
        docs_url="/api/docs" if settings.env != "prod" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.login_limiter = SlidingWindowLimiter(
        settings.login_max_attempts, settings.login_window_seconds
    )
    install_error_handlers(app)
    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(admin.router)
    api.include_router(data.router)
    api.include_router(actions.router)
    app.include_router(api)
    # Middleware: last added runs first.
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origin_set)
    app.add_middleware(RequestIdMiddleware)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory erp_backend.main:app_factory``."""
    return create_app()
