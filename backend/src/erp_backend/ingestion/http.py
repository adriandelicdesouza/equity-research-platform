"""Provider HTTP access: required User-Agent, client-side rate limit, bounded retries.

Every fetch is archived *before* any parsing and recorded in ``source.provider_fetches``
(Blueprint §4.1). Tests inject an ``httpx.MockTransport``; no test touches the network.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from erp_backend.ingestion import archive
from erp_backend.models import ProviderFetch
from erp_backend.observability import current_request_id

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class ProviderNotConfiguredError(Exception):
    pass


class ProviderRequestFailedError(Exception):
    pass


class RateLimiter:
    """Minimum spacing between requests (thread-safe)."""

    def __init__(
        self,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if per_second <= 0:
            raise ValueError("rate must be positive")
        self.interval = 1.0 / per_second
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


@dataclass(frozen=True)
class FetchResult:
    status: int
    content: bytes
    content_type: str | None
    url: str


def redact_url(url: str) -> str:
    """Strip credentials and query strings (which may carry API keys) before logging/storing."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@dataclass
class ProviderClient:
    provider_code: str
    user_agent: str
    per_second: float
    transport: httpx.BaseTransport | None = None
    max_attempts: int = 4
    backoff_seconds: float = 1.0
    timeout_seconds: float = 30.0
    sleep: Callable[[float], None] = time.sleep
    limiter: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise ProviderNotConfiguredError(f"{self.provider_code}: User-Agent is required.")
        self.limiter = RateLimiter(self.per_second, sleep=self.sleep)
        self._client = httpx.Client(
            transport=self.transport,
            timeout=self.timeout_seconds,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=False,
        )

    def get(self, url: str, params: dict[str, Any] | None = None) -> FetchResult:
        last_error: str = ""
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait()
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = type(exc).__name__
            else:
                if response.status_code not in RETRY_STATUSES:
                    return FetchResult(
                        response.status_code,
                        response.content,
                        response.headers.get("content-type"),
                        str(response.url),
                    )
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit() and attempt < self.max_attempts:
                    self.sleep(min(float(retry_after), 60.0))
                    continue
            if attempt < self.max_attempts:
                self.sleep(self.backoff_seconds * 2 ** (attempt - 1))
        raise ProviderRequestFailedError(
            f"{self.provider_code}: {redact_url(url)} failed after {self.max_attempts} "
            f"attempts ({last_error})"
        )

    def close(self) -> None:
        self._client.close()


def fetch_and_archive(
    db: Session,
    client: ProviderClient,
    raw_root: Path,
    url: str,
    params: dict[str, Any] | None = None,
) -> tuple[ProviderFetch, bytes | None]:
    """Fetch, archive the raw bytes, record the fetch; returns (record, payload or None)."""
    try:
        result = client.get(url, params)
    except ProviderRequestFailedError as exc:
        row = ProviderFetch(
            provider_code=client.provider_code,
            url=redact_url(url),
            params=params or {},
            error=str(exc),
            request_id=current_request_id(),
        )
        db.add(row)
        db.flush()
        return row, None
    stored = archive.store(raw_root, client.provider_code, result.content)
    row = ProviderFetch(
        provider_code=client.provider_code,
        url=redact_url(result.url),
        params=params or {},
        http_status=result.status,
        content_sha256=stored.sha256,
        content_type=result.content_type,
        payload_path=stored.relative_path,
        bytes=stored.size,
        stored_bytes=stored.stored_size,
        request_id=current_request_id(),
    )
    db.add(row)
    db.flush()
    return row, result.content
