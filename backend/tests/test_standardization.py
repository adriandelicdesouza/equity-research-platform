"""Standard line-item mappings (loader/validation) and per-period concept resolution."""

from __future__ import annotations

import importlib.util
import random
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import Policy, select
from erp_backend.standardization import mapping as m
from erp_backend.standardization.resolve import (
    StandardValue,
    ValueStatus,
    annual_coverage,
    expected_unit,
    resolve,
    taxonomy_for_standard,
)
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "mappings"
MIGRATIONS = REPO / "backend" / "alembic" / "versions"
PINNED = {
    "v1": ("0005_reference_mappings.py", "V1_SHA256"),
    "v2": ("0007_validation_and_mapping_v2.py", "V2_SHA256"),
}


@pytest.fixture(scope="module")
def v1() -> m.MappingSet:
    return m.load(ROOT, "v1")


# ---- The committed v1 mapping ----------------------------------------------------------------


def test_v1_loads_and_covers_the_release_1_taxonomy(v1: m.MappingSet) -> None:
    assert v1.taxonomies == ("ifrs-full", "us-gaap")
    for code in (
        "IS.REVENUE", "IS.EBIT", "IS.NET_INCOME", "IS.EPS_DILUTED", "IS.SHARES_DILUTED_WA",
        "BS.CASH", "BS.TOTAL_ASSETS", "BS.TOTAL_EQUITY", "BS.LT_DEBT", "BS.SHARES_OUTSTANDING",
        "CF.CFO", "CF.CAPEX", "CF.DIVIDENDS", "CF.NET_CHANGE_CASH",
    ):  # fmt: skip
        assert code in v1.line_items
        assert v1.for_line_item("us-gaap", code), code
    # Blueprint §4.4 example order for revenue.
    assert [c.concept for c in v1.for_line_item("us-gaap", "IS.REVENUE")] == [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ]
    capex = v1.for_line_item("us-gaap", "CF.CAPEX")[0]
    assert (capex.concept, capex.sign) == ("PaymentsToAcquirePropertyPlantAndEquipment", -1)
    assert not v1.for_line_item("us-gaap", "IS.EBITDA")  # derived only
    # LongtermBorrowings includes the current portion; mapping it would double-count.
    assert "LongtermBorrowings" not in {
        c.concept for c in v1.for_line_item("ifrs-full", "BS.LT_DEBT")
    }


def test_v1_subtotals_reference_known_items(v1: m.MappingSet) -> None:
    gross = v1.line_items["IS.GROSS_PROFIT"]
    assert gross.subtotal == ((1, "IS.REVENUE"), (-1, "IS.COGS"))
    assert v1.line_items["IS.EBITDA"].derived_only
    assert v1.line_items["IS.SELLING_MARKETING"].parent == "IS.SGA"


