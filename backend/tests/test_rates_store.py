"""Rate/FX persistence, provenance, idempotency, append-only, provider gate, CLI (D58).

Synthetic payloads, disposable ``erp_test`` database (each test rolled back), and an
``httpx.MockTransport`` for the fetch path — no network request is ever made.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from erp_backend import cli
from erp_backend.ingestion import archive
from erp_backend.ingestion.http import ProviderClient, ProviderDisabledError
from erp_backend.models import (
    AuditEvent,
    Provider,
    ProviderFetch,
    RateObservationRow,
    RateSeries,
)
from erp_backend.rates import bank_of_canada, treasury
from erp_backend.rates.model import RATE_SERIES
from erp_backend.rates.store import (
    RatesError,
    current_fx,
    current_rates,
    fetch_rates,
    ingest_archived,
    store_parsed,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from tests.rates_payloads import TREASURY_GOOD, VALET_GOOD, treasury_csv, valet

TODAY = date(2024, 12, 31)


def mock_client(code: str, payload: bytes, status: int = 200) -> tuple[ProviderClient, list[str]]:
    """A client whose transport answers locally; records every requested URL."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status, content=payload, headers={"content-type": "text/plain"})

    client = ProviderClient(code, "test-agent", 1000.0, transport=httpx.MockTransport(handler))
    return client, seen


@contextmanager
def shared(db: Session) -> Iterator[Session]:
    """The test session for a CLI command: closing it would roll back the test data."""
    yield db


def archived_fetch(db: Session, code: str, payload: bytes, root: Path) -> ProviderFetch:
    stored = archive.store(root, code, payload)
    fetch = ProviderFetch(
        provider_code=code,
        url="https://example.invalid/synthetic",
        http_status=200,
        content_sha256=stored.sha256,
        payload_path=stored.relative_path,
        bytes=stored.size,
        stored_bytes=stored.stored_size,
    )
    db.add(fetch)
    db.flush()
    return fetch


# --------------------------------------------------------------------------- registry and gate


def test_seeded_series_match_the_code_registry(db: Session) -> None:
    rows = {r.code: r for r in db.scalars(select(RateSeries))}
    assert set(rows) == {s.code for s in RATE_SERIES}
    for spec in RATE_SERIES:
        row = rows[spec.code]
        assert (
            row.provider_code,
            row.provider_series_id,
            row.currency,
            row.rate_type,
            row.tenor,
            row.tenor_months,
            row.unit,
            row.quote_convention,
        ) == (
            spec.provider_code,
            spec.provider_series_id,
            spec.currency,
            spec.rate_type.value,
            spec.tenor,
            spec.tenor_months,
            "percent",
            spec.quote_convention,
        )


def test_every_provider_is_seeded_disabled(db: Session) -> None:
    assert db.scalars(select(Provider.code).where(Provider.enabled)).all() == []


@pytest.mark.parametrize("factory", [treasury.treasury_client, bank_of_canada.boc_client])
def test_gate_refuses_while_disabled_and_makes_no_request(db: Session, factory: object) -> None:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))  # type: ignore[func-returns-value]
    with pytest.raises(ProviderDisabledError, match="disabled"):
        factory(db, transport)  # type: ignore[operator]
    assert seen == []


def test_app_role_cannot_enable_a_provider(db: Session) -> None:
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE source.providers SET enabled = true WHERE code = 'us_treasury'"))


# --------------------------------------------------------------------------- store


