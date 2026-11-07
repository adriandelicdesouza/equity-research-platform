"""Input resolution, runs, gates, content idempotency and reproduction (M3 phase 5, stages 3–4).

Benchmarks B1, B1-VD and B3 (docs/methodology/dcf.md §14) are reproduced end to end: a dataset
built and locked through the real import path, assumptions through the assumption service,
the resolver, the engine and the stored run.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from erp_backend.models import (
    AuditEvent,
    FxRate,
    RunDecision,
    RunInput,
    RunTrace,
    User,
    ValuationRun,
)
from erp_backend.valuation import assumptions as asm
from erp_backend.valuation import projects, runs
from erp_backend.valuation.common import Actor
from erp_backend.valuation.errors import ValuationRefused
from erp_backend.valuation.resolver import (
    Acknowledgement,
    CheckOverride,
    InputOverride,
    RunRequest,
    add_months,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import make_user
from .valuation_helpers import (
    B1_EV,
    B1_ITEMS,
    B1_PRICE,
    B1_VD_PRICE,
    B3_EQUITY,
    B3_PRICE,
    B,
    Fixture,
    b1_assumptions,
    b3_assumptions,
    fx_rate,
    price_observation,
    rate_observation,
    setup,
)

Q6 = Decimal("0.000001")
WHY = "Reviewed with the source filing; acceptable for this valuation."


def actor(db: Session, name: str, role: str = "analyst") -> Actor:
    make_user(db, name, [role])
    return Actor.of(db.scalars(select(User).where(User.username == name)).one())


class Case:
    def __init__(self, db: Session, fx: Fixture, owner: Actor, name: str = "Thesis") -> None:
        self.db, self.fx, self.owner = db, fx, owner
        self.project = projects.create_project(
            db,
            owner,
            company_id=fx.company.id,
            name=name,
            dataset_id=fx.dataset.id,
            listing_id=fx.listing.id,
        )
        self.scenario = projects.base_scenario(db, self.project.id)

    def assume(self, changes: list[dict[str, Any]]) -> asm.State:
        return asm.revise(
            self.db,
            self.owner,
            self.scenario.id,
            expected_revision=self.scenario.revision_no,
            changes=[asm.Change(**c) for c in changes],
        )

    def request(self, method: str = "dcf_fcff", **kw: Any) -> RunRequest:
        args: dict[str, Any] = {
            "methodology": method,
            "scenario_id": self.scenario.id,
            "expected_scenario_revision": self.scenario.revision_no,
            "dataset_id": self.fx.dataset.id,
            "dataset_content_sha256": self.fx.dataset.content_sha256,
            "balance_sheet_period_id": self.fx.period.id,
            "valuation_date": B,
        }
        args.update(kw)
        return RunRequest(**args)

    def run(self, method: str = "dcf_fcff", **kw: Any) -> runs.RunResult:
        admin_reason = kw.pop("admin_reason", None)
        who = kw.pop("who", self.owner)
        return runs.create_run(
            self.db, who, self.project.id, self.request(method, **kw), admin_reason=admin_reason
        )

    def refused(self, code: str, method: str = "dcf_fcff", **kw: Any) -> ValuationRefused:
        with pytest.raises(ValuationRefused) as exc:
            self.run(method, **kw)
        assert exc.value.code == code, exc.value.details
        return exc.value


@pytest.fixture
def case(db: Session, tmp_path: Path) -> Case:
    return Case(db, setup(db, tmp_path), actor(db, "olive"))


def q(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Q6)


# --------------------------------------------------------------------------- benchmarks


def test_b1_fcff_through_the_resolver(db: Session, case: Case) -> None:
    case.assume(b1_assumptions())
    result = case.run()
    run = result.run
    assert not result.existing
    assert q(run.primary_value) == B1_EV and q(run.per_share_value) == B1_PRICE
    out = run.outputs
    assert out["primary"]["kind"] == "enterprise_value"
    assert q(out["cost_of_capital"]["wacc"]) == Decimal("0.1")
    assert q(out["equity_value_b"]) == Decimal("1608.818182")
    assert (run.dataset_id, run.dataset_version_no) == (case.fx.dataset.id, 1)
    assert run.input_document["inputs"]["dataset:operating.nwc_0"]["value"] == "100"
    comps = run.input_document["inputs"]["dataset:bridge.debt"]["components"]
    assert [(c["line_item"], c["value"]) for c in comps] == [
        ("BS.ST_DEBT", "0"),
        ("BS.LT_DEBT", "400"),
    ]
    trace = db.get(RunTrace, run.id)
    assert trace is not None and "discounting.ev" in trace.trace and "wacc.wacc" in trace.trace
    lineage = {
        r.input_key: r for r in db.scalars(select(RunInput).where(RunInput.run_id == run.id))
    }
    assert set(lineage) == set(run.input_document["inputs"])
    assert lineage["assumption:revenue_growth[1]"].value == Decimal("0.10")
    [event] = db.scalars(select(AuditEvent).where(AuditEvent.action == "run_created")).all()
    assert event.after is not None and event.after["output_sha256"] == run.output_sha256


def test_b1_value_driver_default(db: Session, case: Case) -> None:
    case.assume(b1_assumptions(terminal_form="value_driver"))
    assert q(case.run().run.per_share_value) == B1_VD_PRICE


def test_b3_fcfe_no_bridge(db: Session, case: Case) -> None:
    case.assume(b3_assumptions())
    run = case.run("dcf_fcfe").run
    assert run.produces == "equity_value"
    assert q(run.primary_value) == B3_EQUITY and q(run.per_share_value) == B3_PRICE
    doc = run.input_document["inputs"]
    assert (doc["dataset:financing.debt_0"]["value"], doc["dataset:financing.cash_0"]["value"]) == (
        "400",
        "150",
    )
    assert not any(k.startswith("dataset:bridge.") for k in doc)  # FCFE: no bridge (D19)


# --------------------------------------------------------------------------- identity


def test_content_idempotency_and_reproduction(db: Session, case: Case, tmp_path: Path) -> None:
    case.assume(b1_assumptions())
    first = case.run().run
    again = case.run()
    assert again.existing and again.run.id == first.id
    assert db.scalar(select(func.count()).select_from(ValuationRun)) == 1
    rep = runs.reproduce(first)
    assert rep.matches and rep.same_engine and rep.recomputed_output_sha256 == first.output_sha256
    # Same inputs in another project of the same company: a separate run (qualification 2).
    other = Case(db, case.fx, case.owner, name="Second thesis")
    other.assume(b1_assumptions())
    second = other.run().run
    assert second.id != first.id and second.output_sha256 != ""  # identity includes the project
    # A changed valuation date is a different document.
    case.assume([{"key": "terminal_growth", "value": "0.02", "source": None}])
    third = case.run()
    assert not third.existing and third.run.scenario_revision_no == 2


def test_reproduction_never_reads_current_data(db: Session, case: Case, tmp_path: Path) -> None:
    obs = rate_observation(db, tmp_path / "r", value="4.00")
    changes = [c for c in b1_assumptions() if c["key"] != "risk_free_rate"]
    changes.append(
        {
            "key": "risk_free_rate",
            "value": "0.04",
            "source": {"type": "rate_observation", "observation_id": obs.id},
        }
    )
    case.assume(changes)
    run = case.run().run
    assert run.input_document["inputs"]["assumption:risk_free_rate"]["source_value"] == "4"
    rate_observation(db, tmp_path / "r2", value="4.50")  # a revision arrives later
    rep = runs.reproduce(run)  # from the stored document only
    assert rep.matches
    with pytest.raises(ValuationRefused) as exc:
        case.run()  # but a new run refuses the superseded observation
    assert exc.value.code == "input_superseded"


# --------------------------------------------------------------------------- dataset gates


def test_draft_dataset_previews_but_never_runs(db: Session, tmp_path: Path) -> None:
    c = Case(db, setup(db, tmp_path, lock=False), actor(db, "dora"))
    c.assume(b1_assumptions())
    p = runs.preview(db, c.owner, c.project.id, c.request())
    assert p.computation is not None and q(p.computation.per_share_value) == B1_PRICE
    assert [i.code for i in p.resolution.blocking] == ["dataset_not_locked"]
    c.refused("dataset_not_locked")
    assert db.scalar(select(func.count()).select_from(ValuationRun)) == 0


def test_dataset_must_be_the_pinned_version_with_its_hash(
    db: Session, case: Case, tmp_path: Path
) -> None:
    case.assume(b1_assumptions())
    case.refused("dataset_hash_mismatch", dataset_content_sha256="0" * 64)
    other = setup(db, tmp_path / "o", ticker="OTH")
    case.refused("dataset_mismatch", dataset_id=other.dataset.id)
    case.refused("invalid_period", balance_sheet_period_id=other.period.id)
    case.refused("invalid_dates", valuation_date=B - timedelta(days=1))


def test_failed_checks_need_a_reasoned_override(db: Session, tmp_path: Path) -> None:
    items = tuple((c, a, "2251" if c == "BS.TOTAL_ASSETS" else b) for c, a, b in B1_ITEMS)
    c = Case(db, setup(db, tmp_path, items=items), actor(db, "cara"))
    c.assume(b1_assumptions())
    err = c.refused("checks_failed")
    assert err.details[0]["check_code"] == "BALANCE"
    subject = err.details[0]["subject"]
    c.refused("checks_failed", check_overrides=(CheckOverride("BALANCE", subject, "short"),))
    c.refused(
        "invalid_check_override",
        check_overrides=(
            CheckOverride("BALANCE", subject, WHY),
            CheckOverride("SUBTOTAL", "x", WHY),
        ),
    )
    run = c.run(check_overrides=(CheckOverride("BALANCE", subject, WHY),)).run
    [d] = db.scalars(select(RunDecision).where(RunDecision.run_id == run.id)).all()
    assert (d.kind, d.code, d.reason, d.user_id) == (
        "check_override",
        "BALANCE",
        WHY,
        c.owner.user_id,
    )


def test_missing_line_items_are_never_zero(db: Session, tmp_path: Path) -> None:
    items = tuple(i for i in B1_ITEMS if i[0] != "BS.INVENTORY")
    c = Case(db, setup(db, tmp_path, items=items), actor(db, "mia"))
    c.assume(b1_assumptions())
    err = c.refused("input_missing")
    assert {"input": "operating.nwc_0", "line_item": "BS.INVENTORY"}.items() <= err.details[
        0
    ].items()
    c.refused(
        "reason_required",
        input_overrides=(
            InputOverride("operating.nwc_0", "BS.INVENTORY", "not_applicable", "short"),
        ),
    )
    run = c.run(
        input_overrides=(
            InputOverride(
                "operating.nwc_0",
                "BS.INVENTORY",
                "value",
                WHY,
                value="70",
                source="Annual report p. 41",
            ),
        )
    ).run
    assert q(run.per_share_value) == B1_PRICE
    item = run.input_document["inputs"]["dataset:operating.nwc_0"]
    assert item["components"][1]["override"]["source"] == "Annual report p. 41"
    [d] = db.scalars(select(RunDecision).where(RunDecision.run_id == run.id)).all()
    assert (d.kind, d.code) == ("input_override", "operating.nwc_0/BS.INVENTORY")


# --------------------------------------------------------------------------- assumptions & dates


def test_unapproved_or_missing_assumptions(db: Session, case: Case) -> None:
    changes = b1_assumptions()
    for ch in changes:
        if ch["key"] == "terminal_growth":
            ch["status"] = "suggested"
    case.assume([c for c in changes if c["key"] != "mature_market_erp"])
    err = case.refused("input_missing")
    assert any(d.get("input") == "mature_market_erp" for d in err.details)
    case.assume([c for c in changes if c["key"] == "mature_market_erp"])
    err = case.refused("assumption_not_approved")
    assert err.details[0]["input"] == "terminal_growth"
    p = runs.preview(case.db, case.owner, case.project.id, case.request())
    assert p.computation is not None  # previews show suggested values, labelled as gates


def test_lookahead_is_an_error_staleness_a_warning(db: Session, case: Case) -> None:
    changes = [c for c in b1_assumptions() if c["key"] != "risk_free_rate"]
    late = {"type": "analyst", "reference": "Desk", "as_of": "2025-01-05", "currency": "USD"}
    case.assume([*changes, {"key": "risk_free_rate", "value": "0.04", "source": late}])
    case.refused("input_lookahead")
    old = {**late, "as_of": "2024-12-20"}
    case.assume([{"key": "risk_free_rate", "value": "0.04", "source": old}])
    run = case.run().run
    assert [w["code"] for w in run.warnings] == ["input_stale"]
    assert run.input_document["inputs"]["assumption:risk_free_rate"]["warnings"] == ["input_stale"]
    eur = {**old, "as_of": "2024-12-31", "currency": "EUR"}
    case.assume([{"key": "risk_free_rate", "value": "0.04", "source": eur}])
    case.refused("currency_mismatch")


def test_add_months() -> None:
    assert add_months(date(2024, 1, 31), 13) == date(2025, 2, 28)
    assert add_months(date(2023, 12, 5), 13) == date(2025, 1, 5)


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("cls", "code"),
    [
        (None, "classification_unconfirmed"),
        ("bank", "classification_blocked"),
        ("insurer", "classification_blocked"),
        ("reit", "acknowledgement_required"),
        ("asset_manager_broker", "acknowledgement_required"),
    ],
)
def test_classification_gates(db: Session, tmp_path: Path, cls: str | None, code: str) -> None:
    c = Case(db, setup(db, tmp_path, classification=cls), actor(db, "cleo"))
    c.assume(b1_assumptions())
    c.refused(code)


def test_reit_runs_with_a_reasoned_acknowledgement(db: Session, tmp_path: Path) -> None:
    c = Case(db, setup(db, tmp_path, classification="reit"), actor(db, "rhea"))
    c.assume(b1_assumptions())
    c.refused(
        "acknowledgement_required",
        acknowledgements=(Acknowledgement("reit_fair_value_gains", "ok"),),
    )
    c.refused(
        "invalid_acknowledgement",
        acknowledgements=(
            Acknowledgement("reit_fair_value_gains", WHY),
            Acknowledgement("client_cash_excluded", WHY),
        ),
    )
    run = c.run(acknowledgements=(Acknowledgement("reit_fair_value_gains", WHY),)).run
    assert any(v["code"] == "reit_fair_value_gains" for v in run.validation)
    [d] = db.scalars(select(RunDecision).where(RunDecision.run_id == run.id)).all()
    assert (d.kind, d.code, d.detail) == (
        "classification_acknowledgement",
        "reit_fair_value_gains",
        {"classification": "reit"},
    )


# --------------------------------------------------------------------------- market inputs


def test_market_weights_and_exact_fx_reciprocal(db: Session, case: Case, tmp_path: Path) -> None:
    changes = [
        c
        for c in b1_assumptions()
        if c["key"] not in {"capital_structure_basis", "target_debt_to_equity"}
    ]
    changes.append({"key": "capital_structure_basis", "choice": "market"})
    case.assume(changes)
    case.refused("input_missing")  # market weights need a price
    price = price_observation(
        db, tmp_path / "p", case.fx.listing, close="27.00", trade_date=B, currency="CAD"
    )
    case.refused("input_missing", price_observation_id=price.id)  # CAD price, USD dataset: FX
    fx = fx_rate(db, tmp_path / "f", base="USD", quote="CAD", rate="1.35", obs_date=B)
    before = db.scalar(select(func.count()).select_from(FxRate))
    run = case.run(price_observation_id=price.id, fx_rate_ids=(fx.id,)).run
    assert db.scalar(select(func.count()).select_from(FxRate)) == before  # nothing derived stored
    item = run.input_document["inputs"]["market:fx"]
    assert (item["conversion"], item["source_value"], item["published_direction"]) == (
        "reciprocal",
        "1.35",
        "CAD per 1 USD",
    )
    trace = db.get(RunTrace, run.id)
    assert trace is not None
    node = trace.trace["market.fx_reciprocal"]
    assert "1 / 1.35" in node["rule"] and node["inputs"] == ["market:fx"]
    price_usd = Decimal(run.outputs["market"]["price_reporting"])
    assert price_usd == Decimal("20.00000000000000000000000000000000")  # 27 / 1.35, exact
    w = run.outputs["cost_of_capital"]
    assert q(w["weight_equity"]) == q(Decimal(2000) / Decimal(2400))  # 20 × 100 vs debt 400
    assert run.outputs["market"]["upside"] is not None


def test_stale_price_and_unneeded_fx(db: Session, case: Case, tmp_path: Path) -> None:
    case.assume(b1_assumptions())
    old = price_observation(
        db,
        tmp_path / "p",
        case.fx.listing,
        close="15",
        currency="USD",
        trade_date=B - timedelta(days=10),
    )
    fx = fx_rate(db, tmp_path / "f", base="USD", quote="CAD", rate="1.35", obs_date=B)
    case.refused("invalid_input", price_observation_id=old.id, fx_rate_ids=(fx.id,))
    run = case.run(price_observation_id=old.id).run
    assert [w["code"] for w in run.warnings] == ["input_stale"]
    assert q(run.outputs["market"]["upside"]) == q(B1_PRICE / 15 - 1)


# --------------------------------------------------------------------------- ownership


def test_admin_runs_on_another_users_project_with_reason(db: Session, case: Case) -> None:
    case.assume(b1_assumptions())
    admin = actor(db, "ada", "admin")
    from erp_backend.errors import ApiError

    with pytest.raises(ApiError, match="reason"):
        case.run(who=admin)
    run = case.run(who=admin, admin_reason="Owner on leave; run for committee pack.").run
    [d] = db.scalars(select(RunDecision).where(RunDecision.run_id == run.id)).all()
    assert (d.kind, d.user_id) == ("admin_override", admin.user_id)
    with pytest.raises(ApiError, match="owner"):
        case.run(who=actor(db, "stan"))
