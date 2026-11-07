"""Scenarios (Phase 5b): inheritance, cycle checks, archive rules, probabilities and the
probability-weighted summary."""

from __future__ import annotations

from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest
from erp_backend.models import AuditEvent, Scenario
from erp_backend.read import valuation as rv
from erp_backend.valuation import assumptions as asm
from erp_backend.valuation import scenarios as scn
from erp_backend.valuation.errors import ValuationRefused
from erp_engine.core.numbers import make_context
from sqlalchemy import select
from sqlalchemy.orm import Session

from .test_valuation_runs import Case, actor, q
from .valuation_helpers import B1_PRICE, b1_assumptions, setup

WHY = "Scenario no longer part of the committee view."


@pytest.fixture
def case(db: Session, tmp_path: Path) -> Case:
    c = Case(db, setup(db, tmp_path), actor(db, "sasha"))
    c.assume(b1_assumptions())
    return c


def child(case: Case, name: str, kind: str = "bull", parent: Scenario | None = None) -> Scenario:
    return scn.create_scenario(
        case.db,
        case.owner,
        case.project.id,
        name=name,
        kind=kind,
        parent_scenario_id=(parent or case.scenario).id,
    )


def revise(case: Case, s: Scenario, changes: list[dict[str, Any]]) -> asm.State:
    return asm.revise(
        case.db,
        case.owner,
        s.id,
        expected_revision=s.revision_no,
        changes=[asm.Change(**c) for c in changes],
    )


def run_on(case: Case, s: Scenario) -> Any:
    saved = case.scenario
    case.scenario = s
    try:
        return case.run().run
    finally:
        case.scenario = saved


def refused_on(case: Case, s: Scenario, code: str) -> ValuationRefused:
    with pytest.raises(ValuationRefused) as exc:
        run_on(case, s)
    assert exc.value.code == code, exc.value.details
    return exc.value


def refuse(code: str, fn: Any, *a: Any, **kw: Any) -> ValuationRefused:
    with pytest.raises(ValuationRefused) as exc:
        fn(*a, **kw)
    assert exc.value.code == code, exc.value.details
    return exc.value


def test_child_inherits_and_overrides(db: Session, case: Case) -> None:
    bull = child(case, "Bull")
    state = asm.resolved_state(db, bull)
    assert asm.current_state(db, bull).items == {}
    assert state.value("terminal_growth") == Decimal("0.02")
    assert state.get("terminal_growth").inherited_from == case.scenario.id  # type: ignore[union-attr]
    base_run = case.run().run
    assert q(base_run.per_share_value) == B1_PRICE
    # Base documents are unchanged by Phase 5b: no lineage and no inheritance markers.
    assert "scenario_lineage" not in base_run.input_document
    assert all("inherited_from" not in i for i in base_run.input_document["inputs"].values())

    revise(
        case, bull, [{"key": "ebit_margin", "period_index": k, "value": "0.25"} for k in (1, 2, 3)]
    )
    run = run_on(case, bull)
    assert run.scenario_id == bull.id and run.per_share_value > base_run.per_share_value
    doc = run.input_document
    assert doc["scenario_lineage"] == [
        {"id": str(case.scenario.id), "revision": case.scenario.revision_no}
    ]
    assert doc["inputs"]["assumption:ebit_margin[1]"].get("inherited_from") is None
    assert doc["inputs"]["assumption:terminal_growth"]["inherited_from"] == str(case.scenario.id)
    # A base change flows into the child's next run (its document, hence identity, changes).
    revise(case, case.scenario, [{"key": "terminal_growth", "value": "0.01"}])
    again = run_on(case, bull)
    assert again.id != run.id and again.per_share_value < run.per_share_value


def test_clear_in_child_hides_inherited_value(db: Session, case: Case) -> None:
    bull = child(case, "Bull")
    revise(case, bull, [{"key": "terminal_growth", "clear": True}])
    assert asm.resolved_state(db, bull).get("terminal_growth") is None
    err = refused_on(case, bull, "input_missing")
    assert any(d.get("input") == "terminal_growth" for d in err.details)
    assert asm.resolved_state(db, case.scenario).value("terminal_growth") == Decimal("0.02")


def test_inherited_suggestion_is_a_gate_and_is_approved_in_the_parent(
    db: Session, case: Case
) -> None:
    bull = child(case, "Bull")
    revise(case, case.scenario, [{"key": "ronic", "value": "0.12", "status": "suggested"}])
    revise(case, case.scenario, [{"key": "terminal_form", "choice": "value_driver"}])
    refused_on(case, bull, "assumption_not_approved")
    refuse(
        "not_suggested",
        asm.approve,
        db,
        case.owner,
        bull.id,
        expected_revision=bull.revision_no,
        items=[("ronic", None)],
    )


