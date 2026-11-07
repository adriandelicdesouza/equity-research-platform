"""Workflow actions API (M3 phase 2, D60).

Every action is a POST that:

* needs a logged-in session, the action's permission (see ``docs/operations/workflow-api.md``
  for the role matrix; ``data:import`` and ``dataset:lock`` are held by no role until the
  owner approves — P15), the session's CSRF token in ``X-CSRF-Token`` and an allowed
  ``Origin`` (existing ``OriginCheckMiddleware``);
* validates its body and parameters, then calls the **existing** domain service, which
  enforces the business rules (reasons, conflict gates, append-only records) and writes the
  ``audit_events`` row;
* is recorded in ``audit.api_actions`` (succeeded or refused) and returns an ``action_id``
  whose result stays inspectable at ``GET /api/actions/{id}``;
* accepts an optional ``Idempotency-Key`` header: a retry with the same key replays the
  first response instead of acting twice.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend.auth.deps import AppSettings, AuthContext, DbSession, authenticated, require
from erp_backend.auth.rbac import Permission
from erp_backend.classifications import confirm_classification, effective_classification
from erp_backend.datasets import (
    IMPORT_BASIS,
    build_dataset,
    build_import_dataset,
    discard_dataset,
    lock_dataset,
    rebuild_draft,
    resolve_conflict,
    verify_dataset,
)
from erp_backend.errors import ApiError
from erp_backend.ingestion.selection import Policy
from erp_backend.market.listings import set_trading_currency
from erp_backend.market.registration import (
    IssuerRef,
    ListingProposal,
    NewIssuer,
    check_listing,
    register_listing,
)
from erp_backend.models import ApiAction, Company, FinancialDataset, Security
from erp_backend.read import companies as read_companies
from erp_backend.read import datasets as read_datasets
from erp_backend.read.common import MAX_LIMIT, paginate
from erp_backend.read.schemas import (
    ClassificationOut,
    DatasetOut,
    ListingOut,
    Page,
    ResolutionOut,
)
from erp_backend.readonly import read_only_db
from erp_backend.workflow import ActionOut, perform

router = APIRouter(tags=["workflow actions"])

Authenticated = Annotated[AuthContext, Depends(authenticated())]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        pattern=r"^[A-Za-z0-9_.:-]{8,128}$",
        description="Optional; a retry with the same key replays the first response.",
    ),
]


def requires(permission: Permission) -> Any:
    return Annotated[AuthContext, Depends(require(permission))]


class ActionRecordOut(BaseModel):
    id: uuid.UUID
    action: str
    target: str | None
    outcome: str
    status_code: int
    idempotency_key: str | None
    created_at: datetime
    response: dict[str, Any]


def _record_out(r: ApiAction) -> ActionRecordOut:
    return ActionRecordOut(
        id=r.id,
        action=r.action,
        target=r.target,
        outcome=r.outcome,
        status_code=r.status_code,
        idempotency_key=r.idempotency_key,
        created_at=r.created_at,
        response=r.response,
    )


@router.get("/actions", response_model=Page[ActionRecordOut])
def list_actions(
    ctx: Authenticated,
    db: Annotated[Any, Depends(read_only_db)],
    action: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ActionRecordOut]:
    """Your own workflow actions, newest first (results and refusals)."""
    stmt = select(ApiAction).where(ApiAction.user_id == ctx.user.id)
    if action:
        stmt = stmt.where(ApiAction.action == action)
    stmt = stmt.order_by(ApiAction.created_at.desc(), ApiAction.id)
    return paginate(db, stmt, limit, offset, lambda rows: [_record_out(r) for r in rows])


@router.get("/actions/{action_id}", response_model=ActionRecordOut)
def get_action(
    action_id: uuid.UUID, ctx: Authenticated, db: Annotated[Any, Depends(read_only_db)]
) -> ActionRecordOut:
    """One action's stored result. Visible to its user, and to administrators."""
    row = db.get(ApiAction, action_id)
    if row is None or (
        row.user_id != ctx.user.id and Permission.USER_MANAGE not in ctx.permissions
    ):
        raise ApiError(404, "not_found", f"No action {action_id}.")
    return _record_out(row)


