"""Valuation workflow API (M3 phase 5; design §9, P16 approved).

Conventions are the D59/D60 ones, reused unchanged:

* GETs need ``valuation:read`` (admin, analyst, reviewer) and run on the read-only session.
* Changes need ``valuation:write`` (admin, analyst), the session's CSRF token and an allowed
  ``Origin``; the services add owner-only editing with a reasoned administrator override (Q3).
  They run through ``workflow.perform``: one transaction for the change, its audit event and
  the ``audit.api_actions`` record; refusals are rolled back and recorded alone; an optional
  ``Idempotency-Key`` replays the first response.
* Previews and reproduction are POSTs on the read-only session: nothing is stored and they
  are not recorded as actions.
* Valuation refusals return ``409`` with a specific code (e.g. ``dataset_not_locked``) and
  ``details``; see ``docs/operations/valuation-api.md``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from erp_backend.api.actions import IdempotencyKey
from erp_backend.auth.deps import AuthContext, DbSession, require
from erp_backend.auth.rbac import Permission
from erp_backend.read import valuation as rv
from erp_backend.read.common import MAX_LIMIT
from erp_backend.read.schemas import Page
from erp_backend.readonly import read_only_db
from erp_backend.valuation import assumptions as asm
from erp_backend.valuation import projects as svc
from erp_backend.valuation import runs as run_svc
from erp_backend.valuation.common import Actor
from erp_backend.valuation.resolver import (
    Acknowledgement,
    CheckOverride,
    InputOverride,
    RunRequest,
)
from erp_backend.workflow import ActionOut, perform

router = APIRouter(prefix="/valuation", tags=["valuation"])
ReadDb = Annotated[Session, Depends(read_only_db)]
Reader = Annotated[AuthContext, Depends(require(Permission.VALUATION_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.VALUATION_WRITE))]
Methodology = Literal["dcf_fcff", "dcf_fcfe"]
Reason = Annotated[str, Field(min_length=10, max_length=2000)]
AdminReason = Annotated[str | None, Field(default=None, max_length=2000)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- request bodies


class CreateProjectRequest(_Body):
    company_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=120)]
    dataset_id: uuid.UUID
    listing_id: uuid.UUID | None = None
    thesis: Annotated[str | None, Field(max_length=20000)] = None


class MetadataRequest(_Body):
    expected_revision: int
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    thesis: Annotated[str | None, Field(max_length=20000)] = None
    listing_id: uuid.UUID | None = None
    clear_listing: bool = False
    clear_thesis: bool = False
    admin_reason: AdminReason = None


class RebaseRequest(_Body):
    expected_revision: int
    dataset_id: uuid.UUID
    content_sha256: Sha256
    reason: Reason
    admin_reason: AdminReason = None


class StatusRequest(_Body):
    expected_revision: int
    reason: Reason
    admin_reason: AdminReason = None


class OwnerRequest(_Body):
    expected_revision: int
    user_id: uuid.UUID
    reason: Reason


class AnalystSource(_Body):
    type: Literal["analyst"]
    reference: Annotated[str, Field(min_length=3, max_length=500)]
    as_of: date | None = None
    currency: Annotated[str | None, Field(pattern=r"^[A-Z]{3}$")] = None


class RateSource(_Body):
    type: Literal["rate_observation"]
    observation_id: int


class ReferenceSource(_Body):
    type: Literal["reference_value"]
    reference_dataset_id: uuid.UUID
    record_key: Annotated[str, Field(min_length=1, max_length=200)]
    field: Annotated[str, Field(min_length=1, max_length=48)]


Source = Annotated[AnalystSource | RateSource | ReferenceSource, Field(discriminator="type")]


class ChangeIn(_Body):
    key: Annotated[str, Field(min_length=1, max_length=48)]
    period_index: Annotated[int | None, Field(ge=1, le=20)] = None
    value: Annotated[
        str | None, Field(max_length=64, description="Decimal string; ratios are fractions")
    ] = None
    choice: Annotated[str | None, Field(max_length=48)] = None
    text: Annotated[str | None, Field(max_length=2000)] = None
    json_value: Annotated[list[dict[str, Any]] | None, Field(alias="json")] = None
    clear: bool = False
    source: Source | None = None
    status: Literal["approved", "suggested"] = "approved"
    note: Annotated[str | None, Field(max_length=2000)] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReviseRequest(_Body):
    expected_revision: int
    changes: Annotated[list[ChangeIn], Field(min_length=1, max_length=400)]
    reason: Annotated[str | None, Field(max_length=2000)] = None
    admin_reason: AdminReason = None


class ApproveItem(_Body):
    key: Annotated[str, Field(min_length=1, max_length=48)]
    period_index: Annotated[int | None, Field(ge=1, le=20)] = None


class ApproveRequest(_Body):
    expected_revision: int
    items: Annotated[list[ApproveItem], Field(min_length=1, max_length=400)]
    reason: Annotated[str | None, Field(max_length=2000)] = None
    admin_reason: AdminReason = None


class DatasetPin(_Body):
    id: uuid.UUID
    content_sha256: Sha256


class OverrideIn(_Body):
    input: Annotated[str, Field(min_length=1, max_length=64)]
    line_item: Annotated[str | None, Field(max_length=64)] = None
    kind: Literal["value", "not_applicable"]
    value: Annotated[str | None, Field(max_length=64)] = None
    source: Annotated[str | None, Field(max_length=500)] = None
    reason: Annotated[str, Field(max_length=2000)]


class AckIn(_Body):
    code: Annotated[str, Field(min_length=1, max_length=96)]
    reason: Annotated[str, Field(max_length=2000)]


class CheckOverrideIn(_Body):
    check_code: Annotated[str, Field(min_length=1, max_length=48)]
    subject: Annotated[str, Field(min_length=1, max_length=128)]
    reason: Annotated[str, Field(max_length=2000)]


class RunRequestIn(_Body):
    methodology: Methodology
    scenario_id: uuid.UUID
    expected_scenario_revision: int
    dataset: DatasetPin
    balance_sheet_period_id: uuid.UUID
    valuation_date: date
    listing_id: uuid.UUID | None = None
    price_observation_id: int | None = None
    fx_rate_ids: Annotated[list[int], Field(max_length=4)] = []
    input_overrides: Annotated[list[OverrideIn], Field(max_length=50)] = []
    acknowledgements: Annotated[list[AckIn], Field(max_length=10)] = []
    check_overrides: Annotated[list[CheckOverrideIn], Field(max_length=50)] = []
    admin_reason: AdminReason = None

    def to_request(self) -> RunRequest:
        return RunRequest(
            methodology=self.methodology,
            scenario_id=self.scenario_id,
            expected_scenario_revision=self.expected_scenario_revision,
            dataset_id=self.dataset.id,
            dataset_content_sha256=self.dataset.content_sha256,
            balance_sheet_period_id=self.balance_sheet_period_id,
            valuation_date=self.valuation_date,
            listing_id=self.listing_id,
            price_observation_id=self.price_observation_id,
            fx_rate_ids=tuple(self.fx_rate_ids),
            input_overrides=tuple(
                InputOverride(o.input, o.line_item, o.kind, o.reason, o.value, o.source)
                for o in self.input_overrides
            ),
            acknowledgements=tuple(
                Acknowledgement(a.code, a.reason) for a in self.acknowledgements
            ),
            check_overrides=tuple(
                CheckOverride(c.check_code, c.subject, c.reason) for c in self.check_overrides
            ),
        )


class CreateRunRequest(RunRequestIn):
    label: Annotated[str | None, Field(max_length=120)] = None

    @field_validator("label")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


# --------------------------------------------------------------------------- response bodies


class IssueOut(BaseModel):
    level: str
    code: str
    message: str
    details: dict[str, Any]


class PreviewOut(BaseModel):
    preview: bool = True
    dataset_status: str | None
    can_save: bool = Field(description="True when no error or gate blocks a saved run")
    issues: list[IssueOut]
    input_document: dict[str, Any] | None
    outputs: dict[str, Any] | None
    validation: list[dict[str, Any]]
    trace: dict[str, Any] | None
    engine_refusal: dict[str, Any] | None


class RunCreatedOut(BaseModel):
    existing: bool = Field(description="True: identical inputs already produced this run (Q8)")
    run: rv.RunSummaryOut


class ReproductionOut(BaseModel):
    run_id: uuid.UUID
    matches: bool
    same_engine: bool
    stored_output_sha256: str
    recomputed_output_sha256: str | None
    stored_engine: dict[str, Any]
    current_engine: dict[str, Any]
    differences: list[dict[str, Any]]
    refusal: dict[str, Any] | None


def _actor(ctx: AuthContext) -> Actor:
    return Actor.of(ctx.user)


# --------------------------------------------------------------------------- reference reads


@router.get("/assumption-keys", response_model=list[rv.KeySpecOut])
def assumption_keys(ctx: Reader) -> list[rv.KeySpecOut]:
    """The assumption key registry: types, units (ratios are fractions), ranges, sources."""
    return rv.key_specs()


@router.get("/blocks", response_model=list[rv.BlockOut])
def list_blocks(
    db: ReadDb,
    ctx: Reader,
    classification: Annotated[str | None, Query(max_length=32, pattern=r"^[a-z_]+$")] = None,
    methodology: Methodology | None = None,
) -> list[rv.BlockOut]:
    """Classification blocks of the current version (design §7)."""
    return rv.blocks(db, classification, methodology)


# --------------------------------------------------------------------------- projects


@router.get("/projects", response_model=Page[rv.ProjectOut])
def list_projects(
    db: ReadDb,
    ctx: Reader,
    company_id: uuid.UUID | None = None,
    status: Literal["active", "archived"] | None = None,
    owner: Literal["me"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[rv.ProjectOut]:
    return rv.list_projects(
        db,
        company_id=company_id,
        status=status,
        owner_id=ctx.user.id if owner == "me" else None,
        limit=limit,
        offset=offset,
    )


@router.post("/projects", response_model=ActionOut, status_code=201)
def create_project(
    body: CreateProjectRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Create a project pinned to one dataset version, with its base scenario."""

    def run() -> tuple[int, BaseModel]:
        p = svc.create_project(
            db,
            _actor(ctx),
            company_id=body.company_id,
            name=body.name,
            dataset_id=body.dataset_id,
            listing_id=body.listing_id,
            thesis=body.thesis,
        )
        return 201, rv.project_out(db, p)

    return perform(
        db,
        ctx,
        action="valuation.project_create",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(body.company_id),
        run=run,
    )


