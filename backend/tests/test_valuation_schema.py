"""Valuation schema (migration 0017): blocks, immutability, grants, constraints, triggers."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from erp_backend.config import Settings
from erp_backend.models import (
    AssumptionRevision,
    RunDecision,
    RunInput,
    RunTrace,
    Scenario,
    User,
    ValuationBlock,
    ValuationProject,
    ValuationRun,
)
from erp_backend.valuation import blocks as blocks_mod
from erp_engine import classification as engine_classification
from erp_engine.core.validation import ValidationReport
from erp_engine.inputs import Classification
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from .conftest import make_user
from .valuation_helpers import ROOT, Fixture, make_company, setup

SHA = "a" * 64
# The app role lacks UPDATE/DELETE grants (first layer); triggers bind every role (second).
DENIED = "append-only|permission denied"
APPEND_ONLY = (
    "ref.valuation_blocks",
    "val.assumption_revisions",
    "val.valuation_runs",
    "val.run_traces",
    "val.run_inputs",
    "val.run_decisions",
)


def _user(db: Session, name: str = "vera") -> User:
    make_user(db, name, ["analyst"])
    return db.scalars(select(User).where(User.username == name)).one()


def _project(db: Session, fx: Fixture, user: User, **kw: Any) -> ValuationProject:
    p = ValuationProject(
        company_id=fx.company.id,
        listing_id=fx.listing.id,
        name=kw.pop("name", "Base case"),
        status=kw.pop("status", "active"),
        owner_user_id=user.id,
        dataset_id=fx.dataset.id,
        revision_no=1,
        created_by=user.id,
        **kw,
    )
    db.add(p)
    db.flush()
    return p


def _scenario(db: Session, project: ValuationProject, user: User, **kw: Any) -> Scenario:
    s = Scenario(
        project_id=project.id,
        name=kw.pop("name", "Base"),
        kind=kw.pop("kind", "base"),
        sort_order=0,
        revision_no=0,
        status="active",
        created_by=user.id,
        **kw,
    )
    db.add(s)
    db.flush()
    return s


def _run(db: Session, fx: Fixture, p: ValuationProject, s: Scenario, user: User) -> ValuationRun:
    block = blocks_mod.current_block(db, "non_financial", "dcf_fcff")
    run = ValuationRun(
        project_id=p.id, scenario_id=s.id, scenario_revision_no=1, company_id=fx.company.id,
        listing_id=fx.listing.id, methodology="dcf_fcff", method_version="1.0.0",
        produces="enterprise_value", dataset_id=fx.dataset.id,
        dataset_version_no=fx.dataset.version_no, dataset_basis=fx.dataset.basis,
        dataset_content_sha256=fx.dataset.content_sha256, block_id=block.id,
        classification="non_financial", valuation_date=fx.period.end_date,
        balance_sheet_date=fx.period.end_date, currency="USD", input_document={"x": 1},
        input_sha256=SHA, engine_input_sha256=SHA, engine_version="0.1.0", module_versions={},
        outputs={}, primary_value=Decimal(1), per_share_value=Decimal(1), validation=[],
        warnings=[], output_sha256=SHA, created_by=user.id,
    )  # fmt: skip
    db.add(run)
    db.flush()
    return run


def _refused(db: Session, sql: str, params: dict[str, Any], match: str) -> None:
    with pytest.raises(DBAPIError, match=match), db.begin_nested():
        db.execute(text(sql), params)


# --------------------------------------------------------------------------- blocks


def test_blocks_loaded_and_pinned(db: Session) -> None:
    rows = db.scalars(select(ValuationBlock)).all()
    loaded = blocks_mod.load(ROOT)
    assert len(rows) == len(loaded.blocks) == 14
    for row in rows:
        block = loaded.get(row.classification, row.methodology)
        assert block is not None
        assert row.definition == block.definition  # compared as parsed JSON
        assert row.definition_sha256 == block.definition_sha256
        assert row.file_sha256 == loaded.sha256


def test_blocks_equal_the_engine_policy_gate() -> None:
    loaded = blocks_mod.load(ROOT)
    for block in loaded.blocks:
        cls = Classification(block.classification)
        blocked = cls in engine_classification.BLOCKED
        assert (block.policy == "blocked") == blocked, block
        if blocked:
            continue
        report = ValidationReport()
        engine_classification.gate(cls, report)
        assert sorted(report.codes()) == sorted(block.acknowledgements), block
        assert (block.policy == "permitted_with_warnings") == bool(report.codes())


def test_block_loader_rejects_incomplete_files(tmp_path: Path) -> None:
    (tmp_path / "valuation-blocks").mkdir()
    (tmp_path / "valuation-blocks" / "v1.yaml").write_text(
        "version: v1\nblocks:\n  - {classification: bank, methodology: dcf_fcff, policy: blocked}\n"
    )
    with pytest.raises(blocks_mod.BlockError, match="no block for"):
        blocks_mod.load(tmp_path)


# --------------------------------------------------------------------------- append-only


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_append_only_tables_refuse_update_delete_truncate_for_owner(
    owner_engine: Engine, table: str
) -> None:
    """The owner (migration role) is bound too. TRUNCATE is a statement trigger, so it fires
    on every table; row UPDATE/DELETE is shown on the populated blocks table here and on
    every run table in ``test_runs_and_children_are_immutable``."""
    statements = [f"TRUNCATE {table} CASCADE"]
    if table == "ref.valuation_blocks":
        statements += [f"UPDATE {table} SET policy = policy", f"DELETE FROM {table}"]
    with owner_engine.connect() as conn:
        for sql in statements:
            trans = conn.begin()
            try:
                with pytest.raises(DBAPIError, match=DENIED):
                    conn.execute(text(sql))
            finally:
                trans.rollback()


def test_app_role_grants(app_engine: Engine, settings: Settings) -> None:
    with app_engine.connect() as conn:

        def priv(table: str, p: str) -> bool:
            return bool(
                conn.scalar(text("SELECT has_table_privilege(:t, :p)"), {"t": table, "p": p})
            )

        for table in ("val.valuation_projects", "val.scenarios"):
            assert priv(table, "SELECT") and priv(table, "INSERT") and priv(table, "UPDATE")
            assert not priv(table, "DELETE") and not priv(table, "TRUNCATE")
        for table in APPEND_ONLY[1:]:
            assert priv(table, "SELECT") and priv(table, "INSERT")
            assert not priv(table, "UPDATE") and not priv(table, "DELETE")
        assert priv("ref.valuation_blocks", "SELECT")
        assert not priv("ref.valuation_blocks", "INSERT")


# --------------------------------------------------------------------------- projects


def test_project_company_dataset_and_listing_must_match(db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    other, other_listing = make_company(db, ticker="OTH")
    user = _user(db)
    p = _project(db, fx, user)
    _refused(db, "UPDATE val.valuation_projects SET company_id = :c WHERE id = :p",
             {"c": other.id, "p": p.id}, "immutable")  # fmt: skip
    _refused(db, "UPDATE val.valuation_projects SET listing_id = :l WHERE id = :p",
             {"l": other_listing.id, "p": p.id}, "does not belong")  # fmt: skip
    with pytest.raises(DBAPIError, match="does not belong"), db.begin_nested():
        _project(db, Fixture(other, other_listing, fx.dataset, fx.period), user, name="X")
    _refused(db, "DELETE FROM val.valuation_projects WHERE id = :p", {"p": p.id}, DENIED)


def test_project_names_unique_among_active(db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    user = _user(db)
    _project(db, fx, user, name="Thesis")
    with pytest.raises(IntegrityError), db.begin_nested():
        _project(db, fx, user, name=" thesis ")
    _project(db, fx, user, name="Thesis", status="archived")


def test_scenarios_one_base_same_project_no_delete(db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    user = _user(db)
    p1, p2 = _project(db, fx, user, name="One"), _project(db, fx, user, name="Two")
    base = _scenario(db, p1, user)
    with pytest.raises(IntegrityError), db.begin_nested():
        _scenario(db, p1, user, name="Base 2")
    other_base = _scenario(db, p2, user)
    with pytest.raises(DBAPIError, match="same project"), db.begin_nested():
        _scenario(db, p1, user, name="Bull", kind="bull", parent_scenario_id=other_base.id)
    _refused(db, "UPDATE val.scenarios SET kind = 'bull' WHERE id = :s", {"s": base.id},
             "immutable")  # fmt: skip
    _refused(db, "DELETE FROM val.scenarios WHERE id = :s", {"s": base.id}, DENIED)


# --------------------------------------------------------------------------- assumptions


def test_assumption_revisions_are_append_only_and_hold_one_value(
    db: Session, tmp_path: Path
) -> None:
    fx = setup(db, tmp_path)
    user = _user(db)
    s = _scenario(db, _project(db, fx, user), user)
    rev = AssumptionRevision(scenario_id=s.id, scenario_revision_no=1, key="terminal_growth",
                             value=Decimal("0.02"), unit="ratio",
                             provenance_kind="analyst_assumption", status="approved",
                             created_by=user.id)  # fmt: skip
    db.add(rev)
    db.flush()
    _refused(db, "UPDATE val.assumption_revisions SET value = 0.03 WHERE id = :i", {"i": rev.id},
             DENIED)  # fmt: skip
    _refused(db, "DELETE FROM val.assumption_revisions WHERE id = :i", {"i": rev.id},
             DENIED)  # fmt: skip
    with pytest.raises(IntegrityError, match="one_value"), db.begin_nested():
        db.add(AssumptionRevision(scenario_id=s.id, scenario_revision_no=2, key="ronic",
                                  value=Decimal(1), value_json="x", unit="ratio",
                                  provenance_kind="analyst_assumption", status="approved",
                                  created_by=user.id))  # fmt: skip
        db.flush()


# --------------------------------------------------------------------------- runs


def test_run_requires_the_companys_locked_dataset(db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path, lock=False)
    assert fx.dataset.status == "draft"
    user = _user(db)
    p = _project(db, fx, user)  # a draft may be pinned (previews, Q4)
    s = _scenario(db, p, user)
    with pytest.raises(DBAPIError, match="locked dataset"), db.begin_nested():
        _run(db, fx, p, s, user)


def test_runs_and_children_are_immutable(db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    user = _user(db)
    p = _project(db, fx, user)
    s = _scenario(db, p, user)
    run = _run(db, fx, p, s, user)
    db.add_all([
        RunTrace(run_id=run.id, trace={"n": {}}, trace_sha256=SHA, node_count=1),
        RunInput(run_id=run.id, input_key="k", value=Decimal(1), unit="ratio",
                 provenance_kind="analyst_assumption", source_type="assumption_revision",
                 warnings=[]),
        RunDecision(run_id=run.id, kind="check_override", code="BALANCE",
                    reason="Reviewed: rounding in the source filing.", detail={},
                    user_id=user.id),
    ])  # fmt: skip
    db.flush()
    for sql in (
        "UPDATE val.valuation_runs SET label = 'x' WHERE id = :r",
        "DELETE FROM val.valuation_runs WHERE id = :r",
        "UPDATE val.run_traces SET node_count = 2 WHERE run_id = :r",
        "DELETE FROM val.run_inputs WHERE run_id = :r",
        "UPDATE val.run_decisions SET reason = 'changed reason text' WHERE run_id = :r",
    ):
        _refused(db, sql, {"r": run.id}, DENIED)
    with pytest.raises(IntegrityError, match="reason"), db.begin_nested():
        db.add(RunDecision(run_id=run.id, kind="check_override", code="X", reason="short",
                           detail={}, user_id=user.id))  # fmt: skip
        db.flush()


def test_run_scenario_must_belong_to_project(db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    user = _user(db)
    p1, p2 = _project(db, fx, user, name="One"), _project(db, fx, user, name="Two")
    s2 = _scenario(db, p2, user)
    with pytest.raises(DBAPIError, match="must match"), db.begin_nested():
        _run(db, fx, p1, s2, user)
    assert uuid.UUID(str(p1.id))
