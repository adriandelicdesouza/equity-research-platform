"""Valuation API (M3 phase 5, stage 5): permissions, CSRF/Origin, the full workflow over HTTP,
refusal envelopes, ownership, idempotency and reproduction."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from erp_backend.auth.deps import POLICY_ATTR
from erp_backend.main import create_app
from erp_backend.models import ApiAction, ValuationRun
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .test_rbac import _api_routes, _policies
from .valuation_helpers import B1_PRICE, B, b1_assumptions, setup
from .workflow_helpers import Actor, as_role

V = "/api/valuation"
ADMIN_REASON = "Owner unavailable; preparing the committee pack."


def test_every_valuation_route_requires_the_valuation_permissions() -> None:
    routes = [r for r in _api_routes(create_app().routes) if r.path.startswith("/valuation")]
    assert len(routes) == 34
    for route in routes:
        [policy] = _policies(route.dependant)
        read_only = route.methods == {"GET"} or route.path.endswith("/reproduce")
        expected = "permission:valuation:read" if read_only else "permission:valuation:write"
        assert policy == expected, (route.methods, route.path)
    assert POLICY_ATTR


def _project(actor: Actor, fx: Any, name: str = "Thesis") -> dict[str, Any]:
    r = actor.post(
        f"{V}/projects",
        json={
            "company_id": str(fx.company.id),
            "name": name,
            "dataset_id": str(fx.dataset.id),
            "listing_id": str(fx.listing.id),
        },
    )
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body["result"]


def _run_body(fx: Any, scenario: dict[str, Any], **kw: Any) -> dict[str, Any]:
    body = {
        "methodology": "dcf_fcff",
        "scenario_id": scenario["id"],
        "expected_scenario_revision": scenario["revision_no"],
        "dataset": {"id": str(fx.dataset.id), "content_sha256": fx.dataset.content_sha256},
        "balance_sheet_period_id": str(fx.period.id),
        "valuation_date": B.isoformat(),
    }
    body.update(kw)
    return body


def _assume(actor: Actor, scenario_id: str, revision: int, changes: list[dict[str, Any]]) -> Any:
    return actor.post(
        f"{V}/scenarios/{scenario_id}/assumptions",
        json={"expected_revision": revision, "changes": changes},
    )


def test_reviewer_reads_but_cannot_write(
    wf_client: TestClient, db: Session, tmp_path: Path
) -> None:
    fx = setup(db, tmp_path)
    rev = as_role(wf_client, db, "rita", ["reviewer"])
    assert wf_client.get(f"{V}/projects").status_code == 200
    assert wf_client.get(f"{V}/assumption-keys").status_code == 200
    r = rev.post(
        f"{V}/projects",
        json={"company_id": str(fx.company.id), "name": "X", "dataset_id": str(fx.dataset.id)},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")


def test_csrf_origin_and_authentication(wf_client: TestClient, db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    body = {"company_id": str(fx.company.id), "name": "X", "dataset_id": str(fx.dataset.id)}
    assert wf_client.get(f"{V}/projects").status_code == 401
    ana = as_role(wf_client, db, "andy", ["analyst"])
    r = ana.post(f"{V}/projects", json=body, csrf=None)
    assert r.json()["error"]["code"] == "csrf_failed"
    r = ana.post(f"{V}/projects", json=body, origin="https://evil.example")
    assert r.json()["error"]["code"] == "origin_rejected"
    r = ana.post(f"{V}/projects", json={**body, "extra": 1})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
    assert db.scalar(select(func.count()).select_from(ApiAction)) == 0


def test_full_workflow_over_http(wf_client: TestClient, db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    ana = as_role(wf_client, db, "andy", ["analyst"])
    project = _project(ana, fx)
    detail = wf_client.get(f"{V}/projects/{project['id']}").json()
    [scenario] = detail["scenarios"]
    assert (detail["classification"], scenario["kind"], detail["dataset"]["status"]) == (
        "non_financial",
        "base",
        "locked",
    )
    state = wf_client.get(f"{V}/scenarios/{scenario['id']}/assumptions").json()
    assert ("horizon_years", None) in {
        (m["key"], m["period_index"]) for m in state["missing_required"]
    }
    r = _assume(ana, scenario["id"], 0, b1_assumptions())
    assert r.status_code == 200, r.text
    assert (
        r.json()["result"]["missing_required"] == []
        and r.json()["result"]["scenario_revision"] == 1
    )
    scenario["revision_no"] = 1
    hist = wf_client.get(
        f"{V}/scenarios/{scenario['id']}/assumptions/history", params={"key": "terminal_growth"}
    ).json()
    assert hist["total"] == 1 and hist["items"][0]["value"] == "0.02"
    cands = wf_client.get(
        f"{V}/projects/{project['id']}/input-candidates", params={"valuation_date": B.isoformat()}
    ).json()
    assert cands["periods"][0]["id"] == str(fx.period.id)
    assert cands["rates"] == [] and cands["notes"]
    ctx = wf_client.get(f"{V}/projects/{project['id']}/historical-context").json()
    last = ctx["years"][-1]
    assert (last["fiscal_year"], last["ebit_margin"], last["nwc_pct"]) == (2024, "0.2", "0.1")
    preview = ana.post(f"{V}/projects/{project['id']}/preview", json=_run_body(fx, scenario))
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["can_save"] and p["issues"] == []
    assert p["outputs"]["per_share"].startswith(str(B1_PRICE)[:8])
    assert db.scalar(select(func.count()).select_from(ValuationRun)) == 0  # preview stores nothing
    created = ana.post(
        f"{V}/projects/{project['id']}/runs",
        json=_run_body(fx, scenario, label="B1 check"),
        key="run-b1-0001",
    )
    assert created.status_code == 201, created.text
    run = created.json()["result"]["run"]
    assert created.json()["result"]["existing"] is False and run["label"] == "B1 check"
    replay = ana.post(
        f"{V}/projects/{project['id']}/runs",
        json=_run_body(fx, scenario, label="B1 check"),
        key="run-b1-0001",
    )
    assert replay.headers.get("Idempotent-Replayed") == "true"
    same = ana.post(f"{V}/projects/{project['id']}/runs", json=_run_body(fx, scenario))
    assert (same.status_code, same.json()["result"]["existing"]) == (200, True)
    assert same.json()["result"]["run"]["id"] == run["id"]
    action = db.scalars(select(ApiAction).where(ApiAction.action == "valuation.run_create")).first()
    stored = db.get(ValuationRun, run["id"])
    assert stored is not None and action is not None and stored.action_id == action.id
    listed = wf_client.get(f"{V}/projects/{project['id']}/runs").json()
    assert listed["total"] == 1
    detail_run = wf_client.get(f"{V}/runs/{run['id']}").json()
    assert detail_run["outputs"]["primary"]["kind"] == "enterprise_value"
    assert detail_run["trace_node_count"] > 10 and detail_run["decisions"] == []
    inputs = wf_client.get(f"{V}/runs/{run['id']}/inputs").json()
    assert inputs["input_document"]["dataset"]["id"] == str(fx.dataset.id)
    assert any(r["input_key"] == "dataset:bridge.cash" for r in inputs["lineage"])
    trace = wf_client.get(
        f"{V}/runs/{run['id']}/trace", params={"node": "discounting.ev", "depth": 1}
    ).json()
    assert trace["root"] == "discounting.ev" and "discounting.pv_tv" in trace["nodes"]
    assert wf_client.get(f"{V}/runs/{run['id']}/trace", params={"node": "nope"}).status_code == 404
    rev = as_role(wf_client, db, "rita", ["reviewer"])
    rep = rev.post(f"{V}/runs/{run['id']}/reproduce")
    assert rep.status_code == 200 and rep.json()["matches"] is True
    assert rep.json()["stored_output_sha256"] == rep.json()["recomputed_output_sha256"]


def test_refusals_carry_codes_and_details(
    wf_client: TestClient, db: Session, tmp_path: Path
) -> None:
    fx = setup(db, tmp_path, lock=False)
    ana = as_role(wf_client, db, "andy", ["analyst"])
    project = _project(ana, fx)
    scenario = wf_client.get(f"{V}/projects/{project['id']}/scenarios").json()[0]
    _assume(ana, scenario["id"], 0, b1_assumptions())
    scenario["revision_no"] = 1
    preview = ana.post(f"{V}/projects/{project['id']}/preview", json=_run_body(fx, scenario)).json()
    assert preview["can_save"] is False and preview["outputs"] is not None
    assert [i["code"] for i in preview["issues"]] == ["dataset_not_locked"]
    r = ana.post(f"{V}/projects/{project['id']}/runs", json=_run_body(fx, scenario))
    err = r.json()["error"]
    assert (r.status_code, err["code"]) == (409, "dataset_not_locked")
    assert err["details"][0]["level"] == "gate" and err["action_id"]
    refused = db.scalars(select(ApiAction).where(ApiAction.outcome == "refused")).one()
    assert refused.status_code == 409
    stale = _assume(ana, scenario["id"], 0, [{"key": "terminal_growth", "value": "0.03"}])
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "revision_conflict")
    bad = _assume(ana, scenario["id"], 1, [{"key": "tax_rate", "period_index": 1, "value": "25"}])
    assert bad.json()["error"]["code"] == "out_of_range"
    assert bad.json()["error"]["details"][0]["key"] == "tax_rate"


def test_ownership_and_admin_override(wf_client: TestClient, db: Session, tmp_path: Path) -> None:
    fx = setup(db, tmp_path)
    owner = as_role(wf_client, db, "olive", ["analyst"])
    project = _project(owner, fx)
    other = as_role(wf_client, db, "stan", ["analyst"])
    body = {"expected_revision": 1, "thesis": "Changed"}
    r = other.post(f"{V}/projects/{project['id']}/metadata", json=body)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "not_owner")
    admin = as_role(wf_client, db, "ada", ["admin"])
    r = admin.post(f"{V}/projects/{project['id']}/metadata", json=body)
    assert r.json()["error"]["code"] == "admin_reason_required"
    r = admin.post(
        f"{V}/projects/{project['id']}/metadata", json={**body, "admin_reason": ADMIN_REASON}
    )
    assert r.status_code == 200 and r.json()["result"]["thesis"] == "Changed"
    r = admin.post(
        f"{V}/projects/{project['id']}/archive",
        json={
            "expected_revision": 2,
            "reason": "Superseded by a new thesis.",
            "admin_reason": ADMIN_REASON,
        },
    )
    assert r.json()["result"]["status"] == "archived"
    mine = wf_client.get(f"{V}/projects", params={"owner": "me"}).json()
    assert mine["total"] == 0  # the admin owns none


def test_scenarios_probabilities_and_sensitivities_over_http(
    wf_client: TestClient, db: Session, tmp_path: Path
) -> None:
    """Phase 5b over HTTP: a bull scenario inheriting from base, probabilities, the weighted
    summary, a two-way grid with refused cells, idempotency and reviewer read-only access."""
    fx = setup(db, tmp_path)
    ana = as_role(wf_client, db, "andy", ["analyst"])
    project = _project(ana, fx)
    pid = project["id"]
    [base] = wf_client.get(f"{V}/projects/{pid}/scenarios").json()
    assert _assume(ana, base["id"], 0, b1_assumptions()).status_code == 200
    base["revision_no"] = 1
    r = ana.post(
        f"{V}/projects/{pid}/scenarios",
        json={"name": "Bull", "kind": "bull", "parent_scenario_id": base["id"]},
    )
    assert r.status_code == 201, r.text
    bull = r.json()["result"]
    assert (bull["kind"], bull["parent_scenario_id"], bull["revision_no"]) == (
        "bull",
        base["id"],
        0,
    )
    state = wf_client.get(f"{V}/scenarios/{bull['id']}/assumptions").json()
    assert state["missing_required"] == [] and state["resolved"] is True
    assert {i["inherited_from"] for i in state["items"]} == {base["id"]}
    own = wf_client.get(
        f"{V}/scenarios/{bull['id']}/assumptions", params={"resolved": "false"}
    ).json()
    assert own["items"] == []
    changes = [{"key": "ebit_margin", "period_index": k, "value": "0.25"} for k in (1, 2, 3)]
    assert _assume(ana, bull["id"], 0, changes).status_code == 200
    bull["revision_no"] = 1
    for s in (base, bull):
        created = ana.post(f"{V}/projects/{pid}/runs", json=_run_body(fx, s))
        assert created.status_code == 201, created.text
        s["run"] = created.json()["result"]["run"]
    detail = wf_client.get(f"{V}/projects/{pid}").json()
    bad = ana.post(
        f"{V}/projects/{pid}/probabilities",
        json={"expected_revision": detail["revision_no"], "weights": {base["id"]: "0.5"}},
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (409, "weights_not_one")
    ok = ana.post(
        f"{V}/projects/{pid}/probabilities",
        json={
            "expected_revision": detail["revision_no"],
            "weights": {base["id"]: "0.6", bull["id"]: "0.4"},
        },
    )
    assert ok.status_code == 200, ok.text
    summary = wf_client.get(f"{V}/projects/{pid}/scenario-summary").json()
    assert summary["complete"] is True and summary["issues"] == []
    assert summary["probabilities_sum"] == "1.0"
    lo, hi = (Decimal(base["run"]["per_share_value"]), Decimal(bull["run"]["per_share_value"]))
    assert lo < Decimal(summary["weighted_per_share"]) < hi
    body = {
        "kind": "two_way",
        "metric": "per_share",
        "x": {"variable": "wacc", "values": ["0.09", "0.10", "0.11"]},
        "y": {"variable": "terminal_g", "values": ["0.02", "0.10"]},
        "label": "WACC × g",
    }
    run_id = base["run"]["id"]
    made = ana.post(f"{V}/runs/{run_id}/sensitivities", json=body)
    assert made.status_code == 201, made.text
    grid = made.json()["result"]["sensitivity"]
    assert (grid["cell_count"], grid["refused_count"], grid["label"]) == (6, 2, "WACC × g")
    center = next(c for c in grid["cells"] if (c["x"], c["y"]) == ("0.10", "0.02"))
    assert center["value"] == base["run"]["per_share_value"]
    again = ana.post(f"{V}/runs/{run_id}/sensitivities", json=body)
    assert (again.status_code, again.json()["result"]["existing"]) == (200, True)
    listed = wf_client.get(f"{V}/runs/{run_id}/sensitivities").json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == grid["id"]
    assert wf_client.get(f"{V}/sensitivities/{grid['id']}").json()["cells"] == grid["cells"]
    big = {
        **body,
        "x": {"variable": "wacc", "values": [f"0.{i:02d}" for i in range(5, 16)]},
        "y": {"variable": "terminal_g", "values": [f"0.00{i}" for i in range(10)]},
    }
    too = ana.post(f"{V}/runs/{run_id}/sensitivities", json=big)
    assert (too.status_code, too.json()["error"]["code"]) == (409, "grid_too_large")
    exact = {
        **body,
        "kind": "one_way",
        "y": None,
        "x": {"variable": "wacc", "values": ["0.1000000000000000000000000001"]},
    }
    assert ana.post(f"{V}/runs/{run_id}/sensitivities", json=exact).status_code == 201
    variables = wf_client.get(f"{V}/sensitivity-variables").json()
    assert {v["variable"] for v in variables} >= {"wacc", "terminal_g", "exit_multiple"}
    archive = ana.post(
        f"{V}/scenarios/{bull['id']}/archive",
        json={"expected_revision": bull["revision_no"], "reason": "Folded into the base case."},
    )
    assert (archive.status_code, archive.json()["error"]["code"]) == (409, "scenario_weighted")
    rev = as_role(wf_client, db, "rita", ["reviewer"])
    for path, payload in (
        (f"{V}/runs/{run_id}/sensitivities", body),
        (f"{V}/projects/{pid}/scenarios", {"name": "Bear", "kind": "bear"}),
    ):
        denied = rev.post(path, json=payload)
        assert (denied.status_code, denied.json()["error"]["code"]) == (403, "forbidden")