@pytest.mark.parametrize("version", sorted(PINNED))
def test_fingerprints_are_pinned_by_their_migrations(version: str) -> None:
    filename, constant = PINNED[version]
    spec = importlib.util.spec_from_file_location(f"migration_{version}", MIGRATIONS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loaded = m.load(ROOT, version)
    assert loaded.sha256 == getattr(module, constant) == m.content_sha256(ROOT, version)


def test_v2_adds_free_cash_flow_and_keeps_v1_candidates(v1: m.MappingSet) -> None:
    v2 = m.load(ROOT, "v2")
    assert set(v2.line_items) - set(v1.line_items) == {"CF.FCF"}
    assert v2.line_items["CF.FCF"].subtotal == ((1, "CF.CFO"), (1, "CF.CAPEX"))
    assert v2.line_items["CF.FCF"].derived_only
    assert v2.candidates == v1.candidates


# ---- Validation ------------------------------------------------------------------------------

LINE_ITEMS = """
line_items:
  - {code: IS.REVENUE, label: Revenue, period: duration, value_type: flow, sign: natural}
  - {code: IS.COGS, label: Cost, period: duration, value_type: flow, sign: expense}
  - {code: BS.CASH, label: Cash, period: instant, value_type: stock, sign: natural}
"""


def _write(tmp: Path, line_items: str = LINE_ITEMS, us: str = "", ifrs: str = "") -> Path:
    d = tmp / "v1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "line_items.yaml").write_text(line_items)
    (d / "us-gaap.yaml").write_text(
        us or "taxonomy: us-gaap\nmappings:\n  IS.REVENUE: [Revenues]\n"
    )
    (d / "ifrs-full.yaml").write_text(ifrs or "taxonomy: ifrs-full\nmappings: {}\n")
    return tmp


def test_minimal_mapping_loads(tmp_path: Path) -> None:
    ms = m.load(_write(tmp_path), "v1")
    assert [c.concept for c in ms.for_line_item("us-gaap", "IS.REVENUE")] == ["Revenues"]
    assert ms.line_items["BS.CASH"].statement == "BS"


def _item(fields: str, code: str = "IS.X", rest: str = "value_type: flow, sign: natural") -> str:
    """LINE_ITEMS plus one extra line item (period: duration unless overridden)."""
    return f"{LINE_ITEMS}  - {{code: {code}, label: x, period: duration, {rest}{fields}}}\n"


def _us(mappings: str) -> str:
    return f"taxonomy: us-gaap\nmappings:\n  {mappings}\n"


@pytest.mark.parametrize(
    ("line_items", "us", "message"),
    [
        (LINE_ITEMS + LINE_ITEMS.split("\n", 2)[2], "", "duplicate code"),
        (_item("", code="is.bad"), "", "invalid code"),
        (_item(", parent: IS.NOPE"), "", "unknown parent"),
        (_item(", subtotal: [+IS.NOPE]"), "", "unknown IS.NOPE"),
        (_item(", subtotal: [IS.REVENUE]"), "", "must look like"),
        (_item("", code="BS.X", rest="value_type: stock, sign: natural"), "", "inconsistent"),
        (_item("", rest="value_type: flow, sign: up"), "", "sign must be one of"),
        (_item(", derived_only: true"), "", "need a subtotal"),
        (_item(", extra: 1"), "", "unknown fields"),
        (LINE_ITEMS, _us("IS.NOPE: [Revenues]"), "unknown line item"),
        (LINE_ITEMS, _us("IS.REVENUE: [Revenues, Revenues]"), "twice"),
        (LINE_ITEMS, _us("IS.REVENUE: [{concept: Revenues, sign: 2}]"), "sign must be 1 or -1"),
        (LINE_ITEMS, _us("IS.REVENUE: [{concept: Revenues, sign: true}]"), "sign must be 1"),
        (LINE_ITEMS, _us("IS.REVENUE: [us-gaap:Revenues]"), "invalid concept"),
        (LINE_ITEMS, _us("IS.REVENUE: []"), "non-empty"),
        (LINE_ITEMS, "taxonomy: ifrs-full\nmappings: {}\n", "taxonomy: us-gaap"),
        (LINE_ITEMS, "taxonomy: us-gaap\nmappings: [\n", "invalid YAML"),
    ],
)
def test_invalid_mappings_are_rejected(
    tmp_path: Path, line_items: str, us: str, message: str
) -> None:
    with pytest.raises(m.MappingError, match=message):
        m.load(_write(tmp_path, line_items, us), "v1")


def test_derived_only_items_cannot_be_mapped(tmp_path: Path) -> None:
    items = LINE_ITEMS + (
        "  - {code: IS.GP, label: GP, period: duration, value_type: flow, sign: natural,"
        " subtotal: [+IS.REVENUE, -IS.COGS], derived_only: true}\n"
    )
    us = "taxonomy: us-gaap\nmappings:\n  IS.GP: [GrossProfit]\n"
    with pytest.raises(m.MappingError, match="derived_only"):
        m.load(_write(tmp_path, items, us), "v1")


def test_missing_files_and_bad_versions_are_rejected(tmp_path: Path) -> None:
    root = _write(tmp_path)
    (root / "v1" / "ifrs-full.yaml").unlink()
    with pytest.raises(m.MappingError, match=r"missing ifrs-full\.yaml"):
        m.load(root, "v1")
    with pytest.raises(m.MappingError, match="Invalid mapping version"):
        m.load(root, "../v1")


def test_fingerprint_changes_with_any_edit(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "v1", tmp_path / "v1")
    before = m.content_sha256(tmp_path, "v1")
    path = tmp_path / "v1" / "us-gaap.yaml"
    path.write_text(path.read_text() + "\n")
    assert m.content_sha256(tmp_path, "v1") != before


# ---- Resolution ------------------------------------------------------------------------------

FY23 = (date(2023, 1, 1), date(2023, 12, 31))
FY24 = (date(2024, 1, 1), date(2024, 12, 31))


def fact(
    concept: str,
    value: str,
    period: tuple[date | None, date],
    *,
    unit: str = "USD",
    filed: date = date(2025, 2, 1),
    accn: str = "0000000001-25-000001",
    form: str = "10-K",
    taxonomy: str = "us-gaap",
) -> RawFact:
    start, end = period
    return RawFact(
        taxonomy, concept, unit, Decimal(value), start, end, None, None, form, filed, accn, None
    )


def _resolve(
    facts: list[RawFact], v1: m.MappingSet, policy: Policy = Policy.LATEST_REPORTED
) -> dict[tuple[str, date], StandardValue]:
    values = resolve(select(facts), v1, taxonomy="us-gaap", currency="USD", policy=policy)
    return {(v.line_item, v.period_end): v for v in values}


def test_fallback_is_chosen_per_period_and_recorded(v1: m.MappingSet) -> None:
    facts = [
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", "100", FY23),
        fact("Revenues", "105", FY23),
        fact("Revenues", "120", FY24),
    ]
    out = _resolve(facts, v1)
    fy23, fy24 = out[("IS.REVENUE", FY23[1])], out[("IS.REVENUE", FY24[1])]
    assert (fy23.value, fy23.concept, fy23.priority) == (Decimal("100"), facts[0].concept, 1)
    assert fy23.alternatives == ("Revenues",)
    assert (fy24.value, fy24.concept, fy24.priority) == (Decimal("120"), "Revenues", 2)


def test_sign_multiplier_normalizes_cash_outflows(v1: m.MappingSet) -> None:
    out = _resolve([fact("PaymentsToAcquirePropertyPlantAndEquipment", "9447", FY24)], v1)
    capex = out[("CF.CAPEX", FY24[1])]
    assert capex.value == Decimal("-9447") and capex.sign == -1


def test_units_and_period_shapes_are_respected(v1: m.MappingSet) -> None:
    facts = [
        fact("Revenues", "999", FY24, unit="EUR"),  # other currency: ignored
        fact("EarningsPerShareDiluted", "6.08", FY24, unit="USD/shares"),
        fact("WeightedAverageNumberOfDilutedSharesOutstanding", "15408095000", FY24, unit="shares"),
        fact("Assets", "364980", (None, FY24[1])),
        fact("Assets", "1", FY24),  # a duration "Assets" fact is not a balance
    ]
    out = _resolve(facts, v1)
    assert ("IS.REVENUE", FY24[1]) not in out
    assert out[("IS.EPS_DILUTED", FY24[1])].unit == "USD/shares"
    assert out[("IS.SHARES_DILUTED_WA", FY24[1])].value == Decimal("15408095000")
    assets = out[("BS.TOTAL_ASSETS", FY24[1])]
    assert assets.period_start is None and assets.value == Decimal("364980")


def test_ambiguous_winner_withholds_value_without_falling_back(v1: m.MappingSet) -> None:
    facts = [
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", "100", FY24),
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", "101", FY24),  # same filing
        fact("Revenues", "100", FY24),
    ]
    rev = _resolve(facts, v1)[("IS.REVENUE", FY24[1])]
    assert rev.status is ValueStatus.AMBIGUOUS
    assert rev.value is None and rev.source is None
    assert rev.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_policy_and_restatement_flag(v1: m.MappingSet) -> None:
    facts = [
        fact("NetIncomeLoss", "50", FY23, filed=date(2024, 2, 1), accn="0000000001-24-000001"),
        fact("NetIncomeLoss", "48", FY23, filed=date(2025, 2, 1), accn="0000000001-25-000001"),
    ]
    latest = _resolve(facts, v1)[("IS.NET_INCOME_COMMON", FY23[1])]
    first = _resolve(facts, v1, policy=Policy.FIRST_REPORTED)[("IS.NET_INCOME_COMMON", FY23[1])]
    assert latest.value == Decimal("48") and first.value == Decimal("50")
    assert latest.restated and first.restated
    assert latest.source.accession_no == "0000000001-25-000001"


def test_resolution_is_deterministic(v1: m.MappingSet) -> None:
    facts = [
        fact("Revenues", "120", FY24),
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", "100", FY23),
        fact("Revenues", "105", FY23),
        fact("PaymentsToAcquirePropertyPlantAndEquipment", "7", FY24),
        fact("Assets", "10", (None, FY24[1])),
        fact("Assets", "9", (None, FY23[1])),
    ]
    baseline = resolve(select(facts), v1, taxonomy="us-gaap", currency="USD")
    for seed in range(5):
        shuffled = facts[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - deterministic test shuffle
        assert resolve(select(shuffled), v1, taxonomy="us-gaap", currency="USD") == baseline


def test_ifrs_taxonomy_is_resolved_independently(v1: m.MappingSet) -> None:
    facts = [
        fact("Revenue", "35656", FY24, unit="CAD", taxonomy="ifrs-full"),
        fact("Revenues", "1", FY24, unit="CAD"),  # us-gaap fact: not used for an IFRS filer
        fact(
            "DividendsPaidClassifiedAsFinancingActivities",
            "4429",
            FY24,
            unit="CAD",
            taxonomy="ifrs-full",
        ),
    ]
    values = resolve(select(facts), v1, taxonomy="ifrs-full", currency="CAD")
    got = {v.line_item: (v.value, v.concept) for v in values}
    assert got == {
        "IS.REVENUE": (Decimal("35656"), "Revenue"),
        "CF.DIVIDENDS": (Decimal("-4429"), "DividendsPaidClassifiedAsFinancingActivities"),
    }


def test_annual_coverage_reports_missing_items(v1: m.MappingSet) -> None:
    q4 = (date(2024, 10, 1), date(2024, 12, 31))
    facts = [
        fact("Revenues", "120", FY24),
        fact("Revenues", "30", q4),  # quarterly window: not an annual value
        fact("Revenues", "100", FY23),
        fact("NetIncomeLoss", "10", FY24),
        fact("Assets", "500", (None, FY24[1])),
    ]
    values = resolve(select(facts), v1, taxonomy="us-gaap", currency="USD")
    cov = annual_coverage(values, v1, "us-gaap", years=5)
    assert cov.year_ends == (FY23[1], FY24[1])
    assert "IS.REVENUE" not in cov.missing(FY24[1])
    assert "BS.TOTAL_ASSETS" not in cov.missing(FY24[1])
    assert "BS.TOTAL_ASSETS" in cov.missing(FY23[1])
    assert "IS.EBITDA" not in cov.mapped  # derived items are never "missing" mappings
    assert annual_coverage(values, v1, "us-gaap", years=1).year_ends == (FY24[1],)


def test_helpers() -> None:
    assert taxonomy_for_standard("US_GAAP") == "us-gaap"
    assert taxonomy_for_standard("IFRS") == "ifrs-full"
    assert taxonomy_for_standard(None) is None
    item = m.LineItem("IS.EPS", "EPS", "IS", "duration", "per_share", "natural")
    assert expected_unit(item, "CAD") == "CAD/shares"


# ---- Database: migration 0005 ----------------------------------------------------------------


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_reference_tables_match_the_yaml(app_engine: Engine, version: str) -> None:
    loaded = m.load(ROOT, version)
    with app_engine.connect() as conn:
        version_rows = conn.execute(
            text("SELECT version, content_sha256 FROM ref.mapping_versions WHERE version = :v"),
            {"v": version},
        ).all()
        items = conn.execute(
            text(
                "SELECT code, statement, period_type, value_type, sign_convention, is_subtotal, "
                "subtotal_formula, derived_only FROM ref.standard_line_items "
                "WHERE mapping_version = :v ORDER BY sort_order"
            ),
            {"v": version},
        ).all()
        concepts = conn.execute(
            text(
                "SELECT taxonomy, line_item_code, priority, concept, sign_multiplier "
                "FROM ref.concept_mappings WHERE mapping_version = :v"
            ),
            {"v": version},
        ).all()
    assert [tuple(r) for r in version_rows] == [(version, loaded.sha256)]
    assert [r.code for r in items] == list(loaded.line_items)
    gp = next(r for r in items if r.code == "IS.GROSS_PROFIT")
    assert gp.is_subtotal and gp.subtotal_formula == [
        {"sign": 1, "code": "IS.REVENUE"},
        {"sign": -1, "code": "IS.COGS"},
    ]
    expected = {
        (c.taxonomy, c.line_item, c.priority, c.concept, c.sign)
        for cands in loaded.candidates.values()
        for c in cands
    }
    assert {tuple(r) for r in concepts} == expected


@pytest.mark.parametrize("engine_name", ["app_engine", "owner_engine"])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ref.concept_mappings SET priority = priority + 100",
        "DELETE FROM ref.standard_line_items",
        "UPDATE ref.mapping_versions SET content_sha256 = repeat('0', 64)",
    ],
)
def test_reference_mappings_are_immutable(
    request: pytest.FixtureRequest, engine_name: str, statement: str
) -> None:
    engine: Engine = request.getfixturevalue(engine_name)
    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(DBAPIError, match=r"append-only|permission denied"):
            conn.execute(text(statement))
        trans.rollback()


def test_app_role_cannot_add_mapping_versions(app_engine: Engine) -> None:
    # New versions arrive only through migrations (owner role).
    with app_engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(
                text(
                    "INSERT INTO ref.mapping_versions (version, content_sha256) "
                    "VALUES ('v9', repeat('0', 64))"
                )
            )
        trans.rollback()