# --------------------------------------------------------------------------- datasets


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


Reason = Annotated[str, Field(min_length=10, max_length=2000)]


class BuildRequest(_Body):
    basis: Literal["standardized", "reported"] = "standardized"
    policy: Literal["latest_reported", "first_reported"] = "latest_reported"
    facts_as_of: date | None = Field(
        default=None, description="SEC cut-off (standardized basis); default: latest filing"
    )


class RebuildRequest(_Body):
    basis: Literal["standardized", "reported"] = "standardized"
    reason: Reason


class ReasonRequest(_Body):
    reason: Reason


class DatasetActionOut(BaseModel):
    dataset: DatasetOut
    created: bool | None = Field(
        default=None, description="Build: false when identical to the current version"
    )
    validation: dict[str, Any] | None = Field(description="Validation summary of the build")
    conflicts: dict[str, Any] | None = Field(description="Conflict summary of the build")


class VerificationOut(BaseModel):
    dataset_id: uuid.UUID
    ok: bool
    stored_matches: bool
    validation_matches: bool
    rebuild_matches: bool | None
    inputs_match: bool
    mapping_matches: bool
    recomputed_sha256: str


def _dataset_result(d: FinancialDataset, created: bool | None = None) -> DatasetActionOut:
    summary = d.source_summary or {}
    return DatasetActionOut(
        dataset=read_datasets._dataset(d),
        created=created,
        validation=summary.get("validation"),
        conflicts=summary.get("conflicts"),
    )


def _company(db: Session, company_id: uuid.UUID) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise ApiError(404, "not_found", f"No company {company_id}.")
    return company


def _dataset_row(db: Session, dataset_id: uuid.UUID) -> FinancialDataset:
    dataset = db.get(FinancialDataset, dataset_id)
    if dataset is None:
        raise ApiError(404, "not_found", f"No dataset {dataset_id}.")
    return dataset


