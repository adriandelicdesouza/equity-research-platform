"""Valuation projects and assumptions services (M3 phase 5, stage 2; design §3, §9.3; Q3)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from erp_backend.datasets import discard_dataset
from erp_backend.errors import ApiError
from erp_backend.models import AssumptionRevision, AuditEvent, Scenario, User
from erp_backend.valuation import assumptions as asm
from erp_backend.valuation import projects
from erp_backend.valuation.blocks import block_from_row, current_block
from erp_backend.valuation.common import Actor
from erp_backend.valuation.errors import ValuationRefused
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .conftest import make_user
from .valuation_helpers import (
    ERP_CSV,
    REASON,
    b1_assumptions,
    make_company,
    rate_observation,
    reference_dataset,
    setup,
)

ADMIN_REASON = "Owner is away; correcting the thesis on request."


def actor(db: Session, name: str, role: str = "analyst") -> Actor:
    make_user(db, name, [role])
    return Actor.of(db.scalars(select(User).where(User.username == name)).one())


def changes(raw: list[dict[str, object]]) -> list[asm.Change]:
    return [asm.Change(**c) for c in raw]  # type: ignore[arg-type]


def events(db: Session, action: str) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


@pytest.fixture
def ctx(db: Session, tmp_path: Path):  # type: ignore[no-untyped-def]
    fx = setup(db, tmp_path)
    owner = actor(db, "olive")
    p = projects.create_project(
        db,
        owner,
        company_id=fx.company.id,
        name="Core thesis",
        dataset_id=fx.dataset.id,
        listing_id=fx.listing.id,
    )
    return fx, owner, p


# --------------------------------------------------------------------------- projects


def test_create_project_with_base_scenario_and_audit(db: Session, ctx) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    assert (p.owner_user_id, p.status, p.revision_no) == (owner.user_id, "active", 1)
    base = projects.base_scenario(db, p.id)
    assert (base.kind, base.name, base.revision_no) == ("base", "Base", 0)
    [ev] = events(db, "project_created")
    assert ev.after is not None and ev.after["base_scenario_id"] == str(base.id)
    assert ev.after["dataset_status"] == "locked"


def test_create_refuses_foreign_or_discarded_dataset_listing_and_duplicates(
    db: Session,
    ctx,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    fx, owner, _ = ctx
    other = setup(db, tmp_path / "o", ticker="OTH")
    for kw, code in (
        ({"dataset_id": other.dataset.id}, "dataset_mismatch"),
        ({"listing_id": other.listing.id}, "listing_mismatch"),
        ({"name": " core THESIS "}, "duplicate_name"),
    ):
        args = {"company_id": fx.company.id, "name": "Another", "dataset_id": fx.dataset.id, **kw}
        with pytest.raises(ValuationRefused) as exc:
            projects.create_project(db, owner, **args)  # type: ignore[arg-type]
        assert exc.value.code == code
    company, listing = make_company(db, ticker="DRF")
    from .valuation_helpers import build_dataset

    draft = build_dataset(db, company, listing, tmp_path / "d", lock=False)
    discard_dataset(db, draft.id, reason=REASON)
    with pytest.raises(ValuationRefused, match="discarded"):
        projects.create_project(db, owner, company_id=company.id, name="X", dataset_id=draft.id)
    with pytest.raises(ApiError, match="No company"):
        projects.create_project(db, owner, company_id=uuid.uuid4(), name="X", dataset_id=draft.id)


def test_owner_edits_others_refused_admin_needs_reason(db: Session, ctx) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    stranger, admin = actor(db, "stan"), actor(db, "ada", "admin")
    with pytest.raises(ApiError) as exc:
        projects.update_metadata(db, stranger, p.id, expected_revision=1, changes={"thesis": "x"})
    assert (exc.value.status_code, exc.value.code) == (403, "not_owner")
    with pytest.raises(ApiError) as exc:
        projects.update_metadata(db, admin, p.id, expected_revision=1, changes={"thesis": "x"})
    assert exc.value.code == "admin_reason_required"
    projects.update_metadata(
        db, admin, p.id, expected_revision=1, changes={"thesis": "Moat"}, admin_reason=ADMIN_REASON
    )
    [ev] = events(db, "project_updated")
    assert ev.user_id == admin.user_id and ev.reason == ADMIN_REASON
    assert ev.after is not None and ev.after["admin_override"] is True
    assert ev.before is not None and ev.before["thesis"] is None and ev.after["thesis"] == "Moat"
    with pytest.raises(ValuationRefused) as rexc:
        projects.update_metadata(db, owner, p.id, expected_revision=1, changes={"name": "N"})
    assert rexc.value.code == "revision_conflict"
    projects.update_metadata(db, owner, p.id, expected_revision=2, changes={"name": "Renamed"})
    assert (p.name, p.revision_no) == ("Renamed", 3)


def test_rebase_archive_restore_and_transfer(db: Session, ctx, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    fx, owner, p = ctx
    from .valuation_helpers import B1_ITEMS, build_dataset

    items = (*B1_ITEMS, ("IS.COGS", "500", "550"))  # a new item: next version, no conflict
    v2 = build_dataset(db, fx.company, fx.listing, tmp_path / "v2", items=items, lock=False)
    with pytest.raises(ValuationRefused, match="hash"):
        projects.rebase(
            db,
            owner,
            p.id,
            expected_revision=1,
            dataset_id=v2.id,
            content_sha256="0" * 64,
            reason=REASON,
        )
    with pytest.raises(ValuationRefused) as exc:
        projects.rebase(
            db,
            owner,
            p.id,
            expected_revision=1,
            dataset_id=v2.id,
            content_sha256=v2.content_sha256,
            reason="short",
        )
    assert exc.value.code == "reason_required"
    projects.rebase(
        db,
        owner,
        p.id,
        expected_revision=1,
        dataset_id=v2.id,
        content_sha256=v2.content_sha256,
        reason=REASON,
    )
    assert p.dataset_id == v2.id
    projects.set_status(db, owner, p.id, expected_revision=2, status="archived", reason=REASON)
    with pytest.raises(ValuationRefused, match="archived"):
        projects.update_metadata(db, owner, p.id, expected_revision=3, changes={"thesis": "x"})
    projects.set_status(db, owner, p.id, expected_revision=3, status="active", reason=REASON)
    analyst2, reviewer, admin = (
        actor(db, "bob"),
        actor(db, "rev", "reviewer"),
        actor(db, "ad", "admin"),
    )
    with pytest.raises(ApiError, match="administrator"):
        projects.transfer_owner(
            db, owner, p.id, expected_revision=4, user_id=analyst2.user_id, reason=REASON
        )
    with pytest.raises(ValuationRefused, match="valuation:write"):
        projects.transfer_owner(
            db, admin, p.id, expected_revision=4, user_id=reviewer.user_id, reason=REASON
        )
    projects.transfer_owner(
        db, admin, p.id, expected_revision=4, user_id=analyst2.user_id, reason=REASON
    )
    assert p.owner_user_id == analyst2.user_id
    actions = db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == str(p.id)))
    assert sorted(actions) == sorted(
        [
            "project_created",
            "project_rebased",
            "project_archived",
            "project_restored",
            "project_owner_changed",
        ]
    )


# --------------------------------------------------------------------------- assumptions


def test_revise_appends_and_bumps_scenario_revision(db: Session, ctx) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    base = projects.base_scenario(db, p.id)
    state = asm.revise(db, owner, base.id, expected_revision=0, changes=changes(b1_assumptions()))
    assert state.scenario_revision == 1
    assert state.value("revenue_growth", 2) == Decimal("0.08")
    assert state.value("convention") == "end_of_period"
    rf = state.get("risk_free_rate")
    assert rf is not None and rf.row.source == {
        "type": "analyst",
        "reference": "Benchmark B1 (methodology §14)",
        "as_of": "2024-12-31",
        "currency": "USD",
    }
    state2 = asm.revise(
        db,
        owner,
        base.id,
        expected_revision=1,
        changes=changes([{"key": "terminal_growth", "value": "0.025"}]),
    )
    assert state2.value("terminal_growth") == Decimal("0.025")
    history = db.scalars(
        select(AssumptionRevision)
        .where(AssumptionRevision.key == "terminal_growth")
        .order_by(AssumptionRevision.id)
    ).all()
    assert [h.value for h in history] == [Decimal("0.02"), Decimal("0.025")]
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(text("UPDATE val.assumption_revisions SET value = 1"))
    [ev, _] = events(db, "assumptions_revised")
    assert ev.after is not None and ev.after["scenario_revision"] == 1
    with pytest.raises(ValuationRefused, match="changed since"):
        asm.revise(
            db,
            owner,
            base.id,
            expected_revision=1,
            changes=changes([{"key": "terminal_growth", "value": "0.03"}]),
        )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"key": "nope", "value": "1"}, "unknown_key"),
        ({"key": "revenue_growth", "value": "0.1"}, "invalid_period"),
        ({"key": "terminal_growth", "period_index": 1, "value": "0.1"}, "invalid_period"),
        ({"key": "tax_rate", "period_index": 1, "value": "25"}, "out_of_range"),
        ({"key": "terminal_growth", "value": "2%"}, "invalid_value"),
        ({"key": "convention", "choice": "monthly"}, "invalid_value"),
        ({"key": "horizon_years", "value": "2.5"}, "invalid_value"),
        ({"key": "risk_free_rate", "value": "0.04"}, "source_required"),
        (
            {
                "key": "risk_free_rate",
                "value": "0.04",
                "source": {"type": "analyst", "reference": "desk note"},
            },
            "source_required",
        ),
        (
            {
                "key": "mature_market_erp",
                "value": "0.05",
                "source": {"type": "rate_observation", "observation_id": 1},
            },
            "source_invalid",
        ),
        ({"key": "crp_country", "text": "usa"}, "invalid_value"),
        (
            {
                "key": "instruments",
                "json": [{"id": "o", "kind": "option", "count": "0", "treatment": "tsm"}],
            },
            "invalid_value",
        ),
        ({"key": "terminal_growth", "value": "0.02", "status": "final"}, "invalid_status"),
    ],
)
def test_revise_validation(db: Session, ctx, change: dict[str, object], code: str) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    base = projects.base_scenario(db, p.id)
    with pytest.raises(ValuationRefused) as exc:
        asm.revise(db, owner, base.id, expected_revision=0, changes=changes([change]))
    assert exc.value.code == code
    assert base.revision_no == 0
    assert db.scalar(select(AssumptionRevision.id)) is None  # nothing partial persisted


def test_sourced_values_must_match_their_source(db: Session, ctx, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    base = projects.base_scenario(db, p.id)
    obs = rate_observation(db, tmp_path, value="4.25")
    src = {"type": "rate_observation", "observation_id": obs.id}
    with pytest.raises(ValuationRefused) as exc:
        asm.revise(
            db,
            owner,
            base.id,
            expected_revision=0,
            changes=changes([{"key": "risk_free_rate", "value": "4.25", "source": src}]),
        )
    assert exc.value.code == "out_of_range"  # percentage points are not fractions
    with pytest.raises(ValuationRefused) as exc:
        asm.revise(
            db,
            owner,
            base.id,
            expected_revision=0,
            changes=changes([{"key": "risk_free_rate", "value": "0.04", "source": src}]),
        )
    assert exc.value.code == "source_mismatch"
    state = asm.revise(
        db,
        owner,
        base.id,
        expected_revision=0,
        changes=changes([{"key": "risk_free_rate", "value": "0.0425", "source": src}]),
    )
    row = state.get("risk_free_rate").row  # type: ignore[union-attr]
    assert row.provenance_kind == "provider_market"
    assert row.source is not None and (
        row.source["published_value"],
        row.source["conversion"],
        row.source["series_code"],
    ) == ("4.25", "pp_to_fraction", "UST_PAR_10Y")
    ds_id = reference_dataset(
        db,
        tmp_path / "ref",
        template="damodaran_implied_erp.v1",
        data=ERP_CSV,
        edition=date(2024, 12, 5),
    )
    from erp_backend.models import ReferenceValue

    ref = db.scalars(
        select(ReferenceValue).where(
            ReferenceValue.dataset_id == ds_id, ReferenceValue.field == "implied_erp"
        )
    ).one()
    state = asm.revise(
        db,
        owner,
        base.id,
        expected_revision=1,
        changes=changes(
            [
                {
                    "key": "mature_market_erp",
                    "value": "0.05",
                    "source": {
                        "type": "reference_value",
                        "reference_dataset_id": str(ds_id),
                        "record_key": ref.record_key,
                        "field": "implied_erp",
                    },
                }
            ]
        ),
    )
    erp = state.get("mature_market_erp").row  # type: ignore[union-attr]
    assert erp.provenance_kind == "reference" and erp.source["published_unit"] == "percent"  # type: ignore[index]


def test_approve_and_clear(db: Session, ctx) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    base = projects.base_scenario(db, p.id)
    asm.revise(
        db,
        owner,
        base.id,
        expected_revision=0,
        changes=changes(
            [
                {"key": "terminal_growth", "value": "0.02", "status": "suggested"},
                {"key": "convention", "choice": "mid_period"},
            ]
        ),
    )
    with pytest.raises(ValuationRefused) as exc:
        asm.approve(db, owner, base.id, expected_revision=1, items=[("convention", None)])
    assert exc.value.code == "not_suggested"
    state = asm.approve(db, owner, base.id, expected_revision=1, items=[("terminal_growth", None)])
    tg = state.get("terminal_growth")
    assert tg is not None and tg.approved and tg.row.value == Decimal("0.02")
    assert events(db, "assumptions_approved")
    state = asm.revise(
        db,
        owner,
        base.id,
        expected_revision=2,
        changes=changes([{"key": "convention", "clear": True}]),
    )
    assert state.get("convention") is None
    other = actor(db, "otto")
    with pytest.raises(ApiError, match="owner"):
        asm.revise(
            db,
            other,
            base.id,
            expected_revision=3,
            changes=changes([{"key": "terminal_growth", "value": "0.03"}]),
        )


def test_required_keys_follow_settings(db: Session, ctx) -> None:  # type: ignore[no-untyped-def]
    _, owner, p = ctx
    base = projects.base_scenario(db, p.id)
    state = asm.revise(
        db,
        owner,
        base.id,
        expected_revision=0,
        changes=changes(b1_assumptions(terminal_form="value_driver")),
    )
    block = block_from_row(current_block(db, "non_financial", "dcf_fcff"))
    need = asm.required_keys(state, "dcf_fcff", block)
    assert ("ronic", None) in need and ("revenue_growth", 3) in need
    assert ("revenue_growth", 4) not in need and ("beta_levered", None) in need
    assert ("target_debt_to_equity", None) in need and ("credit_spread", None) not in need
    assert all(state.get(*k) is not None for k in need)
    fcfe = block_from_row(current_block(db, "non_financial", "dcf_fcfe"))
    assert ("debt_forecast", 1) in asm.required_keys(state, "dcf_fcfe", fcfe)


def test_scenario_lookup_is_project_scoped(db: Session, ctx) -> None:  # type: ignore[no-untyped-def]
    _, _, p = ctx
    stray = db.scalars(select(Scenario)).first()
    assert stray is not None
    asm.scenario_for_project(db, p, stray.id)
    with pytest.raises(ApiError, match="No scenario"):
        asm.scenario_for_project(db, p, uuid.uuid4())
