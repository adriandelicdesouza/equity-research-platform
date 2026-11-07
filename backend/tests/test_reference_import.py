"""Damodaran reference datasets: templates, validation, versioned ingestion, CLI (D58).

All values are synthetic (invented for the tests, not Damodaran's published numbers) and
live only in the disposable ``erp_test`` database, rolled back after each test.
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

import pytest
from erp_backend import cli
from erp_backend.config import get_settings
from erp_backend.ingestion import archive
from erp_backend.models import AuditEvent, ReferenceDataset, ReferenceValue, SourceImport
from erp_backend.reference.damodaran import (
    ImportRefusedError,
    Status,
    dataset_records,
    ingest,
    validate,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

TODAY = date(2024, 12, 31)
EDITION = date(2024, 1, 5)
CRP = "damodaran_country_risk.v1"
ERP = "damodaran_implied_erp.v1"
BETAS = "damodaran_industry_betas.v1"
SPREADS = "damodaran_rating_spreads.v1"

CRP_HEADER = (
    "country_name,country_iso2,region,moodys_rating,default_spread,country_risk_premium,"
    "total_erp,corporate_tax_rate"
)
BETAS_HEADER = (
    "industry_name,number_of_firms,beta,de_ratio,effective_tax_rate,unlevered_beta,"
    "cash_firm_value,unlevered_beta_cash_corrected"
)


def csv(header: str, *lines: str) -> bytes:
    return ("\n".join([header, *lines]) + "\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CRP_GOOD = csv(
    CRP_HEADER,
    "United States,US,North America,Aaa,0.00%,0.00%,4.60%,25.00%",
    "Canada,CA,North America,Aaa,0.00%,0.00%,4.60%,26.50%",
    "Ruritania,DE,Western Europe,Aa1,0.50%,0.70%,5.30%,30.00%",
)
ERP_GOOD = csv(
    "as_of_date,index,implied_erp,t_bond_rate,method",
    "2023-12-01,SP500,4.60%,3.88%,Trailing 12-month cash yield",
    "2024-01-01,SP500,4.55%,4.02%,Trailing 12-month cash yield",
)
BETAS_GOOD = csv(
    BETAS_HEADER,
    "Widgets,40,1.10,25.00%,12.00%,0.92,4.00%,0.96",
    "Gadgets (Synthetic),12,0.80,10.00%,8.00%,0.74,2.50%,0.76",
)
SPREADS_GOOD = csv(
    "coverage_min,coverage_max,rating,spread",
    "-100000,0.499999,D,19.00%",
    "0.5,1.999999,B,4.50%",
    "2,4.999999,BBB,1.80%",
    "5,100000,AAA,0.60%",
)


def run_validate(db: Session, data: bytes, template: str = CRP, **kw: object):  # type: ignore[no-untyped-def]
    return validate(
        db,
        filename="data.csv",
        data=data,
        template=template,
        edition_date=kw.pop("edition_date", EDITION),
        today=TODAY,
        **kw,  # type: ignore[arg-type]
    )


def run_ingest(db: Session, data: bytes, root: Path, template: str = CRP, **kw: object):  # type: ignore[no-untyped-def]
    return ingest(
        db,
        filename="data.csv",
        data=data,
        template=template,
        edition_date=kw.pop("edition_date", EDITION),  # type: ignore[arg-type]
        source_name="Synthetic Damodaran-format test file",
        expected_sha256=kw.pop("expected_sha256", sha(data)),  # type: ignore[arg-type]
        raw_root=root,
        today=TODAY,
        **kw,  # type: ignore[arg-type]
    )


def statuses(report: object) -> list[str]:
    return [r.status.value for r in report.records]  # type: ignore[attr-defined]


def issue_codes(report: object) -> set[str]:
    return {i.code for r in report.records for i in r.issues}  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- validation


def test_valid_country_file_validates_without_persisting(db: Session) -> None:
    before = db.scalar(select(func.count()).select_from(ReferenceDataset))
    report = run_validate(db, CRP_GOOD)
    assert report.file_errors == ()
    assert statuses(report) == ["accepted"] * 3
    assert report.outcome == "new_dataset"
    assert report.lineage.key == "country_risk|2024-01-05|-|-"
    assert report.content_sha256 is not None
    assert db.scalar(select(func.count()).select_from(ReferenceDataset)) == before
    assert db.scalar(select(func.count()).select_from(SourceImport)) == 0


def test_every_template_has_a_valid_example(db: Session) -> None:
    assert statuses(run_validate(db, ERP_GOOD, ERP)) == ["accepted"] * 2
    assert statuses(run_validate(db, BETAS_GOOD, BETAS, region="global")) == ["accepted"] * 2
    report = run_validate(db, SPREADS_GOOD, SPREADS, variant="large_cap")
    assert statuses(report) == ["accepted"] * 4
    assert issue_codes(report) == set()


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("Ruritania,DE,,,0.50,0.70%,5.30%,", "percent_sign_required"),
        ("Ruritania,DE,,,0.5%,0.70%,5.30%,", None),
        ("Ruritania,DE,,,,0.70%,5.30%,", None),
        ("Ruritania,DE,,,,,5.30%,", "missing_value"),
        ("Ruritania,DE,,,,-0.70%,5.30%,", "out_of_range"),
        ("Ruritania,DE,,,,0.70%,0.50%,", "total_below_crp"),
        ("Ruritania,DE,,,,0.7 %,5.30%,", None),
        ("Ruritania,DE,,,,0.70%,5,30%,", "field_count"),
        ("Ruritania,DE,,,,1e0%,5.30%,", "invalid_number"),
    ],
)
def test_country_cell_validation(db: Session, line: str, code: str | None) -> None:
    report = run_validate(db, csv(CRP_HEADER, line))
    record = report.records[0]
    if code is None:
        assert record.status is Status.ACCEPTED, record.issues
    else:
        assert record.status is Status.REJECTED
        assert code in {i.code for i in record.issues}


def test_countries_without_an_iso_code_are_unmapped(db: Session) -> None:
    report = run_validate(
        db,
        csv(
            CRP_HEADER,
            "Western Europe,,,,,0.80%,5.40%,",
            "Atlantis,XA,,,,2.00%,6.60%,",
            "Canada,ca,,Aaa,,0.00%,4.60%,",
        ),
    )
    assert statuses(report) == ["unmapped", "unmapped", "accepted"]
    assert {"unmapped_country", "unknown_country_code"} <= issue_codes(report)
    assert report.records[2].record_key == "CA"


def test_country_cross_checks_are_warnings(db: Session) -> None:
    report = run_validate(
        db,
        csv(
            CRP_HEADER,
            "United States,US,,Aaa,,0.30%,4.90%,",
            "Canada,CA,,Aaa,,0.00%,4.60%,",
            "Ruritania,DE,,,,0.70%,5.30%,",
            "Freedonia,FR,,,,1.00%,5.90%,",
        ),
    )
    assert statuses(report) == ["accepted"] * 4
    by_key = {r.record_key: {i.code for i in r.issues} for r in report.records}
    assert "nonzero_crp_aaa" in by_key["US"]
    assert "inconsistent_mature_erp" in by_key["FR"]  # 5.90 − 1.00 = 4.90 ≠ 4.60
    assert "inconsistent_mature_erp" not in by_key["DE"]


def test_in_file_duplicates_and_conflicts(db: Session) -> None:
    report = run_validate(
        db,
        csv(
            CRP_HEADER,
            "Canada,CA,,Aaa,,0.00%,4.60%,",
            "Canada,CA,,Aaa,,0.00%,4.60%,",
            "Ruritania,DE,,,,0.70%,5.30%,",
            "Ruritania (alt),DE,,,,0.75%,5.35%,",
        ),
    )
    assert statuses(report) == ["accepted", "duplicate", "conflicting", "conflicting"]
    assert [r.storable for r in report.records] == [True, False, False, False]
    assert len(report.skipped) == 2  # the in-file conflict; the exact repeat is not "skipped"


def test_implied_erp_rules(db: Session) -> None:
    report = run_validate(
        db,
        csv(
            "as_of_date,index,implied_erp",
            "2024-01-01,sp500,4.55%",
            "2024-01-01,NASDAQ,5.00%",
            "2024-02-01,SP500,4.50%",
            "2023-11-01,SP500,0.455",
            "2023-10-01,SP500,30%",
            "2023-09-01,SP500,12%",
        ),
        ERP,
    )
    assert statuses(report) == [
        "accepted",
        "unmapped",
        "rejected",
        "rejected",
        "rejected",
        "accepted",
    ]
    codes = [{i.code for i in r.issues} for r in report.records]
    assert report.records[0].record_key == "SP500|2024-01-01"
    assert "unknown_index" in codes[1]
    assert "after_edition" in codes[2]  # 2024-02-01 is after the 2024-01-05 edition
    assert "percent_sign_required" in codes[3]
    assert "out_of_range" in codes[4]
    assert "unusual_value" in codes[5]


def test_industry_beta_rules(db: Session) -> None:
    report = run_validate(
        db,
        csv(
            BETAS_HEADER,
            "Widgets,40,1.10,25%,12%,0.92,4%,0.96",
            "Odd,5,0.70,10%,8%,0.90,,",
            "Empty,0,1.00,,,0.90,,",
            "Percent beta,3,,,,90%,,",
            "Huge,3,,,,12,,",
            "Fractional firms,2.5,,,,0.9,,",
        ),
        BETAS,
        region="us",
    )
    assert statuses(report) == [
        "accepted",
        "accepted",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
    ]
    codes = [{i.code for i in r.issues} for r in report.records]
    assert "unlevered_above_levered" in codes[1]
    assert "no_firms" in codes[2]
    assert "unexpected_percent" in codes[3]
    assert "out_of_range" in codes[4]
    assert "invalid_integer" in codes[5]


def test_rating_spread_rules(db: Session) -> None:
    report = run_validate(
        db,
        csv(
            "coverage_min,coverage_max,rating,spread",
            "-100000,0.499999,D,19.00%",
            "0.4,1.999999,B,4.50%",
            "3,4.999999,BBB,5.00%",
            "5,100000,AAA,0.60%",
            "6,5,AA,0.70%",
            "7,8,Baa2,1.00%",
        ),
        SPREADS,
        variant="small_cap",
    )
    assert statuses(report) == [
        "rejected",
        "rejected",
        "accepted",
        "accepted",
        "rejected",
        "unmapped",
    ]
    codes = [{i.code for i in r.issues} for r in report.records]
    assert "overlapping_range" in codes[0] and "overlapping_range" in codes[1]
    assert "empty_range" in codes[4]
    assert "unknown_rating" in codes[5]
    assert "coverage_gap" in codes[2]  # 1.999999 → 3


def test_lineage_parameters_are_checked(db: Session) -> None:
    with pytest.raises(ImportRefusedError, match="needs --region"):
        run_validate(db, BETAS_GOOD, BETAS)
    with pytest.raises(ImportRefusedError, match="needs --region"):
        run_validate(db, BETAS_GOOD, BETAS, region="canada")
    with pytest.raises(ImportRefusedError, match="does not take a region"):
        run_validate(db, CRP_GOOD, region="us")
    with pytest.raises(ImportRefusedError, match="needs --variant"):
        run_validate(db, SPREADS_GOOD, SPREADS)
    with pytest.raises(ImportRefusedError, match="does not take a variant"):
        run_validate(db, CRP_GOOD, variant="large_cap")
    with pytest.raises(ImportRefusedError, match="future"):
        run_validate(db, CRP_GOOD, edition_date=date(2025, 1, 1))
    with pytest.raises(ImportRefusedError, match="Unknown template"):
        run_validate(db, CRP_GOOD, "damodaran_everything.v1")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"country_name,total_erp\nX,1%\n", "Missing required columns"),
        (csv(CRP_HEADER + ",extra", "a,b,c,d,e,f,g,h,i"), "Unknown columns"),
        (csv("country_name,country_name,country_risk_premium,total_erp"), "Duplicate"),
        (csv(CRP_HEADER), "no data rows"),
    ],
)
def test_file_level_errors(db: Session, data: bytes, message: str) -> None:
    report = run_validate(db, data)
    assert report.file_errors and message in " ".join(report.file_errors)
    assert report.outcome == "file_errors"


def test_xlsx_is_refused_until_a_dependency_is_approved(db: Session) -> None:
    report = validate(
        db,
        filename="ctryprem.xlsx",
        data=b"PK\x03\x04",
        template=CRP,
        edition_date=EDITION,
        today=TODAY,
    )
    assert "XLSX import is not available" in report.file_errors[0]


def test_content_hash_ignores_row_order_and_number_formatting(db: Session) -> None:
    a = run_validate(db, CRP_GOOD)
    lines = CRP_GOOD.decode().strip().split("\n")
    reordered = csv(lines[0], lines[3], lines[1], lines[2].replace("4.60%", "4.6%"))
    b = run_validate(db, reordered)
    assert a.file_sha256 != b.file_sha256
    assert a.content_sha256 == b.content_sha256


# --------------------------------------------------------------------------- ingestion


def test_ingest_creates_version_one_with_full_provenance(db: Session, tmp_path: Path) -> None:
    result = run_ingest(db, CRP_GOOD, tmp_path, source_reference="ctryprem, synthetic copy")
    assert (result.status, result.version_no, result.stored_records) == ("created", 1, 3)
    dataset = db.get(ReferenceDataset, result.dataset_id)
    assert dataset is not None
    assert (dataset.dataset_kind, dataset.edition_date, dataset.supersedes_id) == (
        "country_risk",
        EDITION,
        None,
    )
    record = db.get(SourceImport, dataset.import_id)
    assert record is not None
    assert (record.provider_code, record.template_version, record.status) == (
        "damodaran",
        CRP,
        "complete",
    )
    assert record.file_sha256 == sha(CRP_GOOD)
    assert archive.load(tmp_path, record.stored_path, record.file_sha256) == CRP_GOOD
    values = dataset_records(db, dataset.id)
    assert values["DE"]["country_risk_premium"] == ("percent", Decimal("0.70"))
    assert values["DE"]["moodys_rating"] == ("text", "Aa1")
    stored = db.scalars(
        select(ReferenceValue).where(
            ReferenceValue.dataset_id == dataset.id,
            ReferenceValue.field == "total_erp",
            ReferenceValue.record_key == "US",
        )
    ).one()
    assert (stored.import_row, stored.locator, stored.unit) == (1, "line 2", "percent")
    event = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "reference_dataset_imported",
            AuditEvent.entity_id == str(dataset.id),
        )
    ).one()
    assert event.after["file_sha256"] == sha(CRP_GOOD)  # type: ignore[index]


def test_reimport_is_idempotent(db: Session, tmp_path: Path) -> None:
    first = run_ingest(db, CRP_GOOD, tmp_path)
    again = run_ingest(db, CRP_GOOD, tmp_path)
    assert again.status == "identical_to_current"
    assert again.dataset_id == first.dataset_id and again.import_id is None
    assert run_validate(db, CRP_GOOD).outcome == "identical_to_current"
    assert statuses(run_validate(db, CRP_GOOD)) == ["duplicate"] * 3
    assert db.scalar(select(func.count()).select_from(SourceImport)) == 1


def test_changed_file_never_overwrites_silently(db: Session, tmp_path: Path) -> None:
    first = run_ingest(db, CRP_GOOD, tmp_path)
    changed = csv(
        CRP_HEADER,
        "United States,US,North America,Aaa,0.00%,0.00%,4.60%,25.00%",
        "Ruritania,DE,Western Europe,Aa1,0.50%,0.80%,5.40%,30.00%",
        "Freedonia,FR,,,,1.00%,5.60%,",
    )
    report = run_validate(db, changed)
    assert statuses(report) == ["duplicate", "conflicting", "accepted"]
    assert report.removed_keys == ("CA",)
    assert report.outcome == "new_version_requires_supersede"
    with pytest.raises(ImportRefusedError, match="never overwrite"):
        run_ingest(db, changed, tmp_path)
    with pytest.raises(ImportRefusedError, match="--reason"):
        run_ingest(db, changed, tmp_path, supersede=True, reason="short")

    second = run_ingest(
        db, changed, tmp_path, supersede=True, reason="Corrected transcription of DE row"
    )
    assert (second.status, second.version_no) == ("superseded", 2)
    v2 = db.get(ReferenceDataset, second.dataset_id)
    assert v2 is not None and v2.supersedes_id == first.dataset_id
    # The first version is preserved exactly.
    old = dataset_records(db, first.dataset_id)  # type: ignore[arg-type]
    assert old["DE"]["country_risk_premium"] == ("percent", Decimal("0.70"))
    assert "CA" in old
    new = dataset_records(db, v2.id)
    assert new["DE"]["country_risk_premium"] == ("percent", Decimal("0.80"))
    assert set(new) == {"US", "DE", "FR"}
    # Re-validating the original file now compares with version 2.
    assert run_validate(db, CRP_GOOD).current_version["version_no"] == 2  # type: ignore[index]


def test_supersede_without_a_current_version_is_refused(db: Session, tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match="Nothing to supersede"):
        run_ingest(db, CRP_GOOD, tmp_path, supersede=True, reason="no reason at all")


def test_editions_and_regions_are_separate_lineages(db: Session, tmp_path: Path) -> None:
    run_ingest(db, BETAS_GOOD, tmp_path, BETAS, region="us")
    other_region = run_ingest(db, BETAS_GOOD, tmp_path, BETAS, region="global")
    other_edition = run_ingest(
        db, BETAS_GOOD, tmp_path, BETAS, region="us", edition_date=date(2024, 7, 1)
    )
    assert other_region.version_no == other_edition.version_no == 1
    assert other_region.status == other_edition.status == "created"


def test_ingest_refusals_write_nothing(db: Session, tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match="not the reviewed"):
        run_ingest(db, CRP_GOOD, tmp_path, expected_sha256="0" * 64)
    with pytest.raises(ImportRefusedError, match="source attribution"):
        ingest(
            db,
            filename="a.csv",
            data=CRP_GOOD,
            template=CRP,
            edition_date=EDITION,
            source_name="  ",
            expected_sha256=sha(CRP_GOOD),
            raw_root=tmp_path,
            today=TODAY,
        )
    with pytest.raises(ImportRefusedError, match="File-level errors"):
        run_ingest(db, csv(CRP_HEADER), tmp_path)
    mixed = csv(CRP_HEADER, "Canada,CA,,Aaa,,0.00%,4.60%,", "Africa,,,,,3.00%,7.60%,")
    with pytest.raises(ImportRefusedError, match="would be skipped"):
        run_ingest(db, mixed, tmp_path)
    assert db.scalar(select(func.count()).select_from(SourceImport)) == 0
    assert not any(tmp_path.iterdir())


def test_allow_partial_stores_valid_records_and_keeps_skipped(db: Session, tmp_path: Path) -> None:
    mixed = csv(CRP_HEADER, "Canada,CA,,Aaa,,0.00%,4.60%,", "Africa,,,,,3.00%,7.60%,")
    result = run_ingest(db, mixed, tmp_path, allow_partial=True)
    assert result.stored_records == 1
    record = db.get(SourceImport, result.import_id)
    assert record is not None and record.status == "partial"
    assert record.skipped[0]["status"] == "unmapped"


def test_app_role_cannot_modify_reference_rows(db: Session, tmp_path: Path) -> None:
    run_ingest(db, CRP_GOOD, tmp_path)
    for statement in (
        "UPDATE ref.reference_values SET value = 0",
        "DELETE FROM ref.reference_datasets",
    ):
        with pytest.raises(DBAPIError, match="permission denied"), db.begin_nested():
            db.execute(text(statement))


def test_reference_tables_are_append_only_for_the_owner(owner_engine: Engine) -> None:
    # Own rows in the owner's own transaction (never waits on another test's locks).
    with owner_engine.connect() as conn, conn.begin():
        import_id, dataset_id = uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO source.imports (id, provider_code, kind, template_version, "
                "original_filename, file_sha256, stored_path, bytes, source_name, status, "
                "summary, skipped) VALUES (:i, 'damodaran', 'csv', :t, 'x.csv', :h, 'p', 1, "
                "'synthetic', 'complete', '{}', '[]')"
            ),
            {"i": import_id, "t": CRP, "h": "b" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO ref.reference_datasets (id, provider_code, dataset_kind, "
                "template_version, edition_date, lineage_key, version_no, import_id, "
                "record_count, content_sha256) VALUES (:d, 'damodaran', 'country_risk', :t, "
                "'2024-01-05', 'country_risk|2024-01-05|-|-', 1, :i, 1, :h)"
            ),
            {"d": dataset_id, "i": import_id, "t": CRP, "h": "c" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO ref.reference_values (dataset_id, record_key, field, value, unit, "
                "import_row, locator) VALUES (:d, 'US', 'total_erp', 4.6, 'percent', 1, 'l2')"
            ),
            {"d": dataset_id},
        )
        for statement in (
            "UPDATE ref.reference_values SET value = 0",
            "DELETE FROM ref.reference_values",
            "UPDATE ref.reference_datasets SET reason = 'x'",
            "DELETE FROM ref.reference_datasets",
            "TRUNCATE ref.reference_values",
        ):
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text(statement))
            nested.rollback()
        conn.rollback()


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"version_no": 2}, "supersedes"),
        ({"lineage_key": "country_risk|2024-01-06|-|-"}, "lineage_key"),
        ({"region": "us"}, "region"),
        ({"record_count": 0}, "record_count"),
    ],
)
def test_dataset_constraints(
    db: Session, tmp_path: Path, changes: dict[str, object], constraint: str
) -> None:
    result = run_ingest(db, CRP_GOOD, tmp_path)
    base = db.get(ReferenceDataset, result.dataset_id)
    assert base is not None
    row = {
        "id": uuid.uuid4(),
        "provider_code": "damodaran",
        "dataset_kind": "country_risk",
        "template_version": CRP,
        "edition_date": EDITION,
        "region": None,
        "variant": None,
        "lineage_key": "country_risk|2024-01-05|-|-",
        "version_no": 1,
        "supersedes_id": None,
        "import_id": base.import_id,
        "record_count": 3,
        "content_sha256": "a" * 64,
        **changes,
    }
    with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
        db.execute(ReferenceDataset.__table__.insert().values(**row))  # type: ignore[attr-defined]


def test_value_kind_constraint(db: Session, tmp_path: Path) -> None:
    result = run_ingest(db, CRP_GOOD, tmp_path)
    with pytest.raises(IntegrityError, match="value_kind"), db.begin_nested():
        db.add(
            ReferenceValue(
                dataset_id=result.dataset_id,
                record_key="US",
                field="x",
                value=Decimal(1),
                value_text="1",
                unit="percent",
                import_row=1,
                locator="line 2",
            )
        )
        db.flush()


# --------------------------------------------------------------------------- CLI


@contextmanager
def shared(db: Session) -> Iterator[Session]:
    """The test session for a CLI command: closing it would roll back the test data."""
    yield db


def test_cli_validate_import_list_show(
    db: Session,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("erp_backend.db.new_session", lambda: shared(db))
    patched = get_settings().model_copy(update={"raw_root": tmp_path})
    monkeypatch.setattr("erp_backend.config.get_settings", lambda: patched)
    file = tmp_path / "spreads.csv"
    file.write_bytes(SPREADS_GOOD)
    common = [
        "--template",
        SPREADS,
        "--file",
        str(file),
        "--edition-date",
        "2024-01-05",
        "--variant",
        "large_cap",
    ]
    assert cli.main(["reference-import-validate", *common, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "new_dataset" and report["summary"]["accepted"] == 4

    assert (
        cli.main(
            ["reference-import", *common, "--source", "synthetic", "--expect-sha256", "0" * 64]
        )
        == 1
    )
    assert "refused (nothing written)" in capsys.readouterr().err
    assert (
        cli.main(
            [
                "reference-import",
                *common,
                "--source",
                "synthetic",
                "--expect-sha256",
                sha(SPREADS_GOOD),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "version 1 (created): 4 record(s)" in out
    dataset_id = out.split()[1]
    assert (
        cli.main(
            [
                "reference-import",
                *common,
                "--source",
                "synthetic",
                "--expect-sha256",
                sha(SPREADS_GOOD),
            ]
        )
        == 0
    )
    assert "identical to version 1" in capsys.readouterr().out

    assert cli.main(["reference-dataset-list", "--kind", "rating_spreads"]) == 0
    assert "rating_spreads|2024-01-05|-|large_cap" in capsys.readouterr().out
    assert cli.main(["reference-dataset-show", dataset_id, "--values"]) == 0
    out = capsys.readouterr().out
    assert "BBB" in out and "spread" in out and "1.80 percent" in out
    assert cli.main(["reference-dataset-show", "not-a-uuid"]) == 1
    assert cli.main(["reference-import-validate", *common[:-2], "--json"]) == 1
    assert "needs --variant" in capsys.readouterr().err
    assert (
        cli.main(
            [
                "reference-import-validate",
                "--template",
                CRP,
                "--file",
                "-",
                "--edition-date",
                "2024-01-05",
            ]
        )
        == 1
    )
    assert "--filename is required" in capsys.readouterr().err
