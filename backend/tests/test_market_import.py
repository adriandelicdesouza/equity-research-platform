"""Market-data CSV import (D55): format boundary, validation, preview, ingestion.

All data here is synthetic and exists only inside the disposable ``erp_test`` database,
in a transaction that is rolled back after each test. No network access.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend import cli
from erp_backend.market.file_import import (
    ImportRefusedError,
    IngestResult,
    Status,
    ValidationReport,
    ingest,
    load_import_file,
    validate,
)
from erp_backend.market.listings import ListingError, set_trading_currency
from erp_backend.market.store import current_prices
from erp_backend.market.tabular import FileFormatError, read_csv, read_table
from erp_backend.models import (
    AuditEvent,
    Company,
    CorporateAction,
    PriceObservation,
    ProviderFetch,
    Security,
    SourceImport,
)
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TODAY = date(2024, 12, 31)
PRICES = "market_prices.v1"
ACTIONS = "corporate_actions.v1"
HEADER = (
    "ticker,exchange_mic,date,open,high,low,close,close_basis,adj_close,adj_close_basis,volume,"
    "currency"
)


def csv(*lines: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *lines]) + "\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


GOOD = csv(
    "TST,XNAS,2024-03-04,10.00,10.50,9.90,10.20,raw,10.10,split_and_dividend_adjusted,1000,USD",
    "TST,XNAS,2024-03-05,10.20,10.60,10.00,10.40,raw,10.30,split_and_dividend_adjusted,1200,USD",
    "TST,XNAS,2024-03-06,10.40,10.70,10.30,10.65,raw,10.55,split_and_dividend_adjusted,900,USD",
)


@pytest.fixture
def listing(db: Session) -> Security:
    company = Company(legal_name="Import Test Co")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id,
        ticker="TST",
        exchange_name="Nasdaq",
        exchange_mic="XNAS",
        trading_currency="USD",
    )
    db.add(security)
    db.flush()
    return security


def run(db: Session, data: bytes, template: str = PRICES, name: str = "p.csv") -> ValidationReport:
    return validate(db, filename=name, data=data, template=template, today=TODAY)


def do_ingest(
    db: Session, data: bytes, root: Path, template: str = PRICES, **kw: object
) -> IngestResult:
    return ingest(
        db,
        filename="p.csv",
        data=data,
        template=template,
        source_name="Exchange historical export (test)",
        expected_sha256=sha(data),
        raw_root=root,
        today=TODAY,
        **kw,  # type: ignore[arg-type]
    )


def codes(report: ValidationReport, row: int = 1) -> list[str]:
    return [i.code for i in report.records[row - 1].issues]


def n(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


# --------------------------------------------------------------------- format boundary


def test_csv_reader_bom_blank_lines_quoting_and_locators() -> None:
    table = read_csv(b'\xef\xbb\xbfa,b\n\n"x, y",2\n\n3,"4"\n')
    assert table.kind == "csv"
    assert table.header == ("a", "b")
    assert [r.cells for r in table.rows] == [("x, y", "2"), ("3", "4")]
    assert [(r.number, r.locator) for r in table.rows] == [(1, "line 3"), (2, "line 5")]


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("p.csv", b"", "empty"),
        ("p.csv", b"a,b\n\xff\xfe,1\n", "UTF-8"),
        ("p.csv", b'a,b\n"unterminated,1\n', "Malformed CSV"),
        ("p.csv", b"a,b\n1,\x00\n", "NUL"),
        ("p.xlsx", b"PK\x03\x04", "owner's approval"),
        ("p.xls", b"x", "owner's approval"),
        ("p.txt", b"a\n", "Unsupported file type"),
        ("p", b"a\n", "Unsupported file type"),
    ],
)
def test_unreadable_files_are_file_errors(name: str, data: bytes, message: str) -> None:
    with pytest.raises(FileFormatError, match=message):
        read_table(name, data)


def test_xlsx_is_reported_not_parsed(db: Session) -> None:
    report = run(db, b"PK\x03\x04", name="prices.xlsx")
    assert report.records == ()
    assert "owner's approval" in report.file_errors[0]


# --------------------------------------------------------------------- structure


def test_valid_file_previews_without_writing(db: Session, listing: Security) -> None:
    before = (n(db, PriceObservation), n(db, SourceImport), n(db, AuditEvent))
    report = run(db, GOOD)
    assert report.file_errors == ()
    assert report.summary == {
        "records": 3, "accepted": 3, "duplicate": 0, "conflict": 0, "rejected": 0, "warnings": 0,
    }  # fmt: skip
    assert report.file_sha256 == sha(GOOD)
    assert (n(db, PriceObservation), n(db, SourceImport), n(db, AuditEvent)) == before


def test_reordered_columns_and_omitted_optional_columns(db: Session, listing: Security) -> None:
    data = csv(
        "USD,raw,10.2,2024-03-04,xnas,tst",
        header="Currency, close_basis ,CLOSE,date,exchange_mic,ticker",
    )
    report = run(db, data)
    assert report.summary["accepted"] == 1, report.as_dict()


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("ticker,exchange_mic,date,close,currency", "Missing required columns: close_basis"),
        (HEADER + ",adjclose", "Unknown columns for market_prices.v1: adjclose"),
        (HEADER + ",close", "Duplicate columns: close"),
        (HEADER + ",", "empty column name"),
        ("ticker,exchange_mic,date,close,close_basis,currency,adj_close", "given together"),
    ],
)
def test_header_errors_reject_the_file(
    db: Session, listing: Security, header: str, message: str
) -> None:
    report = run(db, csv("x", header=header))
    assert any(message in e for e in report.file_errors), report.file_errors
    assert report.records == ()


def test_header_only_file(db: Session, listing: Security) -> None:
    report = run(db, csv())
    assert report.file_errors == ("File has a header but no data rows",)


def test_wrong_field_count_rejects_the_row_only(db: Session, listing: Security) -> None:
    data = csv(
        "TST,XNAS,2024-03-04,10,11,9,10.5,raw,,,100,USD",
        "TST,XNAS,2024-03-05,10,11,9,10.5,raw,,,100",
    )
    report = run(db, data)
    assert [r.status for r in report.records] == [Status.ACCEPTED, Status.REJECTED]
    assert codes(report, 2) == ["field_count"]


# --------------------------------------------------------------------- record validation


def one(close: str = "10.5", **cells: str) -> bytes:
    row = {
        "ticker": "TST", "exchange_mic": "XNAS", "date": "2024-03-04", "open": "10",
        "high": "11", "low": "9", "close": close, "close_basis": "raw", "adj_close": "",
        "adj_close_basis": "", "volume": "100", "currency": "USD",
    }  # fmt: skip
    row.update(cells)
    return csv(",".join(row[c] for c in HEADER.split(",")))


@pytest.mark.parametrize(
    ("cells", "code"),
    [
        ({"date": "2024/03/04"}, "invalid_date"),
        ({"date": "04-03-2024"}, "invalid_date"),
        ({"date": "2024-02-30"}, "invalid_date"),
        ({"date": "2024-3-4"}, "invalid_date"),
        ({"date": "2025-01-02"}, "future_date"),
        ({"date": ""}, "missing_value"),
        ({"close": '"1,234.5"'}, "invalid_number"),
        ({"close": "1e3"}, "invalid_number"),
        ({"close": "$10"}, "invalid_number"),
        ({"close": "NaN"}, "invalid_number"),
        ({"close": "-5"}, "non_positive"),
        ({"close": "0"}, "non_positive"),
        ({"close": ""}, "missing_value"),
        ({"open": "abc"}, "invalid_number"),
        ({"volume": "12.5"}, "invalid_integer"),
        ({"volume": "-1"}, "negative_volume"),
        ({"high": "8", "low": "9"}, "high_below_low"),
        ({"currency": "usd"}, "unsupported_currency"),
        ({"currency": "US"}, "unsupported_currency"),
        ({"currency": "GBX"}, "unsupported_currency"),
        ({"currency": "CAD"}, "currency_mismatch"),
        ({"currency": ""}, "missing_value"),
        ({"close_basis": "adjusted"}, "invalid_close_basis"),
        ({"close_basis": "unverified"}, "invalid_close_basis"),
        ({"close_basis": ""}, "missing_value"),
        ({"adj_close": "10.1"}, "missing_value"),
        ({"adj_close_basis": "split_and_dividend_adjusted"}, "unexpected_value"),
        ({"adj_close": "10.1", "adj_close_basis": "total_return"}, "invalid_adj_close_basis"),
        ({"ticker": "NOPE"}, "unknown_listing"),
        ({"exchange_mic": "XNYS"}, "unknown_listing"),
        ({"exchange_mic": ""}, "missing_value"),
        ({"exchange_mic": "NASDAQ"}, "invalid_mic"),
        ({"ticker": ""}, "missing_value"),
    ],
)
def test_invalid_records_are_rejected_with_a_reason(
    db: Session, listing: Security, cells: dict[str, str], code: str
) -> None:
    report = run(db, one(**cells))
    assert report.file_errors == ()
    assert report.records[0].status is Status.REJECTED
    assert code in codes(report), report.as_dict()


def test_close_outside_range_and_weekend_are_warnings(db: Session, listing: Security) -> None:
    report = run(db, one(close="12", date="2024-03-09"))
    assert report.records[0].status is Status.ACCEPTED
    assert codes(report) == ["weekend_date", "close_outside_range"]


def test_identifiers_are_case_insensitive_but_never_ticker_alone(
    db: Session, listing: Security
) -> None:
    assert run(db, one(ticker=" tst ", exchange_mic="xnas")).records[0].status is Status.ACCEPTED


def test_ambiguous_listing_is_rejected(db: Session, listing: Security) -> None:
    other = Company(legal_name="Other Co")
    db.add(other)
    db.flush()
    db.add(Security(company_id=other.id, ticker="TST", exchange_name="NASDAQ GS",
                    exchange_mic="XNAS", trading_currency="USD"))  # fmt: skip
    db.flush()
    assert "ambiguous_listing" in codes(run(db, one()))


def test_listing_without_declared_currency_is_rejected(db: Session, listing: Security) -> None:
    listing.trading_currency = None
    db.flush()
    report = run(db, one())
    assert "listing_currency_not_set" in codes(report)


def test_mixed_price_basis_rejects_the_listing(db: Session, listing: Security) -> None:
    data = csv(
        "TST,XNAS,2024-03-04,,,,10.2,raw,,,,USD",
        "TST,XNAS,2024-03-05,,,,10.4,split_adjusted,,,,USD",
    )
    report = run(db, data)
    assert [r.status for r in report.records] == [Status.REJECTED, Status.REJECTED]
    assert all("mixed_price_basis" in codes(report, i) for i in (1, 2))


def test_mixed_adjusted_basis_rejects_the_listing(db: Session, listing: Security) -> None:
    data = csv(
        "TST,XNAS,2024-03-04,,,,10.2,raw,10.1,split_and_dividend_adjusted,,USD",
        "TST,XNAS,2024-03-05,,,,10.4,raw,10.3,unverified,,USD",
    )
    assert {r.status for r in run(db, data).records} == {Status.REJECTED}


def test_adjusted_close_is_never_used_as_close(db: Session, listing: Security) -> None:
    report = run(db, one(close="", adj_close="10.1", adj_close_basis="split_and_dividend_adjusted"))
    assert report.records[0].status is Status.REJECTED
    assert ("missing_value", "close") in [(i.code, i.column) for i in report.records[0].issues]


def test_in_file_duplicates_and_conflicts(db: Session, listing: Security) -> None:
    data = csv(
        "TST,XNAS,2024-03-04,,,,10.20,raw,,,,USD",
        "TST,XNAS,2024-03-04,,,,10.2,raw,,,,USD",  # same value, different text → duplicate
        "TST,XNAS,2024-03-05,,,,10.40,raw,,,,USD",
        "TST,XNAS,2024-03-05,,,,10.45,raw,,,,USD",
    )
    report = run(db, data)
    assert [r.status for r in report.records] == [
        Status.ACCEPTED, Status.DUPLICATE, Status.CONFLICT, Status.CONFLICT,
    ]  # fmt: skip
    assert "duplicate_in_file" in codes(report, 2)
    assert "conflict_in_file" in codes(report, 3)


def test_validation_is_deterministic(db: Session, listing: Security) -> None:
    data = GOOD + b"TST,XNAS,2024-03-07,1,1,2,1,raw,,,x,EUR\n"
    first = json.dumps(run(db, data).as_dict(), sort_keys=True)
    assert json.dumps(run(db, data).as_dict(), sort_keys=True) == first


# --------------------------------------------------------------------- ingestion


def test_ingest_stores_with_full_provenance(db: Session, listing: Security, tmp_path: Path) -> None:
    result = do_ingest(db, GOOD, tmp_path, source_reference="exported 2024-12-31")
    assert (result.status, result.inserted, result.already_present, result.skipped) == (
        "complete", 3, 0, 0,
    )  # fmt: skip
    record = db.get(SourceImport, result.import_id)
    assert record is not None
    assert (record.kind, record.template_version, record.original_filename) == (
        "csv",
        PRICES,
        "p.csv",
    )
    assert (record.file_sha256, record.bytes, record.provider_code) == (
        sha(GOOD),
        len(GOOD),
        "manual_import",
    )
    assert record.source_name == "Exchange historical export (test)"
    assert record.source_reference == "exported 2024-12-31"
    assert record.summary["accepted"] == 3 and record.skipped == []
    assert load_import_file(db, record.id, tmp_path) == GOOD  # hash-verified original

    rows = current_prices(db, listing.id, "manual_import", "raw")
    assert [(r.trade_date, r.close, r.adj_close, r.volume) for r in rows] == [
        (date(2024, 3, 4), Decimal("10.20"), Decimal("10.10"), 1000),
        (date(2024, 3, 5), Decimal("10.40"), Decimal("10.30"), 1200),
        (date(2024, 3, 6), Decimal("10.65"), Decimal("10.55"), 900),
    ]
    assert {(r.import_id, r.fetch_id, r.currency, r.close_basis) for r in rows} == {
        (record.id, None, "USD", "raw")
    }
    assert [r.import_row for r in rows] == [1, 2, 3]
    assert rows[0].adj_close_basis == "split_and_dividend_adjusted"
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "market_data_imported")).one()
    assert event.entity_id == str(record.id) and event.after["inserted"] == 3  # type: ignore[index]


def test_ingest_is_idempotent(db: Session, listing: Security, tmp_path: Path) -> None:
    do_ingest(db, GOOD, tmp_path)
    again = do_ingest(db, GOOD, tmp_path)
    assert (again.import_id, again.status, again.inserted, again.already_present) == (
        None, "no_new_records", 0, 3,
    )  # fmt: skip
    assert n(db, SourceImport) == 1
    assert len(current_prices(db, listing.id, "manual_import")) == 3
    assert run(db, GOOD).summary["duplicate"] == 3


def test_overlapping_file_adds_only_new_days(
    db: Session, listing: Security, tmp_path: Path
) -> None:
    do_ingest(db, GOOD, tmp_path)
    more = (
        GOOD
        + b"TST,XNAS,2024-03-07,10.6,10.9,10.5,10.8,raw,10.7,split_and_dividend_adjusted,800,USD\n"
    )
    result = do_ingest(db, more, tmp_path)
    assert (result.status, result.inserted, result.already_present) == ("complete", 1, 3)
    assert n(db, SourceImport) == 2


def test_ingest_requires_the_reviewed_hash(db: Session, listing: Security, tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match="validate this exact file"):
        ingest(db, filename="p.csv", data=GOOD, template=PRICES, source_name="s",
               expected_sha256=sha(GOOD + b"\n"), raw_root=tmp_path, today=TODAY)  # fmt: skip
    assert n(db, PriceObservation) == 0


def test_ingest_requires_a_source(db: Session, listing: Security, tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match="source attribution"):
        ingest(db, filename="p.csv", data=GOOD, template=PRICES, source_name="  ",
               expected_sha256=sha(GOOD), raw_root=tmp_path, today=TODAY)  # fmt: skip


def test_unknown_template_is_refused(db: Session) -> None:
    with pytest.raises(ImportRefusedError, match="Unknown template"):
        validate(db, filename="p.csv", data=GOOD, template="prices.v9")


def test_file_errors_refuse_ingestion(db: Session, listing: Security, tmp_path: Path) -> None:
    bad = csv("x", header="ticker,date")
    with pytest.raises(ImportRefusedError, match="File-level errors"):
        do_ingest(db, bad, tmp_path)
    assert (n(db, SourceImport), n(db, PriceObservation)) == (0, 0)
    assert not any(tmp_path.iterdir())  # nothing archived either


def test_rejected_records_refuse_ingestion_unless_partial(
    db: Session, listing: Security, tmp_path: Path
) -> None:
    data = GOOD + b"TST,XNAS,2024-03-07,1,1,1,1,raw,,,,CAD\n"
    with pytest.raises(ImportRefusedError, match="1 record"):
        do_ingest(db, data, tmp_path)
    assert (n(db, SourceImport), n(db, PriceObservation)) == (0, 0)

    result = do_ingest(db, data, tmp_path, allow_partial=True)
    assert (result.status, result.inserted, result.skipped) == ("partial", 3, 1)
    record = db.get(SourceImport, result.import_id)
    assert record is not None and record.status == "partial"
    assert record.skipped[0]["row"] == 4 and record.skipped[0]["status"] == "rejected"
    assert record.skipped[0]["issues"][0]["code"] == "currency_mismatch"


def test_conflicting_stored_observation_is_never_revised(
    db: Session, listing: Security, tmp_path: Path
) -> None:
    do_ingest(db, GOOD, tmp_path)
    revised = GOOD.replace(b"10.40,raw", b"10.41,raw")
    report = run(db, revised)
    assert [r.status for r in report.records] == [
        Status.DUPLICATE, Status.CONFLICT, Status.DUPLICATE,
    ]  # fmt: skip
    assert "differs_from_stored" in codes(report, 2)
    with pytest.raises(ImportRefusedError, match="1 conflicting"):
        do_ingest(db, revised, tmp_path)
    result = do_ingest(db, revised, tmp_path, allow_partial=True)
    assert (result.import_id, result.status, result.skipped) == (None, "no_new_records", 1)
    closes = [r.close for r in current_prices(db, listing.id, "manual_import")]
    assert closes == [Decimal("10.20"), Decimal("10.40"), Decimal("10.65")]


def test_raw_and_split_adjusted_series_stay_separate(
    db: Session, listing: Security, tmp_path: Path
) -> None:
    do_ingest(db, GOOD, tmp_path)
    adjusted = GOOD.replace(b",raw,", b",split_adjusted,").replace(b"10.20,split", b"5.10,split")
    result = do_ingest(db, adjusted, tmp_path)
    assert result.inserted == 3
    raw = current_prices(db, listing.id, "manual_import", "raw")
    split = current_prices(db, listing.id, "manual_import", "split_adjusted")
    assert raw[0].close == Decimal("10.20") and split[0].close == Decimal("5.10")


def test_other_provider_close_difference_is_a_warning(db: Session, listing: Security) -> None:
    fetch = ProviderFetch(provider_code="yahoo_chart", url="https://example.invalid/x")
    db.add(fetch)
    db.flush()
    db.execute(
        insert(PriceObservation).values(
            security_id=listing.id, provider_code="yahoo_chart", fetch_id=fetch.id,
            trade_date=date(2024, 3, 4), close=Decimal("11.00"), currency="USD",
            close_basis="raw", adj_close_basis="unverified",
        )
    )  # fmt: skip
    report = run(db, GOOD)
    assert report.records[0].status is Status.ACCEPTED
    assert "differs_from_other_source" in codes(report, 1)
    assert codes(report, 2) == []


def test_observation_needs_exactly_one_origin(db: Session, listing: Security) -> None:
    values = {
        "security_id": listing.id, "provider_code": "manual_import",
        "trade_date": date(2024, 3, 4), "close": Decimal("1"), "currency": "USD",
        "close_basis": "raw", "adj_close_basis": "unverified",
    }  # fmt: skip
    with pytest.raises(IntegrityError, match="ck_price_observations_origin"), db.begin_nested():
        db.execute(insert(PriceObservation).values(**values))
    with pytest.raises(IntegrityError, match="ck_price_observations_origin"), db.begin_nested():
        db.execute(insert(PriceObservation).values(**values, import_id=uuid.uuid4()))


# --------------------------------------------------------------------- corporate actions

ACTION_HEADER = "ticker,exchange_mic,ex_date,kind,ratio_numerator,ratio_denominator,amount,currency"


def actions(*lines: str) -> bytes:
    return csv(*lines, header=ACTION_HEADER)


def test_corporate_actions_validate_and_ingest(
    db: Session, listing: Security, tmp_path: Path
) -> None:
    data = actions(
        "TST,XNAS,2024-03-04,split,4,1,,",
        "TST,XNAS,2024-03-05,split,1,10,,",
        "TST,XNAS,2024-03-06,cash_dividend,,,0.25,USD",
        "TST,XNAS,2024-03-07,cash_dividend,,,0.30,CAD",
    )
    report = run(db, data, ACTIONS)
    assert report.summary["accepted"] == 4
    assert codes(report, 4) == ["dividend_currency_differs"]
    result = do_ingest(db, data, tmp_path, ACTIONS)
    assert result.inserted == 4
    rows = db.scalars(
        select(CorporateAction).where(CorporateAction.security_id == listing.id)
        .order_by(CorporateAction.ex_date)
    ).all()  # fmt: skip
    assert [
        (r.kind, r.ratio_numerator, r.ratio_denominator, r.amount, r.currency) for r in rows
    ] == [
        ("split", Decimal(4), Decimal(1), None, None),
        ("split", Decimal(1), Decimal(10), None, None),
        ("cash_dividend", None, None, Decimal("0.25"), "USD"),
        ("cash_dividend", None, None, Decimal("0.30"), "CAD"),
    ]
    assert {(r.import_id, r.fetch_id, r.provider_code) for r in rows} == {
        (result.import_id, None, "manual_import")
    }
    assert rows[2].currency_source == "import_file"
    assert do_ingest(db, data, tmp_path, ACTIONS).status == "no_new_records"


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("TST,XNAS,2024-03-04,split,2,2,,", "identity_split"),
        ("TST,XNAS,2024-03-04,split,2,,,", "missing_value"),
        ("TST,XNAS,2024-03-04,split,2,1,0.5,", "unexpected_value"),
        ("TST,XNAS,2024-03-04,split,0,1,,", "non_positive"),
        ("TST,XNAS,2024-03-04,reverse_split,1,10,,", "invalid_kind"),
        ("TST,XNAS,2024-03-04,cash_dividend,,,0.25,", "missing_value"),
        ("TST,XNAS,2024-03-04,cash_dividend,,,0.25,usd", "unsupported_currency"),
        ("TST,XNAS,2024-03-04,cash_dividend,1,1,0.25,USD", "unexpected_value"),
        ("TST,XNAS,2024-03-04,cash_dividend,,,-0.25,USD", "non_positive"),
        ("TST,XNAS,2024-13-04,cash_dividend,,,0.25,USD", "invalid_date"),
    ],
)
def test_invalid_corporate_actions(db: Session, listing: Security, line: str, code: str) -> None:
    report = run(db, actions(line), ACTIONS)
    assert report.records[0].status is Status.REJECTED
    assert code in codes(report), report.as_dict()


def test_conflicting_split_is_reported(db: Session, listing: Security, tmp_path: Path) -> None:
    do_ingest(db, actions("TST,XNAS,2024-03-04,split,4,1,,"), tmp_path, ACTIONS)
    report = run(db, actions("TST,XNAS,2024-03-04,split,3,1,,"), ACTIONS)
    assert report.records[0].status is Status.CONFLICT


# --------------------------------------------------------------------- listing currency


def test_set_trading_currency_once_with_audit(db: Session, listing: Security) -> None:
    listing.trading_currency = None
    db.flush()
    with pytest.raises(ListingError, match="reason"):
        set_trading_currency(db, listing, "USD", reason=" ")
    with pytest.raises(ListingError, match="ISO 4217"):
        set_trading_currency(db, listing, "usd", reason="r")
    assert set_trading_currency(db, listing, "USD", reason="Nasdaq listing trades in USD")
    assert not set_trading_currency(db, listing, "USD", reason="again")
    with pytest.raises(ListingError, match="not supported"):
        set_trading_currency(db, listing, "CAD", reason="r")
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "listing_currency_set")).one()
    assert (
        event.after == {"trading_currency": "USD"}
        and event.reason == "Nasdaq listing trades in USD"
    )


# --------------------------------------------------------------------- CLI (read-only paths)


def test_cli_validate_prints_report_and_writes_nothing(
    migrated_db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = tmp_path / "prices.csv"
    file.write_bytes(one(ticker="ZZZQ"))
    code = cli.main(["market-import-validate", "--template", PRICES, "--file", str(file), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["file_sha256"] == sha(file.read_bytes())
    assert report["summary"]["rejected"] == 1
    assert report["records"][0]["issues"][0]["code"] == "unknown_listing"


def test_cli_stdin_needs_a_filename(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["market-import-validate", "--template", PRICES, "--file", "-"]) == 1
    assert "--filename is required" in capsys.readouterr().err