def test_fetch_archives_first_and_stores_with_provenance(db: Session, tmp_path: Path) -> None:
    client, seen = mock_client("us_treasury", TREASURY_GOOD)
    url, params = treasury.year_url(2024, TODAY)
    result = fetch_rates(db, treasury.UsTreasury(), client, url, params, tmp_path)
    assert len(seen) == 1 and "example" not in seen[0]  # mocked, never sent
    fetch = db.get(ProviderFetch, result.fetch_id)
    assert fetch is not None and fetch.provider_code == "us_treasury"
    assert fetch.content_sha256 == hashlib.sha256(TREASURY_GOOD).hexdigest()
    assert archive.load(tmp_path, fetch.payload_path, fetch.content_sha256) == TREASURY_GOOD  # type: ignore[arg-type]
    assert result.rates_inserted == 39 and result.rates_already_present == 0
    rows = current_rates(db, "UST_PAR_10Y")
    assert [(r.obs_date, r.value) for r in rows][-1] == (date(2024, 3, 5), Decimal("4.15"))
    assert {r.fetch_id for r in rows} == {fetch.id}
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "rates_stored").order_by(AuditEvent.id.desc())
    ).first()
    assert event is not None and event.entity_id == str(fetch.id)


def test_non_200_is_archived_but_not_parsed(db: Session, tmp_path: Path) -> None:
    client, _ = mock_client("us_treasury", b"Not found", status=404)
    url, params = treasury.year_url(2024, TODAY)
    with pytest.raises(RatesError, match="HTTP 404"):
        fetch_rates(db, treasury.UsTreasury(), client, url, params, tmp_path)
    assert db.scalar(select(func.count()).select_from(RateObservationRow)) == 0


def test_client_and_adapter_must_match(db: Session, tmp_path: Path) -> None:
    client, seen = mock_client("bank_of_canada", TREASURY_GOOD)
    with pytest.raises(RatesError, match="different providers"):
        fetch_rates(db, treasury.UsTreasury(), client, "https://x.invalid", {}, tmp_path)
    assert seen == []


def test_store_is_idempotent_and_revisions_append(db: Session, tmp_path: Path) -> None:
    adapter = treasury.UsTreasury()
    first = archived_fetch(db, "us_treasury", TREASURY_GOOD, tmp_path)
    store_parsed(db, adapter.parse(TREASURY_GOOD, TODAY), first)
    again = store_parsed(db, adapter.parse(TREASURY_GOOD, TODAY), first)
    assert (again.rates_inserted, again.rates_already_present, again.rates_revised) == (0, 39, 0)

    revised_payload = treasury_csv(
        "03/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.16,4.42,4.29"
    )
    second = archived_fetch(db, "us_treasury", revised_payload, tmp_path)
    result = store_parsed(db, adapter.parse(revised_payload, TODAY), second)
    assert (result.rates_inserted, result.rates_revised) == (1, 1)
    history = db.scalars(
        select(RateObservationRow.value)
        .where(
            RateObservationRow.series_code == "UST_PAR_10Y",
            RateObservationRow.obs_date == date(2024, 3, 5),
        )
        .order_by(RateObservationRow.id)
    ).all()
    assert history == [Decimal("4.15"), Decimal("4.16")]  # both kept
    latest = current_rates(db, "UST_PAR_10Y", date(2024, 3, 5), date(2024, 3, 5))
    assert [r.value for r in latest] == [Decimal("4.16")]


def test_boc_rates_and_fx_store_and_read_in_published_direction(
    db: Session, tmp_path: Path
) -> None:
    fetch = archived_fetch(db, "bank_of_canada", VALET_GOOD, tmp_path)
    result = store_parsed(db, bank_of_canada.BankOfCanada().parse(VALET_GOOD, TODAY), fetch)
    assert (result.rates_inserted, result.fx_inserted) == (3, 3)
    rows = current_fx(db, "usd", "cad")
    assert (rows[0].obs_date, rows[0].rate) == (date(2024, 3, 1), Decimal("1.3550"))
    assert all(r.provider_series_id == "FXUSDCAD" and r.fetch_id == fetch.id for r in rows)
    assert current_fx(db, "CAD", "USD") == []  # never inverted
    assert [r.value for r in current_rates(db, "GOC_BMK_10Y")] == [
        Decimal("3.41"),
        Decimal("3.44"),
        Decimal("3.47"),
    ]


