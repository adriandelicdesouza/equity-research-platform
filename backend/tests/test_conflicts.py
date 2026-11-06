"""Conflict detection, materiality and resolution application (pure; Blueprint §10.3, D52)."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import Policy
from erp_backend.standardization import mapping as m
from erp_backend.standardization.conflicts import Resolution, materiality
from erp_backend.standardization.dataset import BuiltDataset, DatasetHeader, build, canonical_bytes

ROOT = Path(__file__).resolve().parents[2] / "mappings"
FY24 = (None, date(2024, 12, 31))
FY24D = (date(2024, 1, 1), date(2024, 12, 31))
F1 = {"accn": "0000000001-25-000001", "form": "10-K", "filed": date(2025, 2, 1)}
F2 = {"accn": "0000000001-26-000001", "form": "10-K", "filed": date(2026, 2, 1)}


@pytest.fixture(scope="module")
def v2() -> m.MappingSet:
    return m.load(ROOT, "v2")


def f(concept: str, value: str, period: tuple[date | None, date], *, accn: str, form: str,
      filed: date, unit: str = "USD") -> RawFact:  # fmt: skip
    return RawFact("us-gaap", concept, unit, Decimal(value), period[0], period[1], None, None,
                   form, filed, accn, None)  # fmt: skip


def run(v2: m.MappingSet, *facts: RawFact, resolutions: dict[str, Resolution] | None = None,
        policy: Policy = Policy.LATEST_REPORTED) -> BuiltDataset:  # fmt: skip
    ids = {x: i for i, x in enumerate(facts, start=1)}
    cutoff = max((r.id for r in (resolutions or {}).values()), default=None)
    return build(ids, v2, taxonomy="us-gaap", currency="USD", policy=policy,
                 resolutions=resolutions, resolution_cutoff_id=cutoff)  # fmt: skip


def value(b: BuiltDataset, code: str, ptype: str = "FY"):  # type: ignore[no-untyped-def]
    return next(v for v in b.values if v.line_item == code and v.period.period_type == ptype)


def check(b: BuiltDataset, code: str) -> str:
    return next(r.status for r in b.checks if r.check_code == code)


# A balance sheet where a later filing restated equity but not assets or liabilities.
# Exact (non-round) amounts, as filed: the inferred reporting precision stays at 1.
BASE = (
    f("Revenues", "1000", FY24D, **F1),
    f("Assets", "100123", FY24, **F1),
    f("Liabilities", "60111", FY24, **F1),
    f("StockholdersEquity", "40012", FY24, **F1),
)
RESTATED_EQUITY = f("StockholdersEquity", "50012", FY24, **F2)


# ---- Materiality -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "a", "b", "base", "material"),
    [
        ("BS.TOTAL_EQUITY", "1000", "1005", None, False),  # 0.5% of 1005 = 5.025
        ("BS.TOTAL_EQUITY", "1000", "1006", None, True),
        ("BS.CASH", "1000", "1001", None, False),  # DCF-critical: 0.1% of 1001 = 1.001
        ("BS.CASH", "1000", "1002", None, True),
        ("BS.TOTAL_EQUITY", "1000", "1100", "1000000", False),  # floor 0.05% × base = 500
        ("BS.TOTAL_EQUITY", "1000", "1600", "1000000", True),
        ("IS.EBIT", "10", "-10", None, True),  # sign disagreement
    ],
)
def test_materiality_thresholds(
    code: str, a: str, b: str, base: str | None, material: bool
) -> None:
    result = materiality(code, [Decimal(a), Decimal(b)], Decimal(base) if base else None)
    assert result.is_material is material
    assert result.absolute_difference == abs(Decimal(b) - Decimal(a))
    if code == "IS.EBIT":
        assert result.basis == "sign disagreement"
    elif code == "BS.CASH":
        assert "DCF-critical" in result.basis


# ---- Detection -------------------------------------------------------------------------------


def test_mixed_vintage_restatement_is_a_material_conflict(v2: m.MappingSet) -> None:
    b = run(v2, *BASE, RESTATED_EQUITY)
    (c,) = [c for c in b.conflicts if c.line_item == "BS.TOTAL_EQUITY"]
    assert (c.kind, c.status, c.materiality.is_material) == ("mixed_vintage", "open", True)
    assert [(x.role, x.value, x.accession, x.form, x.filed) for x in c.candidates] == [
        ("first_reported", Decimal("40012"), F1["accn"], "10-K", F1["filed"]),
        ("latest_reported", Decimal("50012"), F2["accn"], "10-K", F2["filed"]),
    ]
    assert c.policy_fact_id == 5  # the latest-reported fact chosen by the policy
    assert ["BS.TOTAL_ASSETS", F1["accn"]] in c.detail["older_related_items"]
    assert b.summary["conflicts"]["open_material"] >= 1
    assert check(b, "BALANCE") == "failed"  # the mixed vintage breaks the identity
    # The policy value is still the latest one: nothing is silently swapped.
    assert value(b, "BS.TOTAL_EQUITY").value == Decimal("50012")


def test_consistent_restatement_and_small_ones_are_not_open_conflicts(v2: m.MappingSet) -> None:
    whole = run(
        v2, *BASE, RESTATED_EQUITY,
        f("Assets", "110123", FY24, **F2), f("Liabilities", "60111", FY24, **F2),
    )  # fmt: skip
    assert [c for c in whole.conflicts if c.line_item == "BS.TOTAL_EQUITY"] == []
    tiny = run(v2, *BASE, f("StockholdersEquity", "40015", FY24, **F2))
    (c,) = [c for c in tiny.conflicts if c.line_item == "BS.TOTAL_EQUITY"]
    assert c.status == "auto_resolved_immaterial" and not c.materiality.is_material


def test_legitimate_differences_are_not_conflicts(v2: m.MappingSet) -> None:
    b = run(
        v2,
        *BASE,
        f("Revenues", "999", FY24D, unit="EUR", **F1),  # another unit
        f("Revenues", "250", (date(2024, 10, 1), date(2024, 12, 31)), **F1),  # another duration
        f("RevenueFromContractWithCustomerExcludingAssessedTax", "900", FY24D, **F1),  # scope
    )
    assert b.conflicts == ()
    rev = value(b, "IS.REVENUE")
    assert rev.value == Decimal("900") and rev.detail["alternatives"] == ["Revenues"]


def test_within_filing_conflict_is_always_material(v2: m.MappingSet) -> None:
    b = run(v2, *BASE, f("Revenues", "1001", FY24D, **F1))
    (c,) = [c for c in b.conflicts if c.kind == "within_filing"]
    assert c.line_item == "IS.REVENUE" and c.status == "open" and c.materiality.is_material
    assert sorted(x.value for x in c.candidates) == [Decimal("1000"), Decimal("1001")]
    assert {x.role for x in c.candidates} == {"same_filing"} and c.policy_fact_id is None
    assert value(b, "IS.REVENUE").status == "ambiguous"


# ---- Resolutions -----------------------------------------------------------------------------


def _key(b: BuiltDataset, code: str) -> str:
    return next(c.key for c in b.conflicts if c.line_item == code)


def test_select_first_reported_candidate_restores_a_consistent_vintage(v2: m.MappingSet) -> None:
    before = run(v2, *BASE, RESTATED_EQUITY)
    key = _key(before, "BS.TOTAL_EQUITY")
    first = next(x for x in before.conflicts[0].candidates if x.role == "first_reported")
    after = run(
        v2, *BASE, RESTATED_EQUITY,
        resolutions={key: Resolution(7, key, "select_candidate", candidate_fact_id=first.fact_id)},
    )  # fmt: skip
    (c,) = [c for c in after.conflicts if c.key == key]
    assert c.status == "resolved" and c.resolution is not None and c.resolution.id == 7
    equity = value(after, "BS.TOTAL_EQUITY")
    assert equity.value == Decimal("40012") and equity.provenance == "reported"
    assert equity.source_fact_ids == (first.fact_id,) and equity.detail["accession"] == F1["accn"]
    assert equity.detail["resolution"]["conflict"] == key
    assert check(after, "BALANCE") == "passed"  # the resolution changes the checks
    assert after.summary["conflicts"]["open_material"] == 0


def test_enter_value_override_and_revoke(v2: m.MappingSet) -> None:
    before = run(v2, *BASE, RESTATED_EQUITY)
    key = _key(before, "BS.TOTAL_EQUITY")
    entry = Resolution(
        8, key, "enter_value", value=Decimal("40012"), source_reference="10-K 2024 p. 52"
    )
    entered = run(v2, *BASE, RESTATED_EQUITY, resolutions={key: entry})
    eq = value(entered, "BS.TOTAL_EQUITY")
    assert (eq.provenance, eq.value, eq.source_fact_ids) == ("analyst", Decimal("40012"), ())
    assert eq.detail["resolution"]["source_reference"] == "10-K 2024 p. 52"
    overridden = run(v2, *BASE, RESTATED_EQUITY,
                     resolutions={key: Resolution(9, key, "override")})  # fmt: skip
    (c,) = [c for c in overridden.conflicts if c.key == key]
    assert c.status == "overridden"
    assert value(overridden, "BS.TOTAL_EQUITY").value == Decimal("50012")  # kept, documented
    assert check(overridden, "BALANCE") == "failed"  # an override does not hide the gap
    revoked = run(v2, *BASE, RESTATED_EQUITY, resolutions={key: Resolution(10, key, "revoke")})
    assert next(c for c in revoked.conflicts if c.key == key).status == "open"


def test_within_filing_resolution_and_unusable_override(v2: m.MappingSet) -> None:
    before = run(v2, *BASE, f("Revenues", "1001", FY24D, **F1))
    c = next(c for c in before.conflicts if c.kind == "within_filing")
    pick = next(x for x in c.candidates if x.value == Decimal("1000"))
    choice = Resolution(3, c.key, "select_candidate", candidate_fact_id=pick.fact_id)
    resolved = run(v2, *BASE, f("Revenues", "1001", FY24D, **F1), resolutions={c.key: choice})
    rev = value(resolved, "IS.REVENUE")
    assert rev.status == "ok" and rev.value == Decimal("1000")
    assert "ambiguous_accessions" not in rev.detail
    stuck = run(v2, *BASE, f("Revenues", "1001", FY24D, **F1),
                resolutions={c.key: Resolution(4, c.key, "override")})  # fmt: skip
    (s,) = [x for x in stuck.conflicts if x.key == c.key]
    assert s.status == "open" and s.detail["unapplied_resolution"] == 4


def test_a_resolution_never_applies_to_a_changed_candidate_set(v2: m.MappingSet) -> None:
    before = run(v2, *BASE, RESTATED_EQUITY)
    key = _key(before, "BS.TOTAL_EQUITY")
    later = f("StockholdersEquity", "52000", FY24, accn="0000000001-27-000001", form="10-K/A",
              filed=date(2027, 2, 1))  # fmt: skip
    after = run(v2, *BASE, RESTATED_EQUITY, later,
                resolutions={key: Resolution(5, key, "override")})  # fmt: skip
    (c,) = [c for c in after.conflicts if c.line_item == "BS.TOTAL_EQUITY"]
    assert c.key != key and c.status == "open"  # reopened for the analyst


def test_conflicts_are_deterministic_and_in_the_canonical_bytes(v2: m.MappingSet) -> None:
    facts = [*BASE, RESTATED_EQUITY, f("Revenues", "1001", FY24D, **F1)]
    ids = {x: i for i, x in enumerate(facts, start=1)}
    ref = build(ids, v2, taxonomy="us-gaap", currency="USD")
    for seed in range(4):
        shuffled = facts[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - deterministic test shuffle
        again = build({x: ids[x] for x in shuffled}, v2, taxonomy="us-gaap", currency="USD")
        assert again.canonical == ref.canonical
    assert b'"conflicts":[' in ref.canonical and b'"resolution_cutoff_id":null' in ref.canonical


def test_format_2_bytes_exclude_conflicts() -> None:
    header = DatasetHeader("v2", "0" * 64, "us-gaap", "USD", "latest_reported",
                           date(2025, 1, 1), "1" * 64, format_version=2)  # fmt: skip
    data = canonical_bytes(header, [], [], ())
    assert b"conflicts" not in data and b"resolution_cutoff_id" not in data
