"""Structured API errors: every error response is ``{"error": {"code", "message"}}``."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from erp_backend.valuation.errors import ValuationRefused

_log = structlog.get_logger("erp.api")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _body(code: str, message: str, **extra: object) -> dict[str, object]:
    return {"error": {"code": code, "message": message, **extra}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        extra = {"details": exc.details} if exc.details else {}
        return JSONResponse(_body(exc.code, exc.message, **extra), status_code=exc.status_code)

    @app.exception_handler(ValuationRefused)
    async def _valuation_refused(_: Request, exc: ValuationRefused) -> JSONResponse:
        extra = {"details": exc.details} if exc.details else {}
        return JSONResponse(_body(exc.code, exc.message, **extra), status_code=409)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(_body("http_error", str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            _body("validation_error", "Request validation failed.", details=details),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Never return SQL, paths or tracebacks to the client; log the type and route only
        # (no query parameters or bodies, which may carry sensitive data).
        from erp_backend.readonly import ReadOnlyViolationError

        if isinstance(exc, ReadOnlyViolationError):
            _log.error("read_only_violation", path=request.url.path)
            return JSONResponse(_body("read_only", "This endpoint is read-only."), status_code=405)
        _log.error("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
        return JSONResponse(_body("internal_error", "An internal error occurred."), status_code=500)