def test_rejected_and_missing_are_counted_not_stored(db: Session, tmp_path: Path) -> None:
    payload = valet(
        [
            {"d": "2024-03-04", "FXUSDCAD": {"v": "-1"}, "BD.CDN.10YR.DQ.YLD": {"v": ""}},
            {"d": "2024-03-05", "FXUSDCAD": {"v": "1.3571"}},
        ]
    )
    fetch = archived_fetch(db, "bank_of_canada", payload, tmp_path)
    result = store_parsed(db, bank_of_canada.parse_valet(payload, TODAY), fetch)
    assert (result.fx_inserted, result.rates_inserted) == (1, 0)
    assert result.rejected == 1 and result.missing == 2


def test_ingest_archived_reparses_without_network(db: Session, tmp_path: Path) -> None:
    fetch = archived_fetch(db, "bank_of_canada", VALET_GOOD, tmp_path)
    result = ingest_archived(db, bank_of_canada.BankOfCanada(), fetch.id, tmp_path, today=TODAY)
    assert result.fx_inserted == 3
    with pytest.raises(RatesError, match="not us_treasury"):
        ingest_archived(db, treasury.UsTreasury(), fetch.id, tmp_path, today=TODAY)
    with pytest.raises(RatesError, match="No archived payload"):
        ingest_archived(db, treasury.UsTreasury(), uuid.uuid4(), tmp_path)


def test_corrupted_archive_is_never_parsed(db: Session, tmp_path: Path) -> None:
    fetch = archived_fetch(db, "us_treasury", TREASURY_GOOD, tmp_path)
    other = archive.store(tmp_path, "us_treasury", b"Date,10 Yr\n")
    target = tmp_path / fetch.payload_path  # type: ignore[operator]
    target.chmod(0o644)
    target.write_bytes((tmp_path / other.relative_path).read_bytes())
    with pytest.raises(archive.ArchiveIntegrityError):
        ingest_archived(db, treasury.UsTreasury(), fetch.id, tmp_path, today=TODAY)


def test_store_refuses_payload_from_another_provider(db: Session, tmp_path: Path) -> None:
    fetch = archived_fetch(db, "us_treasury", VALET_GOOD, tmp_path)
    with pytest.raises(RatesError, match="different providers"):
        store_parsed(db, bank_of_canada.parse_valet(VALET_GOOD, TODAY), fetch)


# --------------------------------------------------------------------------- constraints


def test_observations_are_append_only_for_every_role(owner_engine: Engine, db: Session) -> None:
    with pytest.raises(DBAPIError, match="permission denied"), db.begin_nested():
        db.execute(text("UPDATE market.rate_observations SET value = 1"))
    with owner_engine.connect() as conn, conn.begin():
        fetch_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO source.provider_fetches (id, provider_code, url) "
                "VALUES (:f, 'bank_of_canada', 'https://example.invalid')"
            ),
            {"f": fetch_id},
        )
        conn.execute(
            text(
                "INSERT INTO market.rate_observations (series_code, provider_code, obs_date, "
                "value, fetch_id) VALUES "
                "('GOC_BMK_10Y', 'bank_of_canada', '2024-03-05', 3.47, :f)"
            ),
            {"f": fetch_id},
        )
        conn.execute(
            text(
                "INSERT INTO market.fx_rates (base, quote, obs_date, rate, provider_code, "
                "provider_series_id, fetch_id) VALUES ('USD', 'CAD', '2024-03-05', 1.3571, "
                "'bank_of_canada', 'FXUSDCAD', :f)"
            ),
            {"f": fetch_id},
        )
        for statement in (
            "UPDATE market.rate_observations SET value = 1",
            "DELETE FROM market.fx_rates",
            "UPDATE market.rate_series SET tenor = 'X'",
            "DELETE FROM market.rate_series",
            "TRUNCATE market.rate_observations",
        ):
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text(statement))
            nested.rollback()
        conn.rollback()


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO market.rate_observations (series_code, provider_code, obs_date, value, "
            "fetch_id) VALUES ('UST_PAR_10Y', 'bank_of_canada', '2024-03-05', 4, :f)",
            "fk_rate_observations_series",
        ),
        (
            "INSERT INTO market.rate_observations (series_code, provider_code, obs_date, value, "
            "fetch_id) VALUES ('UST_PAR_10Y', 'us_treasury', '2024-03-05', 415, :f)",
            "value_range",
        ),
        (
            "INSERT INTO market.fx_rates (base, quote, obs_date, rate, provider_code, "
            "provider_series_id, fetch_id) VALUES ('CAD', 'CAD', '2024-03-05', 1, "
            "'bank_of_canada', "
            "'FXUSDCAD', :f)",
            "pair",
        ),
        (
            "INSERT INTO market.fx_rates (base, quote, obs_date, rate, provider_code, "
            "provider_series_id, fetch_id) VALUES ('USD', 'CAD', '2024-03-05', 0, "
            "'bank_of_canada', "
            "'FXUSDCAD', :f)",
            "rate_positive",
        ),
    ],
)
def test_database_constraints(db: Session, tmp_path: Path, statement: str, constraint: str) -> None:
    fetch = archived_fetch(db, "us_treasury", TREASURY_GOOD, tmp_path)
    with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
        db.execute(text(statement), {"f": fetch.id})