@router.post(
    "/companies/{company_id}/datasets/build",
    response_model=ActionOut,
    responses={200: {"description": "Identical to the current version (nothing stored)"}},
    status_code=201,
)
def build_dataset_action(
    company_id: uuid.UUID,
    body: BuildRequest,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Build the next draft version (SEC ``standardized`` or import ``reported`` basis)."""

    def run() -> tuple[int, BaseModel]:
        company = _company(db, company_id)
        build = build_import_dataset if body.basis == IMPORT_BASIS else build_dataset
        result = build(
            db,
            company,
            mappings_root=settings.mappings_root,
            mapping_version=settings.mapping_version,
            policy=Policy(body.policy),
            facts_as_of=body.facts_as_of,
            user_id=ctx.user.id,
        )
        return (201 if result.created else 200), _dataset_result(result.dataset, result.created)

    return perform(
        db,
        ctx,
        action="dataset.build",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(company_id),
        run=run,
    )


@router.post("/companies/{company_id}/datasets/rebuild", response_model=ActionOut, status_code=201)
def rebuild_dataset_action(
    company_id: uuid.UUID,
    body: RebuildRequest,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Discard the open draft of ``basis`` (if any) and build a new one with current inputs
    and resolutions (keeps its selection policy and cut-off). Locked versions are untouched."""

    def run() -> tuple[int, BaseModel]:
        company = _company(db, company_id)
        result = rebuild_draft(
            db,
            company,
            mappings_root=settings.mappings_root,
            mapping_version=settings.mapping_version,
            reason=body.reason,
            user_id=ctx.user.id,
            basis=body.basis,
        )
        return (201 if result.created else 200), _dataset_result(result.dataset, result.created)

    return perform(
        db,
        ctx,
        action="dataset.rebuild",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(company_id),
        run=run,
    )


@router.post("/datasets/{dataset_id}/verify", response_model=ActionOut, status_code=200)
def verify_dataset_action(
    dataset_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.COMPANY_READ),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Re-serialize stored rows and rebuild from sources; both must reproduce the hashes.
    Writes nothing to the dataset (the action itself is logged)."""

    def run() -> tuple[int, BaseModel]:
        d = _dataset_row(db, dataset_id)
        v = verify_dataset(db, d, mappings_root=settings.mappings_root)
        return 200, VerificationOut(
            dataset_id=d.id,
            ok=v.ok,
            stored_matches=v.stored_matches,
            validation_matches=v.validation_matches,
            rebuild_matches=v.rebuild_matches,
            inputs_match=v.inputs_match,
            mapping_matches=v.mapping_matches,
            recomputed_sha256=v.recomputed_sha256,
        )

    return perform(
        db,
        ctx,
        action="dataset.verify",
        request={"dataset_id": str(dataset_id)},
        idempotency_key=idempotency_key,
        target=str(dataset_id),
        run=run,
    )


@router.post("/datasets/{dataset_id}/lock", response_model=ActionOut, status_code=200)
def lock_dataset_action(
    dataset_id: uuid.UUID,
    body: ReasonRequest,
    db: DbSession,
    settings: AppSettings,
    ctx: requires(Permission.DATASET_LOCK),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Lock a verified draft (refused with open material conflicts or ambiguous values).
    **Pending approval (P15):** ``dataset:lock`` is held by no role yet."""

    def run() -> tuple[int, BaseModel]:
        _dataset_row(db, dataset_id)
        d = lock_dataset(
            db,
            dataset_id,
            mappings_root=settings.mappings_root,
            reason=body.reason,
            user_id=ctx.user.id,
        )
        return 200, _dataset_result(d)

    return perform(
        db,
        ctx,
        action="dataset.lock",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(dataset_id),
        run=run,
    )


@router.post("/datasets/{dataset_id}/discard", response_model=ActionOut, status_code=200)
def discard_dataset_action(
    dataset_id: uuid.UUID,
    body: ReasonRequest,
    db: DbSession,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Discard a draft (its header and conflicts stay as history; locked versions refuse)."""

    def run() -> tuple[int, BaseModel]:
        _dataset_row(db, dataset_id)
        d = discard_dataset(db, dataset_id, reason=body.reason, user_id=ctx.user.id)
        return 200, _dataset_result(d)

    return perform(
        db,
        ctx,
        action="dataset.discard",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(dataset_id),
        run=run,
    )


# --------------------------------------------------------------------------- conflicts


class ResolutionRequest(_Body):
    action: Literal["select_candidate", "enter_value", "override", "revoke"]
    reason: Reason
    candidate_id: int | None = Field(
        default=None,
        ge=1,
        description="select_candidate: the candidate's id (fact or import value)",
    )
    candidate_source: Literal["reported_fact", "import_value"] | None = Field(
        default=None, description="select_candidate: which id space (required if ambiguous)"
    )
    value: Decimal | None = Field(default=None, description="enter_value: exact amount")
    source_reference: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _fields_match_action(self) -> ResolutionRequest:
        selecting = self.action == "select_candidate"
        entering = self.action == "enter_value"
        if selecting != (self.candidate_id is not None):
            raise ValueError("candidate_id is required for, and only for, select_candidate")
        if self.candidate_source is not None and not selecting:
            raise ValueError("candidate_source applies to select_candidate only")
        if entering != (self.value is not None) or entering != (self.source_reference is not None):
            raise ValueError(
                "value and source_reference are required for, and only for, enter_value"
            )
        if self.value is not None and not self.value.is_finite():
            raise ValueError("value must be a finite number")
        return self


class ResolutionActionOut(BaseModel):
    resolution: ResolutionOut
    note: str


@router.post(
    "/datasets/{dataset_id}/conflicts/{conflict_key}/resolutions",
    response_model=ActionOut,
    status_code=201,
)
def resolve_conflict_action(
    dataset_id: uuid.UUID,
    conflict_key: Annotated[str, Path(pattern=r"^[0-9a-f]{8,32}$")],
    body: ResolutionRequest,
    db: DbSession,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Append an analyst decision on a conflict (select a candidate, enter a value with a
    source, override with a reason, or revoke the active decision). It never edits a dataset
    or an earlier decision; it takes effect in the next build (``.../datasets/rebuild``)."""

    def run() -> tuple[int, BaseModel]:
        _dataset_row(db, dataset_id)
        resolution = resolve_conflict(
            db,
            dataset_id,
            conflict_key,
            body.action,
            reason=body.reason,
            candidate_fact_id=body.candidate_id,
            value=body.value,
            source_reference=body.source_reference,
            candidate_source=body.candidate_source,
            user_id=ctx.user.id,
        )
        return 201, ResolutionActionOut(
            resolution=read_datasets._resolution(resolution),
            note="Recorded; applied by the next rebuild of the company's draft dataset.",
        )

    return perform(
        db,
        ctx,
        action="conflict.resolve",
        request={
            "dataset_id": str(dataset_id),
            "conflict_key": conflict_key,
            **body.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        target=conflict_key,
        run=run,
    )


# --------------------------------------------------------------------------- classification


class ConfirmClassificationRequest(_Body):
    classification: Literal[
        "bank",
        "insurer",
        "specialty_lender",
        "reit",
        "asset_manager_broker",
        "investment_holding",
        "non_financial",
    ]
    reason: Reason


class ClassificationActionOut(BaseModel):
    company_id: uuid.UUID
    effective: str
    confirmation: ClassificationOut


@router.post(
    "/companies/{company_id}/classification/confirm", response_model=ActionOut, status_code=201
)
def confirm_classification_action(
    company_id: uuid.UUID,
    body: ConfirmClassificationRequest,
    db: DbSession,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Analyst confirmation of the financial-business classification (D53). It may differ
    from the suggestion; the reason says why. History is append-only."""

    def run() -> tuple[int, BaseModel]:
        company = _company(db, company_id)
        row = confirm_classification(
            db, company, body.classification, reason=body.reason, user_id=ctx.user.id
        )
        out = read_companies._classification(row)
        assert out is not None
        return 201, ClassificationActionOut(
            company_id=company.id, effective=effective_classification(company), confirmation=out
        )

    return perform(
        db,
        ctx,
        action="classification.confirm",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(company_id),
        run=run,
    )


# --------------------------------------------------------------------------- listings


class SameIssuerAs(_Body):
    ticker: Annotated[str, Field(min_length=1, max_length=16)]
    mic: Annotated[str, Field(pattern=r"^[A-Za-z0-9]{4}$")]


class NewIssuerIn(_Body):
    legal_name: Annotated[str, Field(min_length=1, max_length=300)]
    country: Annotated[str, Field(pattern=r"^[A-Za-z]{2}$")]
    common_name: Annotated[str | None, Field(max_length=300)] = None
    reporting_currency: Annotated[str | None, Field(pattern=r"^[A-Za-z]{3}$")] = None


class IssuerIn(_Body):
    """Exactly one way of naming the issuer (the service refuses anything else, too)."""

    company_id: uuid.UUID | None = None
    same_issuer_as: SameIssuerAs | None = None
    cik: Annotated[int | None, Field(ge=1, le=9_999_999_999)] = None
    new: NewIssuerIn | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> IssuerIn:
        given = [x for x in (self.company_id, self.same_issuer_as, self.cik, self.new) if x]
        if len(given) != 1:
            raise ValueError("give exactly one of company_id, same_issuer_as, cik or new")
        return self


class ListingRequest(_Body):
    ticker: Annotated[str, Field(min_length=1, max_length=16)]
    mic: Annotated[str, Field(pattern=r"^[A-Za-z0-9]{4}$")]
    currency: Annotated[str, Field(pattern=r"^[A-Za-z]{3}$")]
    security_type: Literal[
        "common_equity", "preferred_equity", "depositary_receipt", "fund_unit", "other"
    ]
    issuer: IssuerIn
    source: Annotated[str, Field(min_length=1, max_length=500)]
    reason: Reason
    source_reference: Annotated[str | None, Field(max_length=1000)] = None
    share_class: Annotated[str | None, Field(max_length=16)] = None
    isin: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9]{12}$")] = None
    provider_symbols: dict[
        Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")],
        Annotated[str, Field(min_length=1, max_length=32)],
    ] = Field(default_factory=dict)
    confirm_new_issuer: bool = False

    def proposal(self) -> ListingProposal:
        i = self.issuer
        return ListingProposal(
            ticker=self.ticker,
            mic=self.mic,
            currency=self.currency,
            security_type=self.security_type,
            issuer=IssuerRef(
                company_id=i.company_id,
                same_issuer_as=(i.same_issuer_as.ticker, i.same_issuer_as.mic)
                if i.same_issuer_as
                else None,
                cik=i.cik,
                new=NewIssuer(
                    legal_name=i.new.legal_name,
                    country=i.new.country,
                    common_name=i.new.common_name,
                    reporting_currency=i.new.reporting_currency,
                )
                if i.new
                else None,
            ),
            source_name=self.source,
            reason=self.reason,
            share_class=self.share_class,
            isin=self.isin,
            source_reference=self.source_reference,
            provider_symbols=dict(self.provider_symbols),
            confirm_new_issuer=self.confirm_new_issuer,
        )


class RegistrationOut(BaseModel):
    listing: ListingOut
    company_id: uuid.UUID
    company_created: bool
    registration_id: uuid.UUID
    warnings: list[dict[str, str]]


@router.post("/listings/validate")
def validate_listing_action(
    body: ListingRequest,
    db: Annotated[Session, Depends(read_only_db)],
    _: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
) -> JSONResponse:
    """Check a listing proposal (issuer resolution, duplicates, currency, symbols). Runs on
    the read-only session: nothing is written and it is not recorded as an action."""
    check = check_listing(db, body.proposal())
    return JSONResponse(check.as_dict())


@router.post("/listings", response_model=ActionOut, status_code=201)
def register_listing_action(
    body: ListingRequest,
    db: DbSession,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Register an exchange listing (D56): re-checked under a lock; refused on any error.
    Audited (``listing_registered``) with an append-only registration record."""

    def run() -> tuple[int, BaseModel]:
        result = register_listing(db, body.proposal(), user_id=ctx.user.id)
        return 201, RegistrationOut(
            listing=read_companies.get_listing(db, result.security.id),
            company_id=result.company.id,
            company_created=result.company_created,
            registration_id=result.registration_id,
            warnings=[w.as_dict() for w in result.check.warnings],
        )

    return perform(
        db,
        ctx,
        action="listing.register",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=f"{body.ticker}:{body.mic}".upper(),
        run=run,
    )


class CurrencyRequest(_Body):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="ISO 4217, upper case")]
    reason: Reason


class CurrencyOut(BaseModel):
    listing: ListingOut
    changed: bool


@router.post("/listings/{listing_id}/trading-currency", response_model=ActionOut)
def declare_currency_action(
    listing_id: uuid.UUID,
    body: CurrencyRequest,
    db: DbSession,
    ctx: requires(Permission.COMPANY_WRITE),  # type: ignore[valid-type]
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Declare a listing's trading currency once (audited). Declaring the same currency again
    changes nothing; a different one is refused (stored prices depend on it)."""

    def run() -> tuple[int, BaseModel]:
        security = db.get(Security, listing_id, with_for_update=True)
        if security is None:
            raise ApiError(404, "not_found", f"No listing {listing_id}.")
        changed = set_trading_currency(
            db, security, body.currency, reason=body.reason, user_id=ctx.user.id
        )
        return 200, CurrencyOut(listing=read_companies.get_listing(db, listing_id), changed=changed)

    return perform(
        db,
        ctx,
        action="listing.currency",
        request=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        target=str(listing_id),
        run=run,
    )
