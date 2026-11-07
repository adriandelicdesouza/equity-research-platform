"""Read services for the valuation workflow (M3 phase 5; design §9). SELECT only.

Exact numbers are ``Decimal`` and serialize as JSON strings (D59). Candidate inputs are
*suggestions* with explicit row ids: the client submits the ids it accepts, and the server
never fills an omitted input with "latest" when a run is created.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any

from erp_engine.core.numbers import make_context
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_backend.classifications import effective_classification
from erp_backend.errors import ApiError
from erp_backend.models import (
    AssumptionRevision,
    Company,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    FxRate,
    PriceObservation,
    RateObservationRow,
    RateSeries,
    ReferenceDataset,
    RunDecision,
    RunInput,
    RunTrace,
    Scenario,
    Security,
    SensitivityRun,
    User,
    ValuationBlock,
    ValuationProject,
    ValuationRun,
)
from erp_backend.read.common import paginate
from erp_backend.read.schemas import Page
from erp_backend.valuation import assumptions as asm
from erp_backend.valuation.blocks import CURRENT_VERSION, block_from_row
from erp_backend.valuation.resolver import STALE_DAYS, STALE_MONTHS, add_months

# --------------------------------------------------------------------------- schemas


class UserRef(BaseModel):
    id: uuid.UUID
    username: str


class DatasetRef(BaseModel):
    id: uuid.UUID
    version_no: int
    basis: str
    status: str
    currency: str
    content_sha256: str


class ProjectOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    listing_id: uuid.UUID | None
    name: str
    thesis: str | None
    status: str
    status_reason: str | None
    owner: UserRef
    dataset: DatasetRef
    revision_no: int
    created_at: datetime
    created_by: UserRef
    updated_at: datetime | None


class ScenarioOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    kind: str
    parent_scenario_id: uuid.UUID | None
    revision_no: int
    status: str
    probability: Decimal | None = None
    sort_order: int = 0
    updated_at: datetime | None = None


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_revision_no: int
    methodology: str
    produces: str
    dataset_id: uuid.UUID
    dataset_version_no: int
    dataset_basis: str
    classification: str
    valuation_date: date
    balance_sheet_date: date
    currency: str
    primary_value: Decimal
    per_share_value: Decimal
    label: str | None
    warnings: list[dict[str, Any]]
    input_sha256: str
    output_sha256: str
    engine_version: str
    created_by: UserRef
    created_at: datetime


class ProjectDetailOut(ProjectOut):
    scenarios: list[ScenarioOut]
    newer_locked_dataset: DatasetRef | None = Field(
        description="A later locked version of the same basis, if any (information only)"
    )
    classification: str
    recent_runs: list[RunSummaryOut]


class AssumptionItemOut(BaseModel):
    key: str
    period_index: int | None
    value: Decimal | None
    json_value: Any = Field(None, alias="json")
    unit: str
    status: str
    provenance_kind: str
    source: dict[str, Any] | None
    note: str | None
    revision_id: int
    scenario_revision_no: int
    created_by: uuid.UUID
    created_at: datetime
    inherited_from: uuid.UUID | None = Field(
        None, description="Ancestor scenario the value is inherited from (resolved state)"
    )

    model_config = {"populate_by_name": True}


class RequiredKeyOut(BaseModel):
    key: str
    period_index: int | None


class AssumptionStateOut(BaseModel):
    scenario_id: uuid.UUID
    scenario_revision: int
    methodology: str
    resolved: bool = True
    items: list[AssumptionItemOut]
    missing_required: list[RequiredKeyOut]
    unapproved: list[RequiredKeyOut]


class KeySpecOut(BaseModel):
    key: str
    kind: str
    label: str
    unit: str
    per_period: bool
    choices: list[str]
    low: Decimal | None
    high: Decimal | None
    sources: list[str]
    source_required: bool
    market: bool
    methodologies: list[str]


class BlockOut(BaseModel):
    id: uuid.UUID
    block_version: str
    classification: str
    methodology: str
    policy: str
    definition: dict[str, Any]
    definition_sha256: str


class PeriodCandidateOut(BaseModel):
    id: uuid.UUID
    fiscal_year: int
    start_date: date
    end_date: date
    value_count: int


class RateCandidateOut(BaseModel):
    observation_id: int
    series_code: str
    tenor: str
    currency: str
    obs_date: date
    value_pp: Decimal
    value_fraction: Decimal
    stale: bool
    status: str = "suggested"


class PriceCandidateOut(BaseModel):
    observation_id: int
    listing_id: uuid.UUID
    provider_code: str
    close_basis: str
    trade_date: date
    close: Decimal
    currency: str
    stale: bool
    status: str = "suggested"


class FxCandidateOut(BaseModel):
    fx_rate_id: int
    base: str
    quote: str
    obs_date: date
    rate: Decimal
    provider_series_id: str
    conversion: str = Field(description="'direct' or 'reciprocal' for price → reporting currency")
    stale: bool
    status: str = "suggested"


class ReferenceCandidateOut(BaseModel):
    reference_dataset_id: uuid.UUID
    dataset_kind: str
    edition_date: date
    region: str | None
    variant: str | None
    version_no: int
    record_count: int
    stale: bool
    status: str = "suggested"


class CandidatesOut(BaseModel):
    valuation_date: date
    currency: str
    dataset: DatasetRef
    periods: list[PeriodCandidateOut]
    rates: list[RateCandidateOut]
    prices: list[PriceCandidateOut]
    fx: list[FxCandidateOut]
    reference: list[ReferenceCandidateOut]
    notes: list[str]


class HistoryYearOut(BaseModel):
    fiscal_year: int
    end_date: date
    revenue: Decimal | None
    revenue_growth: Decimal | None
    ebit_margin: Decimal | None
    tax_rate: Decimal | None
    da_pct: Decimal | None
    capex_pct: Decimal | None
    nwc_pct: Decimal | None


class HistoricalContextOut(BaseModel):
    dataset: DatasetRef
    years: list[HistoryYearOut]
    note: str


class RunDecisionOut(BaseModel):
    kind: str
    code: str
    reason: str
    detail: dict[str, Any]
    user: UserRef
    created_at: datetime


class RunOut(RunSummaryOut):
    method_version: str
    block_id: uuid.UUID
    dataset_content_sha256: str
    engine_input_sha256: str
    module_versions: dict[str, str]
    outputs: dict[str, Any]
    validation: list[dict[str, Any]]
    decisions: list[RunDecisionOut]
    trace_sha256: str
    trace_node_count: int


class RunInputOut(BaseModel):
    input_key: str
    value: Decimal | None
    json_value: Any = Field(None, alias="json")
    unit: str
    source_value: Decimal | None
    source_unit: str | None
    conversion: str | None
    provenance_kind: str
    source_type: str
    source_ref: str | None
    source_sha256: str | None
    as_of_date: date | None
    warnings: list[str]

    model_config = {"populate_by_name": True}


class RunInputsOut(BaseModel):
    run_id: uuid.UUID
    input_sha256: str
    input_document: dict[str, Any]
    lineage: list[RunInputOut]


class TraceOut(BaseModel):
    run_id: uuid.UUID
    trace_sha256: str
    root: str | None
    nodes: dict[str, dict[str, Any]]
    truncated: bool


# --------------------------------------------------------------------------- helpers


def _users(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, UserRef]:
    if not ids:
        return {}
    return {
        u.id: UserRef(id=u.id, username=u.username)
        for u in db.scalars(select(User).where(User.id.in_(ids)))
    }


def _dataset_ref(d: FinancialDataset) -> DatasetRef:
    return DatasetRef(
        id=d.id,
        version_no=d.version_no,
        basis=d.basis,
        status=d.status,
        currency=d.currency,
        content_sha256=d.content_sha256,
    )


def get_project(db: Session, project_id: uuid.UUID) -> ValuationProject:
    project = db.get(ValuationProject, project_id)
    if project is None:
        raise ApiError(404, "not_found", f"No valuation project {project_id}.")
    return project


def _projects_out(db: Session, rows: list[ValuationProject]) -> list[ProjectOut]:
    users = _users(db, {r.owner_user_id for r in rows} | {r.created_by for r in rows})
    datasets = {
        d.id: d
        for d in db.scalars(
            select(FinancialDataset).where(FinancialDataset.id.in_({r.dataset_id for r in rows}))
        )
    }
    companies = {
        c.id: c
        for c in db.scalars(select(Company).where(Company.id.in_({r.company_id for r in rows})))
    }
    return [
        ProjectOut(
            id=r.id,
            company_id=r.company_id,
            company_name=companies[r.company_id].common_name or companies[r.company_id].legal_name,
            listing_id=r.listing_id,
            name=r.name,
            thesis=r.thesis,
            status=r.status,
            status_reason=r.status_reason,
            owner=users[r.owner_user_id],
            dataset=_dataset_ref(datasets[r.dataset_id]),
            revision_no=r.revision_no,
            created_at=r.created_at,
            created_by=users[r.created_by],
            updated_at=r.updated_at,
        )
        for r in rows
    ]


def project_out(db: Session, project: ValuationProject) -> ProjectOut:
    return _projects_out(db, [project])[0]


def list_projects(
    db: Session,
    *,
    company_id: uuid.UUID | None,
    status: str | None,
    owner_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> Page[ProjectOut]:
    stmt = select(ValuationProject)
    if company_id is not None:
        stmt = stmt.where(ValuationProject.company_id == company_id)
    if status is not None:
        stmt = stmt.where(ValuationProject.status == status)
    if owner_id is not None:
        stmt = stmt.where(ValuationProject.owner_user_id == owner_id)
    stmt = stmt.order_by(ValuationProject.created_at.desc(), ValuationProject.id)
    return paginate(db, stmt, limit, offset, lambda rows: _projects_out(db, list(rows)))


def _scenario_out(s: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=s.id,
        project_id=s.project_id,
        name=s.name,
        kind=s.kind,
        parent_scenario_id=s.parent_scenario_id,
        revision_no=s.revision_no,
        status=s.status,
        probability=s.probability,
        sort_order=s.sort_order,
        updated_at=s.updated_at,
    )


def scenarios(db: Session, project_id: uuid.UUID) -> list[ScenarioOut]:
    rows = db.scalars(
        select(Scenario)
        .where(Scenario.project_id == project_id)
        .order_by(Scenario.sort_order, Scenario.created_at)
    )
    return [_scenario_out(s) for s in rows]


def _run_summaries(db: Session, rows: list[ValuationRun]) -> list[RunSummaryOut]:
    users = _users(db, {r.created_by for r in rows})
    return [_summary(r, users[r.created_by]) for r in rows]


def run_summary(db: Session, run: ValuationRun) -> RunSummaryOut:
    return _run_summaries(db, [run])[0]


def _summary(r: ValuationRun, user: UserRef) -> RunSummaryOut:
    return RunSummaryOut(
        id=r.id,
        project_id=r.project_id,
        scenario_id=r.scenario_id,
        scenario_revision_no=r.scenario_revision_no,
        methodology=r.methodology,
        produces=r.produces,
        dataset_id=r.dataset_id,
        dataset_version_no=r.dataset_version_no,
        dataset_basis=r.dataset_basis,
        classification=r.classification,
        valuation_date=r.valuation_date,
        balance_sheet_date=r.balance_sheet_date,
        currency=r.currency,
        primary_value=r.primary_value,
        per_share_value=r.per_share_value,
        label=r.label,
        warnings=r.warnings,
        input_sha256=r.input_sha256,
        output_sha256=r.output_sha256,
        engine_version=r.engine_version,
        created_by=user,
        created_at=r.created_at,
    )


def project_detail(db: Session, project_id: uuid.UUID) -> ProjectDetailOut:
    project = get_project(db, project_id)
    base = project_out(db, project)
    pinned = db.get(FinancialDataset, project.dataset_id)
    assert pinned is not None
    newer = db.scalars(
        select(FinancialDataset)
        .where(
            FinancialDataset.company_id == project.company_id,
            FinancialDataset.basis == pinned.basis,
            FinancialDataset.status == "locked",
            FinancialDataset.version_no > pinned.version_no,
        )
        .order_by(FinancialDataset.version_no.desc())
        .limit(1)
    ).first()
    company = db.get(Company, project.company_id)
    assert company is not None
    recent = db.scalars(
        select(ValuationRun)
        .where(ValuationRun.project_id == project.id)
        .order_by(ValuationRun.created_at.desc(), ValuationRun.id)
        .limit(5)
    ).all()
    return ProjectDetailOut(
        **base.model_dump(),
        scenarios=scenarios(db, project.id),
        newer_locked_dataset=_dataset_ref(newer) if newer else None,
        classification=effective_classification(company),
        recent_runs=_run_summaries(db, list(recent)),
    )


# --------------------------------------------------------------------------- assumptions


def _item_out(r: AssumptionRevision, inherited_from: uuid.UUID | None = None) -> AssumptionItemOut:
    return AssumptionItemOut(
        key=r.key,
        period_index=r.period_index,
        value=r.value,
        json=r.value_json,
        unit=r.unit,
        status=r.status,
        provenance_kind=r.provenance_kind,
        source=r.source,
        note=r.note,
        revision_id=r.id,
        scenario_revision_no=r.scenario_revision_no,
        created_by=r.created_by,
        created_at=r.created_at,
        inherited_from=inherited_from,
    )


def scenario_or_404(db: Session, scenario_id: uuid.UUID) -> Scenario:
    s = db.get(Scenario, scenario_id)
    if s is None:
        raise ApiError(404, "not_found", f"No scenario {scenario_id}.")
    return s


def assumption_state(
    db: Session, scenario_id: uuid.UUID, methodology: str, resolved: bool = True
) -> AssumptionStateOut:
    """Current values; with ``resolved`` (default) inherited values are applied, as a run
    would use them, and marked with ``inherited_from``."""
    scenario = scenario_or_404(db, scenario_id)
    project = get_project(db, scenario.project_id)
    company = db.get(Company, project.company_id)
    assert company is not None
    state = asm.resolved_state(db, scenario) if resolved else asm.current_state(db, scenario)
    cls = effective_classification(company)
    row = db.scalars(
        select(ValuationBlock).where(
            ValuationBlock.block_version == CURRENT_VERSION,
            ValuationBlock.classification == (cls if cls != "needs_review" else "non_financial"),
            ValuationBlock.methodology == methodology,
        )
    ).one()
    block = block_from_row(row)
    required = asm.required_keys(state, methodology, block) if block.policy != "blocked" else []
    missing = [
        RequiredKeyOut(key=k, period_index=p) for k, p in required if state.get(k, p) is None
    ]
    unapproved = [
        RequiredKeyOut(key=c.key, period_index=c.period_index)
        for c in state.items.values()
        if not c.approved and asm.applicable(c.key, methodology)
    ]
    return AssumptionStateOut(
        scenario_id=scenario.id,
        scenario_revision=scenario.revision_no,
        methodology=methodology,
        resolved=resolved,
        items=[_item_out(c.row, c.inherited_from) for c in state.items.values()],
        missing_required=missing,
        unapproved=unapproved,
    )


def assumption_history(
    db: Session, scenario_id: uuid.UUID, key: str | None, limit: int, offset: int
) -> Page[AssumptionItemOut]:
    scenario_or_404(db, scenario_id)
    stmt = select(AssumptionRevision).where(AssumptionRevision.scenario_id == scenario_id)
    if key is not None:
        stmt = stmt.where(AssumptionRevision.key == key)
    stmt = stmt.order_by(AssumptionRevision.id.desc())
    return paginate(db, stmt, limit, offset, lambda rows: [_item_out(r) for r in rows])


def key_specs() -> list[KeySpecOut]:
    return [
        KeySpecOut(
            key=s.key,
            kind=s.kind,
            label=s.label,
            unit=s.unit,
            per_period=s.per_period,
            choices=list(s.choices),
            low=s.low,
            high=s.high,
            sources=list(s.sources),
            source_required=s.source_required,
            market=s.market,
            methodologies=list(s.methodologies),
        )
        for s in asm.REGISTRY.values()
    ]


def blocks(db: Session, classification: str | None, methodology: str | None) -> list[BlockOut]:
    stmt = select(ValuationBlock).where(ValuationBlock.block_version == CURRENT_VERSION)
    if classification:
        stmt = stmt.where(ValuationBlock.classification == classification)
    if methodology:
        stmt = stmt.where(ValuationBlock.methodology == methodology)
    stmt = stmt.order_by(ValuationBlock.classification, ValuationBlock.methodology)
    return [
        BlockOut(
            id=b.id,
            block_version=b.block_version,
            classification=b.classification,
            methodology=b.methodology,
            policy=b.policy,
            definition=b.definition,
            definition_sha256=b.definition_sha256,
        )
        for b in db.scalars(stmt)
    ]


# --------------------------------------------------------------------------- inputs


def _stale_days(as_of: date, v: date) -> bool:
    return (v - as_of).days > STALE_DAYS


def candidates(
    db: Session, project_id: uuid.UUID, valuation_date: date, listing_id: uuid.UUID | None
) -> CandidatesOut:
    project = get_project(db, project_id)
    dataset = db.get(FinancialDataset, project.dataset_id)
    assert dataset is not None
    v, currency = valuation_date, dataset.currency
    notes: list[str] = []
    counts = (
        select(FinancialValue.period_id, func.count().label("n"))
        .where(FinancialValue.dataset_id == dataset.id)
        .group_by(FinancialValue.period_id)
        .subquery()
    )
    periods = [
        PeriodCandidateOut(
            id=p.id,
            fiscal_year=p.fiscal_year,
            start_date=p.start_date,
            end_date=p.end_date,
            value_count=n,
        )
        for p, n in db.execute(
            select(FiscalPeriodRow, counts.c.n)
            .join(counts, counts.c.period_id == FiscalPeriodRow.id)
            .where(FiscalPeriodRow.period_type == "FY", FiscalPeriodRow.end_date <= v)
            .order_by(FiscalPeriodRow.end_date.desc())
        )
    ]
    # Latest observation on or before V per series in the cash-flow currency (latest revision).
    rates: list[RateCandidateOut] = []
    for series in db.scalars(select(RateSeries).where(RateSeries.currency == currency)):
        obs = db.scalars(
            select(RateObservationRow)
            .where(RateObservationRow.series_code == series.code, RateObservationRow.obs_date <= v)
            .order_by(RateObservationRow.obs_date.desc(), RateObservationRow.id.desc())
            .limit(1)
        ).first()
        if obs is not None:
            with localcontext(make_context()):
                frac = obs.value / Decimal(100)
            rates.append(
                RateCandidateOut(
                    observation_id=obs.id,
                    series_code=series.code,
                    tenor=series.tenor,
                    currency=series.currency,
                    obs_date=obs.obs_date,
                    value_pp=obs.value,
                    value_fraction=frac,
                    stale=_stale_days(obs.obs_date, v),
                )
            )
    if not rates:
        notes.append(
            f"No stored risk-free observation in {currency} on or before {v}: enter the "
            "rate as an analyst assumption with a written source."
        )
    listing = (
        db.get(Security, listing_id or project.listing_id)
        if (listing_id or project.listing_id)
        else None
    )
    prices: list[PriceCandidateOut] = []
    fx: list[FxCandidateOut] = []
    if listing is not None and listing.company_id == project.company_id:
        latest = (
            select(
                PriceObservation.provider_code,
                PriceObservation.close_basis,
                func.max(PriceObservation.trade_date).label("d"),
            )
            .where(PriceObservation.security_id == listing.id, PriceObservation.trade_date <= v)
            .group_by(PriceObservation.provider_code, PriceObservation.close_basis)
            .subquery()
        )
        for row in db.scalars(
            select(PriceObservation)
            .join(
                latest,
                (latest.c.provider_code == PriceObservation.provider_code)
                & (latest.c.close_basis == PriceObservation.close_basis)
                & (latest.c.d == PriceObservation.trade_date),
            )
            .where(PriceObservation.security_id == listing.id)
            .order_by(PriceObservation.id.desc())
        ):
            if any(
                p.provider_code == row.provider_code and p.close_basis == row.close_basis
                for p in prices
            ):
                continue  # an older revision of the same date
            prices.append(
                PriceCandidateOut(
                    observation_id=row.id,
                    listing_id=listing.id,
                    provider_code=row.provider_code,
                    close_basis=row.close_basis,
                    trade_date=row.trade_date,
                    close=row.close,
                    currency=row.currency,
                    stale=_stale_days(row.trade_date, v),
                )
            )
        price_ccy = listing.trading_currency
        if price_ccy and price_ccy != currency:
            for base, quote, conv in (
                (price_ccy, currency, "direct"),
                (currency, price_ccy, "reciprocal"),
            ):
                r = db.scalars(
                    select(FxRate)
                    .where(FxRate.base == base, FxRate.quote == quote, FxRate.obs_date <= v)
                    .order_by(FxRate.obs_date.desc(), FxRate.id.desc())
                    .limit(1)
                ).first()
                if r is not None:
                    fx.append(
                        FxCandidateOut(
                            fx_rate_id=r.id,
                            base=r.base,
                            quote=r.quote,
                            obs_date=r.obs_date,
                            rate=r.rate,
                            provider_series_id=r.provider_series_id,
                            conversion=conv,
                            stale=_stale_days(r.obs_date, v),
                        )
                    )
            if not fx:
                notes.append(
                    f"The listing trades in {price_ccy}; no stored {price_ccy}/{currency} "
                    "FX rate exists on or before the valuation date."
                )
        if not prices:
            notes.append("No stored price for this listing on or before the valuation date.")
    superseded = select(ReferenceDataset.supersedes_id).where(
        ReferenceDataset.supersedes_id.is_not(None)
    )
    reference = [
        ReferenceCandidateOut(
            reference_dataset_id=d.id,
            dataset_kind=d.dataset_kind,
            edition_date=d.edition_date,
            region=d.region,
            variant=d.variant,
            version_no=d.version_no,
            record_count=d.record_count,
            stale=add_months(d.edition_date, STALE_MONTHS) < v,
        )
        for d in db.scalars(
            select(ReferenceDataset)
            .where(ReferenceDataset.edition_date <= v, ReferenceDataset.id.not_in(superseded))
            .order_by(ReferenceDataset.dataset_kind, ReferenceDataset.edition_date.desc())
        )
    ]
    return CandidatesOut(
        valuation_date=v,
        currency=currency,
        dataset=_dataset_ref(dataset),
        periods=periods,
        rates=rates,
        prices=prices,
        fx=fx,
        reference=reference,
        notes=notes,
    )


def historical_context(db: Session, project_id: uuid.UUID, years: int) -> HistoricalContextOut:
    """Per fiscal year of the pinned dataset: growth, margins and ratios as context only."""
    project = get_project(db, project_id)
    dataset = db.get(FinancialDataset, project.dataset_id)
    assert dataset is not None
    codes = (
        "IS.REVENUE",
        "IS.EBIT",
        "IS.INCOME_TAX",
        "IS.PRETAX_INCOME",
        "CF.DA",
        "CF.CAPEX",
        "BS.RECEIVABLES",
        "BS.INVENTORY",
        "BS.PAYABLES",
    )
    rows = db.execute(
        select(FiscalPeriodRow, FinancialValue)
        .join(FinancialValue, FinancialValue.period_id == FiscalPeriodRow.id)
        .where(
            FinancialValue.dataset_id == dataset.id,
            FiscalPeriodRow.period_type == "FY",
            FinancialValue.line_item_code.in_(codes),
            FinancialValue.status == "ok",
        )
    ).all()
    by_period: dict[uuid.UUID, tuple[FiscalPeriodRow, dict[str, Decimal]]] = {}
    for p, v in rows:
        if v.value is not None:
            by_period.setdefault(p.id, (p, {}))[1][v.line_item_code] = v.value
    ordered = sorted(by_period.values(), key=lambda t: t[0].end_date)
    out: list[HistoryYearOut] = []
    with localcontext(make_context()):

        def ratio(a: Decimal | None, b: Decimal | None) -> Decimal | None:
            return None if a is None or b is None or b == 0 else a / b

        def growth(a: Decimal | None, b: Decimal | None) -> Decimal | None:
            r = ratio(a, b)
            return None if r is None else r - 1

        prev_rev: Decimal | None = None
        for p, vals in ordered:
            rev = vals.get("IS.REVENUE")
            nwc = None
            if all(c in vals for c in ("BS.RECEIVABLES", "BS.INVENTORY", "BS.PAYABLES")):
                nwc = vals["BS.RECEIVABLES"] + vals["BS.INVENTORY"] - vals["BS.PAYABLES"]
            capex = vals.get("CF.CAPEX")
            out.append(
                HistoryYearOut(
                    fiscal_year=p.fiscal_year,
                    end_date=p.end_date,
                    revenue=rev,
                    revenue_growth=growth(rev, prev_rev),
                    ebit_margin=ratio(vals.get("IS.EBIT"), rev),
                    tax_rate=ratio(vals.get("IS.INCOME_TAX"), vals.get("IS.PRETAX_INCOME")),
                    da_pct=ratio(vals.get("CF.DA"), rev),
                    capex_pct=ratio(abs(capex) if capex is not None else None, rev),
                    nwc_pct=ratio(nwc, rev),
                )
            )
            prev_rev = rev
    return HistoricalContextOut(
        dataset=_dataset_ref(dataset),
        years=out[-years:],
        note="Context only, computed from the pinned dataset; never an input until an analyst "
        "enters and approves an assumption. NWC = receivables + inventory − payables.",
    )


# --------------------------------------------------------------------------- runs


def run_or_404(db: Session, run_id: uuid.UUID) -> ValuationRun:
    run = db.get(ValuationRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", f"No valuation run {run_id}.")
    return run


def list_runs(
    db: Session,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID | None,
    methodology: str | None,
    limit: int,
    offset: int,
) -> Page[RunSummaryOut]:
    get_project(db, project_id)
    stmt = select(ValuationRun).where(ValuationRun.project_id == project_id)
    if scenario_id is not None:
        stmt = stmt.where(ValuationRun.scenario_id == scenario_id)
    if methodology is not None:
        stmt = stmt.where(ValuationRun.methodology == methodology)
    stmt = stmt.order_by(ValuationRun.created_at.desc(), ValuationRun.id)
    return paginate(db, stmt, limit, offset, lambda rows: _run_summaries(db, list(rows)))


def run_detail(db: Session, run_id: uuid.UUID) -> RunOut:
    run = run_or_404(db, run_id)
    decisions = db.scalars(
        select(RunDecision).where(RunDecision.run_id == run.id).order_by(RunDecision.id)
    ).all()
    users = _users(db, {run.created_by} | {d.user_id for d in decisions})
    trace = db.get(RunTrace, run.id)
    assert trace is not None
    return RunOut(
        **_summary(run, users[run.created_by]).model_dump(),
        method_version=run.method_version,
        block_id=run.block_id,
        dataset_content_sha256=run.dataset_content_sha256,
        engine_input_sha256=run.engine_input_sha256,
        module_versions=run.module_versions,
        outputs=run.outputs,
        validation=run.validation,
        decisions=[
            RunDecisionOut(
                kind=d.kind,
                code=d.code,
                reason=d.reason,
                detail=d.detail,
                user=users[d.user_id],
                created_at=d.created_at,
            )
            for d in decisions
        ],
        trace_sha256=trace.trace_sha256,
        trace_node_count=trace.node_count,
    )


def run_inputs(db: Session, run_id: uuid.UUID) -> RunInputsOut:
    run = run_or_404(db, run_id)
    rows = db.scalars(
        select(RunInput).where(RunInput.run_id == run.id).order_by(RunInput.input_key)
    ).all()
    return RunInputsOut(
        run_id=run.id,
        input_sha256=run.input_sha256,
        input_document=run.input_document,
        lineage=[
            RunInputOut(
                input_key=r.input_key,
                value=r.value,
                json=r.value_json,
                unit=r.unit,
                source_value=r.source_value,
                source_unit=r.source_unit,
                conversion=r.conversion,
                provenance_kind=r.provenance_kind,
                source_type=r.source_type,
                source_ref=r.source_ref,
                source_sha256=r.source_sha256,
                as_of_date=r.as_of_date,
                warnings=r.warnings,
            )
            for r in rows
        ],
    )


def run_trace(db: Session, run_id: uuid.UUID, node: str | None, depth: int) -> TraceOut:
    run = run_or_404(db, run_id)
    trace = db.get(RunTrace, run.id)
    assert trace is not None
    nodes: dict[str, dict[str, Any]] = trace.trace
    if node is None:
        return TraceOut(
            run_id=run.id, trace_sha256=trace.trace_sha256, root=None, nodes=nodes, truncated=False
        )
    if node not in nodes:
        raise ApiError(404, "not_found", f"No trace node {node!r} in run {run_id}.")
    picked: dict[str, dict[str, Any]] = {}
    frontier, truncated = [node], False
    for level in range(depth + 1):
        nxt: list[str] = []
        for n in frontier:
            if n in picked or n not in nodes:
                continue
            picked[n] = nodes[n]
            nxt.extend(nodes[n].get("inputs", []))
        frontier = nxt
        if level == depth and any(n in nodes and n not in picked for n in frontier):
            truncated = True
    return TraceOut(
        run_id=run.id, trace_sha256=trace.trace_sha256, root=node, nodes=picked, truncated=truncated
    )


# --------------------------------------------------------------------------- scenario summary


class ScenarioValueOut(BaseModel):
    scenario: ScenarioOut
    run: RunSummaryOut | None = Field(
        description="The scenario's latest saved run of the methodology, if any"
    )


class ScenarioSummaryOut(BaseModel):
    """Per-scenario values and, when complete, the probability-weighted value (MASTER_BRIEF
    §14.1). Weighted only when every weighted scenario has a run of the methodology with the
    same valuation date, currency and dataset; otherwise ``issues`` explain why not."""

    project_id: uuid.UUID
    methodology: str
    scenarios: list[ScenarioValueOut]
    probabilities_sum: Decimal | None
    weighted_per_share: Decimal | None
    weighted_primary_value: Decimal | None
    per_share_low: Decimal | None
    per_share_high: Decimal | None
    complete: bool
    issues: list[str]


def scenario_summary(db: Session, project_id: uuid.UUID, methodology: str) -> ScenarioSummaryOut:
    project = get_project(db, project_id)
    rows = db.scalars(
        select(Scenario)
        .where(Scenario.project_id == project.id, Scenario.status == "active")
        .order_by(Scenario.sort_order, Scenario.created_at)
    ).all()
    latest: dict[uuid.UUID, ValuationRun] = {}
    for s in rows:
        run = db.scalars(
            select(ValuationRun)
            .where(ValuationRun.scenario_id == s.id, ValuationRun.methodology == methodology)
            .order_by(ValuationRun.created_at.desc(), ValuationRun.id)
            .limit(1)
        ).first()
        if run is not None:
            latest[s.id] = run
    summaries = {
        r.id: s
        for r, s in zip(latest.values(), _run_summaries(db, list(latest.values())), strict=True)
    }
    items = [
        ScenarioValueOut(
            scenario=_scenario_out(s),
            run=summaries[latest[s.id].id] if s.id in latest else None,
        )
        for s in rows
    ]
    issues: list[str] = []
    weighted = [s for s in rows if s.probability is not None]
    total = sum((s.probability for s in weighted if s.probability is not None), Decimal(0))
    if not weighted:
        issues.append("No scenario probabilities are set.")
    for s in weighted:
        if s.id not in latest:
            issues.append(f"Scenario {s.name!r} has a probability but no {methodology} run.")
    runs = [latest[s.id] for s in weighted if s.id in latest]
    for attr, label in (
        ("valuation_date", "valuation dates"),
        ("currency", "currencies"),
        ("dataset_id", "datasets"),
    ):
        if len({getattr(r, attr) for r in runs}) > 1:
            issues.append(f"The weighted scenarios' runs use different {label}.")
    per_share = [r.per_share_value for r in latest.values()]
    w_ps = w_pv = None
    if weighted and not issues:
        with localcontext(make_context()):
            w_ps = sum(
                (
                    s.probability * latest[s.id].per_share_value
                    for s in weighted
                    if s.probability is not None
                ),
                Decimal(0),
            )
            w_pv = sum(
                (
                    s.probability * latest[s.id].primary_value
                    for s in weighted
                    if s.probability is not None
                ),
                Decimal(0),
            )
    return ScenarioSummaryOut(
        project_id=project.id,
        methodology=methodology,
        scenarios=items,
        probabilities_sum=total if weighted else None,
        weighted_per_share=w_ps,
        weighted_primary_value=w_pv,
        per_share_low=min(per_share) if per_share else None,
        per_share_high=max(per_share) if per_share else None,
        complete=bool(weighted) and not issues,
        issues=issues,
    )


def scenarios_by_id(db: Session, project_id: uuid.UUID) -> dict[uuid.UUID, ScenarioOut]:
    return {s.id: s for s in scenarios(db, project_id)}


# --------------------------------------------------------------------------- sensitivities


class SensitivitySummaryOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    metric: str
    label: str | None
    config: dict[str, Any]
    cell_count: int
    refused_count: int
    result_sha256: str
    engine_version: str
    created_by: UserRef
    created_at: datetime


class SensitivityOut(SensitivitySummaryOut):
    config_sha256: str
    base: dict[str, Any] = Field(description="The run's own value of the metric and variables")
    cells: list[dict[str, Any]] = Field(
        description="Coordinates with value (decimal string) or refused_code and message"
    )


class SensitivityVariableOut(BaseModel):
    variable: str
    label: str
    low: Decimal
    high: Decimal
    shift: bool
    methodologies: list[str]


def _sensitivity_summary(row: SensitivityRun, user: UserRef) -> SensitivitySummaryOut:
    return SensitivitySummaryOut(
        id=row.id,
        run_id=row.run_id,
        project_id=row.project_id,
        kind=row.kind,
        metric=row.metric,
        label=row.label,
        config=row.config,
        cell_count=row.cell_count,
        refused_count=row.refused_count,
        result_sha256=row.result_sha256,
        engine_version=row.engine_version,
        created_by=user,
        created_at=row.created_at,
    )


def list_sensitivities(
    db: Session, run_id: uuid.UUID, limit: int, offset: int
) -> Page[SensitivitySummaryOut]:
    run_or_404(db, run_id)
    stmt = (
        select(SensitivityRun)
        .where(SensitivityRun.run_id == run_id)
        .order_by(SensitivityRun.created_at.desc(), SensitivityRun.id)
    )

    def convert(rows: Any) -> list[SensitivitySummaryOut]:
        rows = list(rows)
        users = _users(db, {r.created_by for r in rows})
        return [_sensitivity_summary(r, users[r.created_by]) for r in rows]

    return paginate(db, stmt, limit, offset, convert)


def sensitivity_detail(db: Session, sensitivity_id: uuid.UUID) -> SensitivityOut:
    row = db.get(SensitivityRun, sensitivity_id)
    if row is None:
        raise ApiError(404, "not_found", f"No sensitivity {sensitivity_id}.")
    user = _users(db, {row.created_by})[row.created_by]
    return SensitivityOut(
        **_sensitivity_summary(row, user).model_dump(),
        config_sha256=row.config_sha256,
        base=row.base,
        cells=row.cells,
    )