def test_cycles_parents_and_depth(db: Session, case: Case) -> None:
    a = child(case, "A", "custom")
    b = child(case, "B", "custom", parent=a)
    refuse(
        "scenario_cycle",
        scn.update_scenario,
        db,
        case.owner,
        a.id,
        expected_revision=a.revision_no,
        changes={"parent_scenario_id": b.id},
    )
    refuse(
        "scenario_cycle",
        scn.update_scenario,
        db,
        case.owner,
        a.id,
        expected_revision=a.revision_no,
        changes={"parent_scenario_id": a.id},
    )
    refuse(
        "invalid_parent",
        scn.update_scenario,
        db,
        case.owner,
        case.scenario.id,
        expected_revision=case.scenario.revision_no,
        changes={"parent_scenario_id": a.id},
    )
    chain = b
    for i in range(asm.MAX_DEPTH - 3):
        chain = child(case, f"D{i}", "custom", parent=chain)
    refuse("scenario_cycle", child, case, "Too deep", "custom", chain)
    # Re-parenting bumps the revision (resolved values change) and is audited.
    rev = b.revision_no
    scn.update_scenario(
        db, case.owner, b.id, expected_revision=rev, changes={"parent_scenario_id": None}
    )
    assert b.revision_no == rev + 1 and b.parent_scenario_id is None
    refuse(
        "revision_conflict",
        scn.update_scenario,
        db,
        case.owner,
        b.id,
        expected_revision=rev,
        changes={"name": "B2"},
    )
    events = db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == str(b.id))).all()
    assert list(events) == ["scenario_created", "scenario_updated"]


def test_create_rules(db: Session, case: Case) -> None:
    child(case, "Bull")
    refuse("duplicate_name", child, case, "bull")
    refuse(
        "invalid_kind",
        scn.create_scenario,
        db,
        case.owner,
        case.project.id,
        name="Second base",
        kind="base",
    )
    other = actor(db, "otto")
    from erp_backend.errors import ApiError

    with pytest.raises(ApiError) as exc:
        scn.create_scenario(db, other, case.project.id, name="Mine", kind="custom")
    assert exc.value.code == "not_owner"


def test_archive_and_restore_rules(db: Session, case: Case) -> None:
    bull = child(case, "Bull")
    leaf = child(case, "Leaf", "custom", parent=bull)

    def status(s: Scenario, to: str) -> Scenario:
        return scn.set_scenario_status(
            db, case.owner, s.id, expected_revision=s.revision_no, status=to, reason=WHY
        )

    refuse("base_scenario", status, case.scenario, "archived")
    refuse("scenario_has_children", status, bull, "archived")
    status(leaf, "archived")
    refuse("scenario_archived", revise, case, leaf, [{"key": "terminal_growth", "value": "0.03"}])
    refused_on(case, leaf, "scenario_archived")
    scn.set_probabilities(
        db,
        case.owner,
        case.project.id,
        expected_revision=case.project.revision_no,
        weights={case.scenario.id: Decimal("0.5"), bull.id: Decimal("0.5")},
    )
    refuse("scenario_weighted", status, bull, "archived")
    refuse(
        "reason_required",
        scn.set_scenario_status,
        db,
        case.owner,
        leaf.id,
        expected_revision=leaf.revision_no,
        status="active",
        reason="short",
    )
    status(leaf, "active")
    assert leaf.status == "active"


def test_probabilities(db: Session, case: Case) -> None:
    bull = child(case, "Bull")
    bear = child(case, "Bear", "bear")

    def weights(w: dict[Scenario, str], rev: int | None = None) -> Any:
        return scn.set_probabilities(
            db,
            case.owner,
            case.project.id,
            expected_revision=case.project.revision_no if rev is None else rev,
            weights={s.id: Decimal(v) for s, v in w.items()},
        )

    err = refuse("weights_not_one", weights, {case.scenario: "0.5", bull: "0.4"})
    assert err.details == [{"sum": "0.9"}]
    refuse("invalid_weights", weights, {case.scenario: "1.5", bull: "-0.5"})
    refuse("revision_conflict", weights, {case.scenario: "1"}, 99)
    weights({case.scenario: "0.5", bull: "0.25", bear: "0.25"})
    assert [s.probability for s in (case.scenario, bull, bear)] == [
        Decimal("0.5"),
        Decimal("0.25"),
        Decimal("0.25"),
    ]
    weights({case.scenario: "0.6", bull: "0.4"})
    assert bear.probability is None
    weights({})
    assert all(s.probability is None for s in (case.scenario, bull, bear))
    actions = db.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_id == str(case.project.id))
    ).all()
    assert list(actions).count("probabilities_set") == 3


def test_probability_weighted_summary(db: Session, case: Case) -> None:
    bull = child(case, "Bull")
    revise(
        case, bull, [{"key": "ebit_margin", "period_index": k, "value": "0.25"} for k in (1, 2, 3)]
    )
    scn.set_probabilities(
        db,
        case.owner,
        case.project.id,
        expected_revision=case.project.revision_no,
        weights={case.scenario.id: Decimal("0.7"), bull.id: Decimal("0.3")},
    )
    summary = rv.scenario_summary(db, case.project.id, "dcf_fcff")
    assert not summary.complete and summary.weighted_per_share is None
    assert len(summary.issues) == 2
    base_run = case.run().run
    bull_run = run_on(case, bull)
    summary = rv.scenario_summary(db, case.project.id, "dcf_fcff")
    assert summary.complete and summary.issues == []
    with localcontext(make_context()):  # the engine's decimal context (precision 34)
        expected = (
            Decimal("0.7") * base_run.per_share_value + Decimal("0.3") * bull_run.per_share_value
        )
    assert summary.weighted_per_share == expected
    assert summary.probabilities_sum == Decimal("1.0")
    assert summary.per_share_low == base_run.per_share_value
    assert summary.per_share_high == bull_run.per_share_value
    assert [i.run.id if i.run else None for i in summary.scenarios] == [base_run.id, bull_run.id]
