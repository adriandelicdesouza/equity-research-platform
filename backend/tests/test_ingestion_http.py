from pathlib import Path

import httpx
import pytest
from erp_backend.config import Settings
from erp_backend.ingestion import archive
from erp_backend.ingestion.http import (
    ProviderClient,
    ProviderNotConfiguredError,
    ProviderRequestFailedError,
    RateLimiter,
    fetch_and_archive,
    redact_url,
)
from erp_backend.ingestion.sec import sec_client, validate_user_agent
from erp_backend.models import ProviderFetch
from sqlalchemy import select
from sqlalchemy.orm import Session

# Test-only value: syntactically valid, used exclusively with MockTransport (never sent to SEC).
VALIDATOR_FIXTURE_UA = "Validator Fixture fixture-only@validator-fixture.dev"


def _client(handler, **kw) -> ProviderClient:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    c = ProviderClient(
        "sec_edgar",
        "Test Agent",
        1000.0,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **kw,
    )
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


def test_user_agent_header_is_sent() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, content=b"ok")

    assert _client(handler).get("https://data.sec.gov/x").content == b"ok"
    assert seen["ua"] == "Test Agent"


def test_retries_transient_errors_with_backoff_then_succeeds() -> None:
    calls = iter(
        [
            httpx.Response(503),
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(200, content=b"done"),
        ]
    )
    c = _client(lambda r: next(calls))
    assert c.get("https://data.sec.gov/x").status == 200
    assert c.sleeps[0] == 1.0 and 2.0 in c.sleeps  # type: ignore[attr-defined]


def test_gives_up_after_max_attempts() -> None:
    c = _client(lambda r: httpx.Response(500), max_attempts=3)
    with pytest.raises(ProviderRequestFailedError, match="after 3 attempts"):
        c.get("https://data.sec.gov/x?token=secret")


def test_non_retryable_status_is_returned_not_retried() -> None:
    n = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        return httpx.Response(404)

    assert _client(handler).get("https://data.sec.gov/missing").status == 404
    assert n["calls"] == 1


def test_rate_limiter_spaces_requests() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    lim = RateLimiter(5.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        lim.wait()
    assert slept == pytest.approx([0.2, 0.2])


def test_redact_url_strips_credentials_and_query() -> None:
    assert redact_url("https://user:pw@api.example/x/y?apikey=s3cret") == "https://api.example/x/y"


def test_fetch_and_archive_records_fetch_and_payload(db: Session, tmp_path: Path) -> None:
    c = _client(
        lambda r: httpx.Response(
            200, content=b'{"a":1}', headers={"content-type": "application/json"}
        )
    )
    row, payload = fetch_and_archive(db, c, tmp_path, "https://data.sec.gov/api/x.json")
    db.commit()
    assert payload == b'{"a":1}'
    stored = db.scalars(select(ProviderFetch)).one()
    assert stored.http_status == 200 and stored.content_sha256 == row.content_sha256
    assert stored.payload_path is not None and stored.content_sha256 is not None
    assert archive.load(tmp_path, stored.payload_path, stored.content_sha256) == payload


def test_failed_fetch_is_recorded_without_payload(db: Session, tmp_path: Path) -> None:
    c = _client(lambda r: httpx.Response(503), max_attempts=2)
    row, payload = fetch_and_archive(db, c, tmp_path, "https://data.sec.gov/x?key=s3cret")
    assert payload is None and row.error and row.payload_path is None
    assert "s3cret" not in row.url and "s3cret" not in (row.error or "")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "Just A Name",
        "Acme admin@example.com",
        "Acme ops@acme.test",
        "Your Name your.email@gmail.com",
        "CHANGEME changeme@corp.io",
        "placeholder x@y.org",
    ],
)
def test_sec_user_agent_rejects_missing_and_placeholder_values(value: str | None) -> None:
    with pytest.raises(ProviderNotConfiguredError):
        validate_user_agent(value)


def test_sec_client_requires_configured_user_agent(settings: Settings) -> None:
    assert settings.sec_user_agent is None  # never configured in tests
    with pytest.raises(ProviderNotConfiguredError, match="not configured"):
        sec_client(settings)


def test_sec_client_uses_validated_user_agent_with_mock_transport(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"], seen["host"] = request.headers["user-agent"], request.url.host
        return httpx.Response(200)

    configured = settings.model_copy(update={"sec_user_agent": VALIDATOR_FIXTURE_UA})
    client = sec_client(configured, transport=httpx.MockTransport(handler))
    client.get("https://data.sec.gov/submissions/CIK0000000000.json")
    assert seen == {"ua": VALIDATOR_FIXTURE_UA, "host": "data.sec.gov"}
    assert client.per_second == 5.0