@router.get("/projects/{project_id}", response_model=rv.ProjectDetailOut)
def get_project(project_id: uuid.UUID, db: ReadDb, ctx: Reader) -> rv.ProjectDetailOut:
    return rv.project_detail(db, project_id)


def _project_action(
    db: Session,
    ctx: Any,
    project_id: uuid.UUID,
    action: str,
    body: _Body,
    key: str | None,
    fn: Any,
) -> JSONResponse:
    def run() -> tuple[int, BaseModel]:
        return 200, rv.project_out(db, fn())

    return perform(
        db,
        ctx,
        action=action,
        request=body.model_dump(mode="json"),
        idempotency_key=key,
        target=str(project_id),
        run=run,
    )


@router.post("/projects/{project_id}/metadata", response_model=ActionOut)
def update_project(
    project_id: uuid.UUID,
    body: MetadataRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Change name, thesis or default listing (owner, or admin with ``admin_reason``)."""
    changes: dict[str, Any] = {}
    if body.name is not None:
        changes["name"] = body.name
    if body.thesis is not None or body.clear_thesis:
        changes["thesis"] = None if body.clear_thesis else body.thesis
    if body.listing_id is not None or body.clear_listing:
        changes["listing_id"] = None if body.clear_listing else body.listing_id
    return _project_action(
        db,
        ctx,
        project_id,
        "valuation.project_update",
        body,
        idempotency_key,
        lambda: svc.update_metadata(
            db,
            _actor(ctx),
            project_id,
            expected_revision=body.expected_revision,
            changes=changes,
            admin_reason=body.admin_reason,
        ),
    )


@router.post("/projects/{project_id}/rebase", response_model=ActionOut)
def rebase_project(
    project_id: uuid.UUID,
    body: RebaseRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Pin another dataset version of the same company (hash-confirmed, with reason)."""
    return _project_action(
        db,
        ctx,
        project_id,
        "valuation.project_rebase",
        body,
        idempotency_key,
        lambda: svc.rebase(
            db,
            _actor(ctx),
            project_id,
            expected_revision=body.expected_revision,
            dataset_id=body.dataset_id,
            content_sha256=body.content_sha256,
            reason=body.reason,
            admin_reason=body.admin_reason,
        ),
    )


@router.post("/projects/{project_id}/archive", response_model=ActionOut)
def archive_project(
    project_id: uuid.UUID,
    body: StatusRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    return _project_action(
        db,
        ctx,
        project_id,
        "valuation.project_archive",
        body,
        idempotency_key,
        lambda: svc.set_status(
            db,
            _actor(ctx),
            project_id,
            expected_revision=body.expected_revision,
            status="archived",
            reason=body.reason,
            admin_reason=body.admin_reason,
        ),
    )


@router.post("/projects/{project_id}/restore", response_model=ActionOut)
def restore_project(
    project_id: uuid.UUID,
    body: StatusRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    return _project_action(
        db,
        ctx,
        project_id,
        "valuation.project_restore",
        body,
        idempotency_key,
        lambda: svc.set_status(
            db,
            _actor(ctx),
            project_id,
            expected_revision=body.expected_revision,
            status="active",
            reason=body.reason,
            admin_reason=body.admin_reason,
        ),
    )


@router.post("/projects/{project_id}/owner", response_model=ActionOut)
def transfer_owner(
    project_id: uuid.UUID,
    body: OwnerRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Administrators only: give the project to another user holding ``valuation:write``."""
    return _project_action(
        db,
        ctx,
        project_id,
        "valuation.project_owner",
        body,
        idempotency_key,
        lambda: svc.transfer_owner(
            db,
            _actor(ctx),
            project_id,
            expected_revision=body.expected_revision,
            user_id=body.user_id,
            reason=body.reason,
        ),
    )


@router.get("/projects/{project_id}/scenarios", response_model=list[rv.ScenarioOut])
def list_scenarios(project_id: uuid.UUID, db: ReadDb, ctx: Reader) -> list[rv.ScenarioOut]:
    rv.get_project(db, project_id)
    return rv.scenarios(db, project_id)


# --------------------------------------------------------------------------- assumptions


@router.get("/scenarios/{scenario_id}/assumptions", response_model=rv.AssumptionStateOut)
def assumption_state(
    scenario_id: uuid.UUID,
    db: ReadDb,
    ctx: Reader,
    methodology: Methodology = "dcf_fcff",
) -> rv.AssumptionStateOut:
    """Current value per key, with the keys still missing or unapproved for ``methodology``."""
    return rv.assumption_state(db, scenario_id, methodology)


@router.get(
    "/scenarios/{scenario_id}/assumptions/history", response_model=Page[rv.AssumptionItemOut]
)
def assumption_history(
    scenario_id: uuid.UUID,
    db: ReadDb,
    ctx: Reader,
    key: Annotated[str | None, Query(max_length=48, pattern=r"^[a-z_]+$")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[rv.AssumptionItemOut]:
    """Every revision (newest first): values, statuses, sources, users and times."""
    return rv.assumption_history(db, scenario_id, key, limit, offset)


@router.post("/scenarios/{scenario_id}/assumptions", response_model=ActionOut)
def revise_assumptions(
    scenario_id: uuid.UUID,
    body: ReviseRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Apply changes atomically as one new scenario revision (append-only)."""

    def run() -> tuple[int, BaseModel]:
        changes = [
            asm.Change(
                key=c.key,
                period_index=c.period_index,
                value=c.value,
                choice=c.choice,
                text=c.text,
                json=c.json_value,
                clear=c.clear,
                source=c.source.model_dump(mode="json", exclude_none=True) if c.source else None,
                status=c.status,
                note=c.note,
            )
            for c in body.changes
        ]
        asm.revise(
            db,
            _actor(ctx),
            scenario_id,
            expected_revision=body.expected_revision,
            changes=changes,
            reason=body.reason,
            admin_reason=body.admin_reason,
        )
        return 200, rv.assumption_state(db, scenario_id, "dcf_fcff")

    return perform(
        db,
        ctx,
        action="valuation.assumptions_revise",
        request=body.model_dump(mode="json", by_alias=True),
        idempotency_key=idempotency_key,
        target=str(scenario_id),
        run=run,
    )


@router.post("/scenarios/{scenario_id}/assumptions/approve", response_model=ActionOut)
def approve_assumptions(
    scenario_id: uuid.UUID,
    body: ApproveRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Approve current suggested values unchanged (one approved revision each)."""

    def run() -> tuple[int, BaseModel]:
        asm.approve(
            db,
            _actor(ctx),
            scenario_id,
            expected_revision=body.expected_revision,
            items=[(i.key, i.period_index) for i in body.items],
            reason=body.reason,
            admin_reason=body.admin_reason,
        )
        return 200, rv.assumption_state(db, scenario_id, "dcf_fcff")

    return perform(
        db,
        ctx,
        action="valuation.assumptions_approve",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(scenario_id),
        run=run,
    )


# --------------------------------------------------------------------------- inputs, preview, runs


@router.get("/projects/{project_id}/input-candidates", response_model=rv.CandidatesOut)
def input_candidates(
    project_id: uuid.UUID,
    valuation_date: date,
    db: ReadDb,
    ctx: Reader,
    listing_id: uuid.UUID | None = None,
) -> rv.CandidatesOut:
    """Suggested inputs with explicit row ids (latest on or before the valuation date)."""
    return rv.candidates(db, project_id, valuation_date, listing_id)


@router.get("/projects/{project_id}/historical-context", response_model=rv.HistoricalContextOut)
def historical_context(
    project_id: uuid.UUID,
    db: ReadDb,
    ctx: Reader,
    years: Annotated[int, Query(ge=1, le=15)] = 10,
) -> rv.HistoricalContextOut:
    return rv.historical_context(db, project_id, years)


@router.post("/projects/{project_id}/preview", response_model=PreviewOut)
def preview(
    project_id: uuid.UUID,
    body: RunRequestIn,
    db: ReadDb,
    ctx: Writer,
) -> PreviewOut:
    """Resolve and compute without storing anything (read-only session; not an action).
    Draft datasets and suggested values are allowed and reported as gates."""
    p = run_svc.preview(db, _actor(ctx), project_id, body.to_request(), body.admin_reason)
    res, comp = p.resolution, p.computation
    return PreviewOut(
        dataset_status=res.dataset.status if res.dataset else None,
        can_save=not res.blocking and comp is not None and not comp.blocks_save,
        issues=[
            IssueOut(level=i.level, code=i.code, message=i.message, details=i.details)
            for i in res.issues
        ],
        input_document=res.document,
        outputs=comp.outputs if comp else None,
        validation=comp.validation if comp else [],
        trace=comp.trace if comp else None,
        engine_refusal=p.refusal,
    )


@router.post("/projects/{project_id}/runs", response_model=ActionOut, status_code=201)
def create_run(
    project_id: uuid.UUID,
    body: CreateRunRequest,
    db: DbSession,
    ctx: Writer,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Create an immutable run (201), or return the identical existing one (200, Q8)."""
    action_id = uuid.uuid4()

    def run() -> tuple[int, BaseModel]:
        result = run_svc.create_run(
            db,
            _actor(ctx),
            project_id,
            body.to_request(),
            label=body.label,
            admin_reason=body.admin_reason,
            action_id=action_id,
        )
        out = RunCreatedOut(existing=result.existing, run=rv.run_summary(db, result.run))
        return (200 if result.existing else 201), out

    return perform(
        db,
        ctx,
        action="valuation.run_create",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(project_id),
        run=run,
        action_id=action_id,
    )


@router.get("/projects/{project_id}/runs", response_model=Page[rv.RunSummaryOut])
def list_runs(
    project_id: uuid.UUID,
    db: ReadDb,
    ctx: Reader,
    scenario_id: uuid.UUID | None = None,
    methodology: Methodology | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[rv.RunSummaryOut]:
    return rv.list_runs(db, project_id, scenario_id, methodology, limit, offset)


@router.get("/runs/{run_id}", response_model=rv.RunOut)
def get_run(run_id: uuid.UUID, db: ReadDb, ctx: Reader) -> rv.RunOut:
    return rv.run_detail(db, run_id)


@router.get("/runs/{run_id}/inputs", response_model=rv.RunInputsOut)
def get_run_inputs(run_id: uuid.UUID, db: ReadDb, ctx: Reader) -> rv.RunInputsOut:
    """The stored input document and its lineage rows (sources, conversions, as-of dates)."""
    return rv.run_inputs(db, run_id)


@router.get("/runs/{run_id}/trace", response_model=rv.TraceOut)
def get_run_trace(
    run_id: uuid.UUID,
    db: ReadDb,
    ctx: Reader,
    node: Annotated[str | None, Query(max_length=128)] = None,
    depth: Annotated[int, Query(ge=0, le=5)] = 2,
) -> rv.TraceOut:
    """All trace nodes, or one node and its inputs to ``depth`` levels."""
    return rv.run_trace(db, run_id, node, depth)


@router.post("/runs/{run_id}/reproduce", response_model=ReproductionOut)
def reproduce_run(run_id: uuid.UUID, db: ReadDb, ctx: Reader) -> ReproductionOut:
    """Re-execute from the stored input document only; compare output hashes. Stores nothing."""
    run = rv.run_or_404(db, run_id)
    rep = run_svc.reproduce(run)
    return ReproductionOut(run_id=run.id, **rep.__dict__)
