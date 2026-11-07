"""Sensitivity grids over stored runs (Phase 5b): exact base cells, refused cells, monotonicity,
tornado, idempotency, applicability, limits, ownership and immutability."""

from __future__ import annotations

from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest
from erp_backend.errors import ApiError
from erp_backend.models import AuditEvent, SensitivityRun
from erp_backend.valuation import projects, runs
from erp_backend.valuation import sensitivity as sens
from erp_backend.valuation.canonical import sha256_of
from erp_backend.valuation.errors import ValuationRefused
from erp_engine.core.numbers import make_context
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .test_valuation_runs import Case, actor
from .valuation_helpers import b1_assumptions, b3_assumptions, setup

D = Decimal


@pytest.fixture
def case(db: Session, tmp_path: Path) -> Case:
    return Case(db, setup(db, tmp_path), actor(db, "selma"))


def axis(variable: str, *values: str) -> sens.Axis:
    return sens.Axis(variable, tuple(D(v) for v in values))


def grid(case: Case, run_id: Any, req: sens.SensitivityRequest) -> tuple[SensitivityRun, bool]:
    return sens.create(case.db, case.owner, run_id, req)


def refuse(code: str, case: Case, run_id: Any, req: sens.SensitivityRequest) -> ValuationRefused:
    with pytest.raises(ValuationRefused) as exc:
        grid(case, run_id, req)
    assert exc.value.code == code, exc.value.details
    return exc.value


def b1_run(case: Case, form: str = "simple") -> Any:
    case.assume(b1_assumptions(terminal_form=form))
    return case.run().run


def test_two_way_wacc_by_growth(db: Session, case: Case) -> None:
    run = b1_run(case)
    doc_sha = sha256_of(run.input_document)
    req = sens.SensitivityRequest(
        kind="two_way",
        metric="per_share",
        x=axis("wacc", "0.08", "0.10", "0.12"),
        y=axis("terminal_g", "0.01", "0.02", "0.09", "0.10"),
    )
    row, existing = grid(case, run.id, req)
    assert not existing and row.cell_count == 12
    cells = {(c["x"], c["y"]): c for c in row.cells}
    # The base point (WACC 10 %, g 2 %) reproduces the stored run exactly.
    assert row.base == {"value": str(run.per_share_value), "x": "0.1", "y": "0.02"}
    assert D(cells[("0.10", "0.02")]["value"]) == run.per_share_value
    # Higher WACC → lower value; higher g → higher value (where g < WACC).
    assert (
        D(cells[("0.08", "0.02")]["value"])
        > run.per_share_value
        > D(cells[("0.12", "0.02")]["value"])
    )
    assert D(cells[("0.10", "0.01")]["value"]) < run.per_share_value
    # g ≥ WACC is refused, never a number.
    for key in (("0.08", "0.09"), ("0.08", "0.10"), ("0.10", "0.10")):
        assert cells[key]["value"] is None and cells[key]["refused_code"], cells[key]
    assert row.refused_count == 3
    # The run and its stored document are untouched; reproduction still matches.
    assert sha256_of(run.input_document) == doc_sha
    assert runs.reproduce(run).matches
    [event] = db.scalars(select(AuditEvent).where(AuditEvent.action == "sensitivity_created")).all()
    assert event.after is not None and event.after["result_sha256"] == row.result_sha256


def test_identical_config_returns_the_stored_result(db: Session, case: Case) -> None:
    run = b1_run(case)
    req = sens.SensitivityRequest(
        kind="one_way", metric="per_share", x=axis("terminal_g", "0.015", "0.025")
    )
    first, _ = grid(case, run.id, req)
    again, existing = grid(
        case, run.id, sens.SensitivityRequest(**{**req.__dict__, "label": "Other"})
    )
    assert existing and again.id == first.id
    assert db.scalar(select(func.count()).select_from(SensitivityRun)) == 1


def test_driver_shifts_and_tornado(db: Session, case: Case) -> None:
    run = b1_run(case)
    row, _ = grid(
        case,
        run.id,
        sens.SensitivityRequest(
            kind="one_way", metric="equity_value", x=axis("ebit_margin_shift", "-0.01", "0", "0.01")
        ),
    )
    lo, zero, hi = (D(c["value"]) for c in row.cells)
    base_equity = D(run.outputs["equity_value_v"])
    assert zero == base_equity and lo < base_equity < hi
    assert row.base == {"value": str(run.outputs["equity_value_v"]), "x": "0"}
    tornado, _ = grid(
        case,
        run.id,
        sens.SensitivityRequest(
            kind="tornado",
            metric="per_share",
            tornado=(
                sens.TornadoItem("wacc", D("0.09"), D("0.11")),
                sens.TornadoItem("revenue_growth_shift", D("-0.02"), D("0.02")),
                sens.TornadoItem("capex_pct_shift", D("-0.01"), D("0.01")),
            ),
        ),
    )
    assert tornado.cell_count == 6 and tornado.refused_count == 0
    by = {(c["variable"], c["side"]): D(c["value"]) for c in tornado.cells}
    assert by[("wacc", "low")] > run.per_share_value > by[("wacc", "high")]
    assert by[("revenue_growth_shift", "low")] < run.per_share_value
    assert by[("capex_pct_shift", "low")] > by[("capex_pct_shift", "high")]
    assert tornado.base["variables"] == {
        "wacc": "0.1",
        "revenue_growth_shift": "0",
        "capex_pct_shift": "0",
    }
    ranking = tornado.base["ranking"]
    swings = [D(r["swing"]) for r in ranking]
    assert swings == sorted(swings, reverse=True) and D(ranking[0]["share"]) == 1
    with localcontext(make_context()):
        for r in ranking:
            assert D(r["swing"]) == abs(D(r["high"]) - D(r["low"]))