# --------------------------------------------------------------------------- CLI


def test_cli_parse_file_previews_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = tmp_path / "valet.json"
    file.write_bytes(VALET_GOOD)
    assert (
        cli.main(
            ["rates-parse-file", "--provider", "bank_of_canada", "--file", str(file), "--json"]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["fx_observations"] == 3
    assert out["fx"][0] == {
        "base": "USD",
        "quote": "CAD",
        "date": "2024-03-01",
        "rate": "1.3550",
        "series": "FXUSDCAD",
    }
    file.write_bytes(b"<html>")
    assert cli.main(["rates-parse-file", "--provider", "us_treasury", "--file", str(file)]) == 1
    assert "payload rejected" in capsys.readouterr().err


def test_cli_fetch_is_refused_while_disabled(
    migrated_db: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    assert cli.main(["rates-fetch", "--provider", "us_treasury", "--year", "2024"]) == 1
    assert "refused (no request made)" in capsys.readouterr().err
    assert (
        cli.main(
            [
                "rates-fetch",
                "--provider",
                "bank_of_canada",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
            ]
        )
        == 1
    )
    assert "bank_of_canada provider is disabled" in capsys.readouterr().err


def test_cli_series_list_and_show(
    db: Session,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = archived_fetch(db, "bank_of_canada", VALET_GOOD, tmp_path)
    store_parsed(db, bank_of_canada.parse_valet(VALET_GOOD, TODAY), fetch)
    monkeypatch.setattr("erp_backend.db.new_session", lambda: shared(db))
    assert cli.main(["rate-series-list"]) == 0
    out = capsys.readouterr().out
    assert "GOC_BMK_10Y" in out and "observations 3" in out and "UST_PAR_30Y" in out
    assert cli.main(["rates-show", "--series", "GOC_BMK_10Y"]) == 0
    assert "2024-03-05  3.47 percent" in capsys.readouterr().out
    assert cli.main(["fx-show", "--base", "USD", "--quote", "CAD"]) == 0
    assert "1.3571" in capsys.readouterr().out
    assert cli.main(["fx-show", "--base", "CAD", "--quote", "USD"]) == 0
    assert "no observations in this direction" in capsys.readouterr().out
    assert cli.main(["rates-show", "--series", "UST_10Y"]) == 1
    assert "unknown series" in capsys.readouterr().err
    assert (
        cli.main(["rates-ingest-archived", "--provider", "us_treasury", "--fetch-id", "nope"]) == 1
    )
    assert "invalid fetch id" in capsys.readouterr().err
