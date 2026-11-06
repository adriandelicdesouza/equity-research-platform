"""Origin check for state-changing requests (CSRF defence, Blueprint §11.1)."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class OriginCheckMiddleware:
    """Rejects unsafe requests whose Origin (or Referer) is not an allowed origin.

    Requests with neither header are rejected: browsers always send Origin on
    cross-origin and same-origin fetch/XHR POSTs, so its absence means a non-browser
    client, which must then set Origin explicitly.
    """

    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str]) -> None:
        self.app = app
        self.allowed = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
            }
            origin = headers.get("origin")
            if origin is None and "referer" in headers:
                origin = _origin_of(headers["referer"])
            if origin is None or origin.rstrip("/") not in self.allowed:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "origin_rejected",
                            "message": "Request origin not allowed.",
                        }
                    },
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