def test_fcfe_uses_cost_of_equity(db: Session, case: Case) -> None:
    case.assume(b3_assumptions())
    run = case.run("dcf_fcfe").run
    ke = run.outputs["cost_of_capital"]["cost_of_equity"]
    row, _ = grid(
        case,
        run.id,
        sens.SensitivityRequest(
            kind="one_way", metric="per_share", x=axis("cost_of_equity", ke, "0.2")
        ),
    )
    assert D(row.cells[0]["value"]) == run.per_share_value
    assert D(row.cells[1]["value"]) < run.per_share_value
    refuse(
        "variable_not_applicable",
        case,
        run.id,
        sens.SensitivityRequest(kind="one_way", metric="per_share", x=axis("wacc", "0.1")),
    )
    refuse(
        "variable_not_applicable",
        case,
        run.id,
        sens.SensitivityRequest(
            kind="one_way", metric="enterprise_value", x=axis("terminal_g", "0.01")
        ),
    )


def test_request_validation(db: Session, case: Case) -> None:
    run = b1_run(case)
    ten = [f"0.0{i}" for i in range(1, 10)] + ["0.1"]
    eleven = [*ten, "0.11"]
    cases = [
        (
            "grid_too_large",
            sens.SensitivityRequest(
                kind="two_way",
                metric="per_share",
                x=axis("wacc", *eleven),
                y=axis("terminal_g", *ten[:-1], "0.005"),
            ),
        ),
        (
            "variable_not_applicable",
            sens.SensitivityRequest(
                kind="one_way", metric="per_share", x=axis("exit_multiple", "8")
            ),
        ),
        (
            "out_of_range",
            sens.SensitivityRequest(
                kind="one_way", metric="per_share", x=axis("terminal_g", "0.5")
            ),
        ),
        (
            "invalid_request",
            sens.SensitivityRequest(
                kind="one_way", metric="per_share", x=axis("wacc", "0.1", "0.1")
            ),
        ),
        (
            "invalid_request",
            sens.SensitivityRequest(
                kind="two_way", metric="per_share", x=axis("wacc", "0.1"), y=axis("wacc", "0.09")
            ),
        ),
        (
            "invalid_request",
            sens.SensitivityRequest(kind="two_way", metric="per_share", x=axis("wacc", "0.1")),
        ),
        (
            "invalid_request",
            sens.SensitivityRequest(
                kind="tornado",
                metric="per_share",
                tornado=(sens.TornadoItem("wacc", D("0.11"), D("0.09")),),
            ),
        ),
        (
            "unknown_variable",
            sens.SensitivityRequest(kind="one_way", metric="per_share", x=axis("beta", "1")),
        ),
    ]
    for code, req in cases:
        refuse(code, case, run.id, req)
    assert db.scalar(select(func.count()).select_from(SensitivityRun)) == 0


def test_exit_multiple_runs_accept_the_exit_multiple_variable(db: Session, case: Case) -> None:
    changes = b1_assumptions()
    case.assume(changes)
    case.assume(
        [
            {"key": "terminal_form", "choice": "exit_multiple"},
            {"key": "exit_multiple", "value": "8"},
            {"key": "exit_metric", "choice": "ebitda"},
        ]
    )
    run = case.run().run
    row, _ = grid(
        case,
        run.id,
        sens.SensitivityRequest(
            kind="two_way",
            metric="per_share",
            x=axis("wacc", "0.1"),
            y=axis("exit_multiple", "6", "8", "10"),
        ),
    )
    values = [D(c["value"]) for c in row.cells]
    assert values[1] == run.per_share_value and values[0] < values[1] < values[2]


def test_ownership_archive_and_immutability(db: Session, case: Case) -> None:
    run = b1_run(case)
    req = sens.SensitivityRequest(kind="one_way", metric="per_share", x=axis("terminal_g", "0.03"))
    with pytest.raises(ApiError) as exc:
        sens.create(db, actor(db, "oscar"), run.id, req)
    assert exc.value.code == "not_owner"
    row, _ = grid(case, run.id, req)
    for sql in (
        "UPDATE val.sensitivity_runs SET label = 'x' WHERE id = :i",
        "DELETE FROM val.sensitivity_runs WHERE id = :i",
    ):
        with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
            db.execute(text(sql), {"i": row.id})
    projects.set_status(
        db,
        case.owner,
        case.project.id,
        expected_revision=case.project.revision_no,
        status="archived",
        reason="Superseded by the committee version.",
    )
    refuse("project_archived", case, run.id, req)
