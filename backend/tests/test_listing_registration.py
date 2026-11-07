"""Manual listing registration (D56) and its integration with the CSV import (D55).

The RY / XTSE case is an integration test with synthetic prices. Everything lives in the
disposable ``erp_test`` database, inside a transaction rolled back after each test; the
identifiers below (company, CIK) are synthetic. Nothing here verifies real market data.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend import cli
from erp_backend.market.file_import import ingest, validate
from erp_backend.market.registration import (
    IssuerRef,
    ListingProposal,
    NewIssuer,
    RegistrationRefusedError,
    check_listing,
    describe_listing,
    isin_is_valid,
    register_listing,
)
from erp_backend.market.store import current_prices
from erp_backend.models import (
    AuditEvent,
    Company,
    CompanyIdentifier,
    ListingRegistration,
    Security,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

SYNTHETIC_CIK = 990001
TODAY = date(2024, 12, 31)


@pytest.fixture
def ry(db: Session) -> Security:
    """An issuer as the SEC path creates it: a US listing without a declared currency."""
    company = Company(legal_name="Synthetic Royal Test Bank", common_name="SYNTH ROYAL TEST BANK")
    db.add(company)
    db.flush()
    db.add(
        CompanyIdentifier(scheme="SEC_CIK", value=f"{SYNTHETIC_CIK:010d}", company_id=company.id)
    )
    listing = Security(
        company_id=company.id,
        ticker="RY",
        exchange_name="NYSE",
        exchange_mic="XNYS",
        is_primary=True,
        provider_symbols={"sec": "RY"},
    )
    db.add(listing)
    db.flush()
    return listing


def proposal(**kw: object) -> ListingProposal:
    base: dict[str, object] = {
        "ticker": "RY",
        "mic": "XTSE",
        "currency": "CAD",
        "security_type": "common_equity",
        "issuer": IssuerRef(same_issuer_as=("RY", "XNYS")),
        "source_name": "TSX listed-company directory (test)",
        "reason": "Register the TSX listing of the cross-listed issuer for CAD prices",
    }
    base.update(kw)
    return ListingProposal(**base)  # type: ignore[arg-type]


def codes(check: object) -> list[str]:
    return [e.code for e in check.errors]  # type: ignore[attr-defined]


def n(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


# --------------------------------------------------------------------- identifiers


def test_isin_check_digit() -> None:
    assert isin_is_valid("US0378331005")  # widely published example ISIN
    assert not isin_is_valid("US0378331006")  # wrong check digit
    assert not isin_is_valid("US037833100")  # too short
    assert not isin_is_valid("0S0378331005")  # country must be letters


# --------------------------------------------------------------------- validation (read-only)


def test_valid_proposal_previews_without_writing(db: Session, ry: Security) -> None:
    before = (n(db, Security), n(db, Company), n(db, ListingRegistration), n(db, AuditEvent))
    check = check_listing(db, proposal())
    assert check.ok, check.as_dict()
    assert check.listing["exchange_name"] == "TSX"
    assert check.listing["trading_currency"] == "CAD"
    assert check.issuer["company_id"] == str(ry.company_id)
    assert check.suggested_provider_symbols == {"yahoo_chart": "RY.TO"}
    assert check.listing["provider_symbols"] == {}  # suggestions are never stored implicitly
    assert (
        n(db, Security),
        n(db, Company),
        n(db, ListingRegistration),
        n(db, AuditEvent),
    ) == before


def test_check_is_deterministic(db: Session, ry: Security) -> None:
    first = json.dumps(check_listing(db, proposal(currency="EUR")).as_dict(), sort_keys=True)
    assert (
        json.dumps(check_listing(db, proposal(currency="EUR")).as_dict(), sort_keys=True) == first
    )


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"currency": "EUR"}, "currency_not_traded_on_exchange"),
        ({"currency": "cad"}, "unsupported_currency"),
        ({"currency": "GBX"}, "unsupported_currency"),
        ({"currency": ""}, "missing_currency"),
        ({"mic": "XTSX", "currency": "USD"}, "currency_not_traded_on_exchange"),
        ({"mic": "XNYS", "currency": "CAD"}, "currency_not_traded_on_exchange"),
        ({"mic": "XLON"}, "unsupported_exchange"),
        ({"mic": "TSX"}, "unsupported_exchange"),
        ({"ticker": "RY TO"}, "invalid_ticker"),
        ({"ticker": ""}, "invalid_ticker"),
        ({"security_type": "stock"}, "invalid_security_type"),
        ({"share_class": "B-1"}, "invalid_share_class"),
        ({"isin": "CA0000000001"}, "invalid_isin"),
        ({"source_name": " "}, "missing_source"),
        ({"reason": "because"}, "reason_too_short"),
        ({"issuer": IssuerRef()}, "issuer_resolution_required"),
        (
            {"issuer": IssuerRef(cik=SYNTHETIC_CIK, same_issuer_as=("RY", "XNYS"))},
            "issuer_resolution_required",
        ),
        ({"issuer": IssuerRef(company_id=uuid.uuid4())}, "unknown_company"),
        ({"issuer": IssuerRef(cik=123)}, "unknown_cik"),
        ({"issuer": IssuerRef(same_issuer_as=("RY", "XNAS"))}, "unknown_listing"),
        ({"provider_symbols": {"nope": "RY.TO"}}, "unknown_provider"),
        ({"provider_symbols": {"yahoo_chart": "RY TO"}}, "invalid_provider_symbol"),
    ],
)
def test_invalid_proposals_are_refused(
    db: Session, ry: Security, tmp_path: Path, changes: dict[str, object], code: str
) -> None:
    p = proposal(**changes)
    assert code in codes(check_listing(db, p))
    with pytest.raises(RegistrationRefusedError, match=code):
        register_listing(db, p)
    assert n(db, ListingRegistration) == 0


def test_usd_listing_on_tsx_is_allowed(db: Session, ry: Security) -> None:
    assert check_listing(db, proposal(ticker="RY.U", currency="USD", share_class="U")).ok


def test_issuer_by_company_id_and_cik(db: Session, ry: Security) -> None:
    for ref in (IssuerRef(company_id=ry.company_id), IssuerRef(cik=SYNTHETIC_CIK)):
        check = check_listing(db, proposal(issuer=ref))
        assert check.ok and check.issuer["company_id"] == str(ry.company_id)


# --------------------------------------------------------------------- registration


def test_register_creates_one_listing_under_the_existing_issuer(db: Session, ry: Security) -> None:
    companies = n(db, Company)
    result = register_listing(
        db, proposal(provider_symbols={"yahoo_chart": "RY.TO"}, source_reference="ref-1")
    )
    assert n(db, Company) == companies and not result.company_created
    s = result.security
    assert (s.company_id, s.ticker, s.exchange_mic, s.exchange_name) == (
        ry.company_id, "RY", "XTSE", "TSX",
    )  # fmt: skip
    assert (s.trading_currency, s.security_type, s.is_primary) == ("CAD", "common_equity", False)
    assert s.provider_symbols == {"yahoo_chart": "RY.TO"}
    reg = db.get(ListingRegistration, result.registration_id)
    assert reg is not None
    assert (reg.security_id, reg.company_id, reg.company_created) == (s.id, ry.company_id, False)
    assert reg.source_name == "TSX listed-company directory (test)"
    assert reg.source_reference == "ref-1"
    assert reg.proposal["listing"]["exchange_mic"] == "XTSE"
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "listing_registered")).one()
    assert event.entity_id == str(s.id) and event.reason == reg.reason
    assert event.after["registration_id"] == str(reg.id)  # type: ignore[index]
    assert ry.trading_currency is None  # the existing listing is untouched


def test_duplicate_registration_is_prevented(db: Session, ry: Security) -> None:
    register_listing(db, proposal())
    for again in (proposal(), proposal(ticker="ry", mic="xtse")):
        assert "duplicate_listing" in codes(check_listing(db, again))
        with pytest.raises(RegistrationRefusedError, match="duplicate_listing"):
            register_listing(db, again)
    assert n(db, ListingRegistration) == 1


def test_existing_sec_listing_cannot_be_registered_again(db: Session, ry: Security) -> None:
    check = check_listing(db, proposal(mic="XNYS", currency="USD"))
    assert "duplicate_listing" in codes(check)


def test_same_instrument_twice_on_one_exchange_is_refused(db: Session, ry: Security) -> None:
    register_listing(db, proposal())
    assert "issuer_already_listed_on_exchange" in codes(check_listing(db, proposal(ticker="RYX")))
    assert check_listing(db, proposal(ticker="RY.PR.A", security_type="preferred_equity",
                                      share_class="PRA")).ok  # fmt: skip


def test_provider_symbol_rules(db: Session, ry: Security) -> None:
    check = check_listing(db, proposal(provider_symbols={"yahoo_chart": "RY-CA.TO"}))
    assert check.ok
    assert [w.code for w in check.warnings] == ["provider_symbol_differs_from_rule"]
    register_listing(db, proposal(provider_symbols={"yahoo_chart": "RY.TO"}))
    taken = proposal(ticker="RYY", mic="XTSX", provider_symbols={"yahoo_chart": "RY.TO"},
                     issuer=IssuerRef(new=NewIssuer("Other Venture Co", "CA")))  # fmt: skip
    assert "provider_symbol_in_use" in codes(check_listing(db, taken))


def test_isin_belongs_to_one_issuer(db: Session, ry: Security) -> None:
    isin = "US0378331005"  # any valid ISIN; only format and ownership are tested
    register_listing(db, proposal(isin=isin))
    other = proposal(ticker="ZZT", mic="XTSX", isin=isin,
                     issuer=IssuerRef(new=NewIssuer("Unrelated Co", "CA")))  # fmt: skip
    assert "isin_belongs_to_other_issuer" in codes(check_listing(db, other))
    cross = proposal(mic="XNAS", ticker="RYQ", currency="USD", isin=isin)
    assert "isin_belongs_to_other_issuer" not in codes(check_listing(db, cross))


def test_new_issuer_requires_explicit_resolution_when_one_may_exist(
    db: Session, ry: Security
) -> None:
    by_name = proposal(issuer=IssuerRef(new=NewIssuer("Synthetic Royal Test Bank.", "CA")))
    check = check_listing(db, by_name)
    assert "possible_existing_issuer" in codes(check)
    assert check.issuer["candidates"] == [str(ry.company_id)]
    by_ticker = proposal(issuer=IssuerRef(new=NewIssuer("Completely Different Inc", "CA")))
    assert "possible_existing_issuer" in codes(check_listing(db, by_ticker))

    companies = n(db, Company)
    result = register_listing(db, replace(by_ticker, confirm_new_issuer=True))
    assert result.company_created and n(db, Company) == companies + 1
    reg = db.get(ListingRegistration, result.registration_id)
    assert reg is not None and reg.company_created
    assert [w["code"] for w in reg.warnings] == ["possible_existing_issuer_dismissed"]


def test_new_issuer_without_candidates(db: Session) -> None:
    p = proposal(ticker="ZQV", mic="XTSX", issuer=IssuerRef(new=NewIssuer(
        "Synthetic Venture Minerals Ltd", "ca", "Synthetic Venture", "CAD")))  # fmt: skip
    result = register_listing(db, p)
    c = result.company
    assert (c.legal_name, c.common_name, c.country, c.reporting_currency) == (
        "Synthetic Venture Minerals Ltd", "Synthetic Venture", "CA", "CAD",
    )  # fmt: skip
    assert result.security.exchange_name == "TSX Venture"
    bad = proposal(ticker="ZQW", mic="XTSX", issuer=IssuerRef(new=NewIssuer("", "Canada")))
    assert {"missing_legal_name", "invalid_country"} <= set(codes(check_listing(db, bad)))


def test_registrations_are_append_only(db: Session, ry: Security) -> None:
    register_listing(db, proposal())
    # The application role has no UPDATE/DELETE privilege at all.
    for statement in (
        "UPDATE market.listing_registrations SET reason = 'changed reason'",
        "DELETE FROM market.listing_registrations",
    ):
        with pytest.raises(DBAPIError, match="permission denied"), db.begin_nested():
            db.execute(text(statement))


def test_registration_table_rejects_truncate_for_owner(owner_engine: Engine) -> None:
    """The trigger also binds the owner role (separate test: no competing row locks)."""
    with (
        owner_engine.connect() as conn,
        conn.begin(),
        pytest.raises(DBAPIError, match="append-only"),
    ):
        conn.execute(text("TRUNCATE market.listing_registrations"))


def test_describe_listing(db: Session, ry: Security) -> None:
    result = register_listing(db, proposal(provider_symbols={"yahoo_chart": "RY.TO"}))
    info = describe_listing(db, result.security)
    assert info["origin"] == "manual_registration"
    assert info["issuer"]["other_listings"] == ["RY XNYS"]
    assert info["registrations"][0]["source_name"] == "TSX listed-company directory (test)"
    assert info["observations"] == {"prices": 0, "corporate_actions": 0}
    assert describe_listing(db, ry)["origin"] == "sec_ingestion"


# --------------------------------------------------------------------- CSV import integration

HEADER = "ticker,exchange_mic,date,open,high,low,close,close_basis,volume,currency"
RY_TO = (
    HEADER + "\n"
    "RY,XTSE,2024-03-04,130.10,131.00,129.50,130.60,raw,1000,CAD\n"
    "RY,XTSE,2024-03-05,130.60,131.40,130.00,131.20,raw,1100,CAD\n"
).encode()  # synthetic values


def test_registered_tsx_listing_is_resolved_by_the_csv_import(
    db: Session, ry: Security, tmp_path: Path
) -> None:
    before = validate(db, filename="ry.csv", data=RY_TO, template="market_prices.v1", today=TODAY)
    assert {i.code for r in before.records for i in r.issues} == {"unknown_listing"}

    tsx = register_listing(db, proposal()).security
    report = validate(db, filename="ry.csv", data=RY_TO, template="market_prices.v1", today=TODAY)
    assert report.summary["accepted"] == 2 and report.summary["rejected"] == 0
    assert {r.listing for r in report.records} == {"RY XTSE"}

    usd = RY_TO.replace(b",CAD", b",USD")
    report = validate(db, filename="ry.csv", data=usd, template="market_prices.v1", today=TODAY)
    assert {i.code for r in report.records for i in r.issues} == {"currency_mismatch"}

    us_listing = RY_TO.replace(b"RY,XTSE", b"RY,XNYS")
    report = validate(db, filename="ry.csv", data=us_listing, template="market_prices.v1",
                      today=TODAY)  # fmt: skip
    assert {i.code for r in report.records for i in r.issues} == {"listing_currency_not_set"}

    # Explicit ingestion still works unchanged — into the disposable test DB only.
    result = ingest(db, filename="ry.csv", data=RY_TO, template="market_prices.v1",
                    source_name="synthetic test fixture",
                    expected_sha256=hashlib.sha256(RY_TO).hexdigest(), raw_root=tmp_path,
                    today=TODAY)  # fmt: skip
    assert result.inserted == 2
    rows = current_prices(db, tsx.id, "manual_import", "raw")
    assert [(r.trade_date, r.close, r.currency) for r in rows] == [
        (date(2024, 3, 4), Decimal("130.60"), "CAD"),
        (date(2024, 3, 5), Decimal("131.20"), "CAD"),
    ]
    assert current_prices(db, ry.id, "manual_import") == []  # not attributed to RY XNYS


# --------------------------------------------------------------------- CLI (read-only)


def test_cli_validate_reports_errors_as_json(
    migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main([
        "listing-validate", "--ticker", "RY", "--mic", "XTSE", "--currency", "CAD",
        "--security-type", "common_equity", "--same-issuer-as", "NOPE:XNYS",
        "--provider-symbol", "yahoo_chart=RY.TO", "--source", "test", "--reason",
        "integration test of the CLI", "--json",
    ])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert [e["code"] for e in out["errors"]] == ["unknown_listing"]
    assert out["listing"]["provider_symbols"] == {"yahoo_chart": "RY.TO"}


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--provider-symbol", "yahoo_chart"], "expected PROVIDER=SYMBOL"),
        (["--same-issuer-as", "RY"], "expected TICKER:MIC"),
        (["--company-id", "x"], "is not a UUID"),
        (["--legal-name", "X"], "need --new-issuer"),
    ],
)
def test_cli_argument_errors(
    migrated_db: str, capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    args = [
        "listing-validate", "--ticker", "RY", "--mic", "XTSE", "--currency", "CAD",
        "--security-type", "common_equity", "--source", "s", "--reason", "long enough reason",
    ]  # fmt: skip
    assert cli.main(args + extra) == 1
    assert message in capsys.readouterr().err
