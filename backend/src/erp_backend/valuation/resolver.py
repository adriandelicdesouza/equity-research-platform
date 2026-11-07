"""Input resolution: from a project, a scenario and explicitly named rows to a canonical input
document (design §4.1, §5; Q4, Q5, Q6, Q11, Q12).

The resolver only reads. It never picks "latest" data: the dataset, the balance-sheet period,
the price and FX rows and every sourced assumption are named by id, and anything that does not
match is refused rather than substituted. It returns *issues* instead of raising, so a preview
can show all of them at once; a saved run refuses on the first blocking one.

Issue levels:

* ``error``   — the inputs cannot be computed (missing or inconsistent data);
* ``gate``    — computable for a preview, but a saved run is refused (draft dataset,
  unapproved assumption, missing acknowledgement or check override, unconfirmed class);
* ``warning`` — recorded on the run and shown with every output (stale inputs).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend.classifications import effective_classification
from erp_backend.datasets import stored_checks
from erp_backend.models import (
    Company,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    FxRate,
    PriceObservation,
    Scenario,
    Security,
    ValuationProject,
)
from erp_backend.valuation import assumptions as asm
from erp_backend.valuation import sources
from erp_backend.valuation.blocks import Block, block_from_row, current_block
from erp_backend.valuation.canonical import jsonable
from erp_backend.valuation.common import MIN_REASON
from erp_backend.valuation.compute import METHODS, engine_versions
from erp_backend.valuation.errors import ValuationRefused

RESOLVER_VERSION = "1.0.0"
DOCUMENT_FORMAT = 1
STALE_DAYS = 7  # prices, risk-free rates, FX (Q6)
STALE_MONTHS = 13  # Damodaran editions (Q6)


@dataclass(frozen=True)
class Issue:
    level: str  # error | gate | warning
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "code": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class InputOverride:
    input: str
    line_item: str | None
    kind: str  # value | not_applicable
    reason: str
    value: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class Acknowledgement:
    code: str
    reason: str


@dataclass(frozen=True)
class CheckOverride:
    check_code: str
    subject: str
    reason: str


@dataclass(frozen=True)
class RunRequest:
    methodology: str
    scenario_id: uuid.UUID
    expected_scenario_revision: int
    dataset_id: uuid.UUID
    dataset_content_sha256: str
    balance_sheet_period_id: uuid.UUID
    valuation_date: date
    listing_id: uuid.UUID | None = None
    price_observation_id: int | None = None
    fx_rate_ids: tuple[int, ...] = ()
    input_overrides: tuple[InputOverride, ...] = ()
    acknowledgements: tuple[Acknowledgement, ...] = ()
    check_overrides: tuple[CheckOverride, ...] = ()


@dataclass
class Resolution:
    issues: list[Issue] = field(default_factory=list)
    document: dict[str, Any] | None = None
    dataset: FinancialDataset | None = None
    block_id: uuid.UUID | None = None
    classification: str | None = None
    scenario: Scenario | None = None

    def add(self, level: str, code: str, message: str, **details: Any) -> None:
        self.issues.append(Issue(level, code, message, details))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.level in ("error", "gate")]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    def refusal(self) -> ValuationRefused:
        first = self.blocking[0]
        return ValuationRefused(first.code, first.message, [i.as_dict() for i in self.blocking])


def _meaningful(text: str | None) -> bool:
    t = (text or "").strip()
    return len(t) >= MIN_REASON and any(c.isalpha() for c in t)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def _dated(res: Resolution, key: str, as_of: date | None, v: date, *, months: bool) -> list[str]:
    """Look-ahead is an error; staleness a warning (Q6). Returns warning codes for the input."""
    if as_of is None:
        return []
    if as_of > v:
        res.add(
            "error",
            "input_lookahead",
            f"{key} is dated {as_of}, after the valuation date {v}.",
            input=key,
            as_of=as_of.isoformat(),
        )
        return []
    stale = add_months(as_of, STALE_MONTHS) < v if months else (v - as_of).days > STALE_DAYS
    if stale:
        limit = f"{STALE_MONTHS} months" if months else f"{STALE_DAYS} calendar days"
        res.add(
            "warning",
            "input_stale",
            f"{key} is dated {as_of}, more than {limit} before the valuation date {v}.",
            input=key,
            as_of=as_of.isoformat(),
        )
        return ["input_stale"]
    return []


# --------------------------------------------------------------------------- dataset values


def _values_at(
    db: Session, dataset: FinancialDataset, period: FiscalPeriodRow
) -> dict[str, FinancialValue]:
    rows = db.scalars(
        select(FinancialValue).where(
            FinancialValue.dataset_id == dataset.id, FinancialValue.period_id == period.id
        )
    ).all()
    return {r.line_item_code: r for r in rows}


def _dataset_inputs(
    res: Resolution,
    block: Block,
    lease_treatment: str | None,
    values: dict[str, FinancialValue],
    dataset: FinancialDataset,
    period: FiscalPeriodRow,
    overrides: tuple[InputOverride, ...],
    inputs: dict[str, Any],
) -> None:
    mapping = dict(block.line_items)
    if lease_treatment is not None:
        extra = block.lease_line_items(lease_treatment)
        if extra is None:
            res.add(
                "error",
                "lease_treatment_not_supported",
                f"Lease treatment {lease_treatment!r} is not supported for this block in "
                "Phase 5 (per-year lease forecasts are deferred).",
                lease_treatment=lease_treatment,
            )
        else:
            mapping.update(extra)
    used: set[tuple[str, str | None]] = set()
    by_key: dict[tuple[str, str | None], InputOverride] = {}
    for o in overrides:
        if _override_problem(o) is not None:
            res.add("error", *_override_problem(o), input=o.input, line_item=o.line_item)  # type: ignore[misc]
            used.add((o.input, o.line_item))
        elif (o.input, o.line_item) in by_key:
            res.add("error", "invalid_override", "Duplicate override.", input=o.input)
        else:
            by_key[(o.input, o.line_item)] = o
    for name, components in sorted(mapping.items()):
        key = f"dataset:{name}"
        whole = by_key.get((name, None))
        if whole is not None:
            used.add((name, None))
            value = Decimal(0) if whole.kind == "not_applicable" else Decimal(str(whole.value))
            inputs[key] = {
                "value": value,
                "unit": "count" if name == "shares.basic" else "currency",
                "provenance_kind": "analyst_adjusted",
                "source_type": "analyst_override",
                "source_ref": f"override:{name}",
                "override": _override_json(whole),
                "warnings": [],
            }
            continue
        total = Decimal(0)
        parts: list[dict[str, Any]] = []
        ok = True
        for comp in components:
            sign, code = (-1 if comp[0] == "-" else 1), comp[1:]
            row = values.get(code)
            override = by_key.get((name, code))
            if override is not None:
                used.add((name, code))
                amount = (
                    Decimal(0)
                    if override.kind == "not_applicable"
                    else Decimal(str(override.value))
                )
                parts.append(
                    {
                        "line_item": code,
                        "sign": sign,
                        "value": amount,
                        "override": _override_json(override),
                    }
                )
                total += sign * amount
                continue
            if row is None or row.value is None:
                res.add(
                    "error",
                    "input_missing",
                    f"{code} has no value at {period.end_date} in dataset v{dataset.version_no} "
                    f"(needed for {name}); enter it or declare it not applicable, with a reason.",
                    input=name,
                    line_item=code,
                )
                ok = False
                continue
            if row.status != "ok":
                res.add(
                    "error",
                    "input_ambiguous",
                    f"{code} is {row.status} in the dataset.",
                    input=name,
                    line_item=code,
                )
                ok = False
                continue
            expected_unit = "shares" if name == "shares.basic" else dataset.currency
            if row.unit != expected_unit:
                res.add(
                    "error",
                    "unit_mismatch",
                    f"{code} is in {row.unit}, expected {expected_unit}.",
                    input=name,
                    line_item=code,
                )
                ok = False
                continue
            parts.append(
                {
                    "line_item": code,
                    "sign": sign,
                    "value": row.value,
                    "provenance_kind": row.provenance_kind,
                    "concept": row.concept,
                    "source_fact_ids": list(row.source_fact_ids),
                    "import_value_id": row.import_value_id,
                }
            )
            total += sign * row.value
        if ok:
            inputs[key] = {
                "value": total,
                "unit": "count" if name == "shares.basic" else "currency",
                "provenance_kind": "dataset",
                "source_type": "dataset_value",
                "source_ref": f"dataset:{dataset.id}:{period.id}:{name}",
                "source_sha256": dataset.content_sha256,
                "as_of": period.end_date,
                "components": parts,
                "warnings": [],
            }
    for o in overrides:
        if (o.input, o.line_item) not in used:
            res.add(
                "error",
                "invalid_override",
                f"Override for {o.input}"
                f"{'/' + o.line_item if o.line_item else ''} matches no mapped input.",
                input=o.input,
                line_item=o.line_item,
            )


def _override_problem(o: InputOverride) -> tuple[str, str] | None:
    if not _meaningful(o.reason):
        return (
            "reason_required",
            f"An override needs a reason of at least {MIN_REASON} characters.",
        )
    if o.kind == "not_applicable":
        return None if o.value is None else ("invalid_override", "not_applicable takes no value.")
    if o.kind != "value":
        return ("invalid_override", "An override is 'value' or 'not_applicable'.")
    try:
        Decimal(str(o.value))
    except ArithmeticError:
        return ("invalid_override", "An override value must be a decimal string.")
    if o.value is None or len((o.source or "").strip()) < 3:
        return ("invalid_override", "A value override needs a value and a written source.")
    return None


def _override_json(o: InputOverride) -> dict[str, Any]:
    return {"kind": o.kind, "value": o.value, "source": o.source, "reason": o.reason}


# --------------------------------------------------------------------------- assumptions


def _assumption_inputs(
    db: Session,
    res: Resolution,
    state: asm.State,
    methodology: str,
    block: Block,
    v: date,
    currency: str,
    inputs: dict[str, Any],
) -> None:
    required = set(asm.required_keys(state, methodology, block))
    horizon = state.value("horizon_years")
    horizon_n = int(horizon) if isinstance(horizon, Decimal) else 0
    for ident in sorted(required, key=lambda k: (k[0], k[1] or 0)):
        if state.get(*ident) is None:
            key, period = ident
            res.add(
                "error",
                "input_missing",
                f"Assumption {key}{'' if period is None else f'[{period}]'} is missing.",
                input=key,
                period_index=period,
            )
    for (key, period), cur in sorted(state.items.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        if not asm.applicable(key, methodology):
            continue
        if period is not None and period > horizon_n:
            continue  # beyond the horizon: not used
        name = f"assumption:{key}" + ("" if period is None else f"[{period}]")
        row = cur.row
        if row.status != "approved":
            res.add(
                "gate",
                "assumption_not_approved",
                f"{name.split(':', 1)[1]} is {row.status}; approve it before saving a run.",
                input=key,
                period_index=period,
            )
        spec = asm.REGISTRY[key]
        item: dict[str, Any] = {
            "unit": row.unit,
            "provenance_kind": row.provenance_kind,
            "status": row.status,
            "revision_id": row.id,
            "source_type": "assumption_revision",
            "source_ref": f"assumption_revision:{row.id}",
            "source": row.source,
            "warnings": [],
        }
        if row.value is not None:
            item["value"] = row.value
        else:
            item["json"] = row.value_json
        resolved = None
        if row.source is not None and row.source.get("type") in {
            "rate_observation",
            "reference_value",
        }:
            try:
                resolved = sources.resolve(db, row.source, market=spec.market)
            except ValuationRefused as exc:
                res.add("error", exc.code, exc.message, input=key)
                continue
        elif row.source is not None and row.source.get("type") == "analyst" and spec.market:
            resolved = sources.analyst(row.source, market=True)
        if resolved is not None:
            if resolved.superseded:
                res.add(
                    "error",
                    "input_superseded",
                    f"{key}: its source {resolved.source_ref} has been superseded by a newer "
                    "revision; select the current one.",
                    input=key,
                )
            item["warnings"] = _dated(
                res, key, resolved.as_of, v, months=resolved.source_type == "reference_value"
            )
            item.update(
                source_type=resolved.source_type,
                source_ref=resolved.source_ref,
                source_sha256=resolved.source_sha256,
                as_of=resolved.as_of,
                source_value=resolved.published_value,
                source_unit=resolved.published_unit,
                conversion=resolved.conversion,
            )
            if resolved.currency is not None:
                item["currency"] = resolved.currency
                if resolved.currency != currency:
                    res.add(
                        "error",
                        "currency_mismatch",
                        f"{key} is in {resolved.currency}; the cash flows are in {currency}.",
                        input=key,
                    )
        inputs[name] = item


# --------------------------------------------------------------------------- market inputs


def _market_inputs(
    db: Session,
    res: Resolution,
    request: RunRequest,
    project: ValuationProject,
    currency: str,
    needs_price: bool,
    inputs: dict[str, Any],
) -> uuid.UUID | None:
    listing_id = request.listing_id or project.listing_id
    v = request.valuation_date
    if request.price_observation_id is None:
        if needs_price:
            res.add(
                "error",
                "input_missing",
                "Market-value weights need a market price (price_observation_id).",
                input="price",
            )
        if request.fx_rate_ids:
            res.add("error", "invalid_input", "FX rates were given without a price.", input="fx")
        return listing_id
    price = db.get(PriceObservation, request.price_observation_id)
    if price is None:
        res.add(
            "error",
            "source_not_found",
            f"No price observation {request.price_observation_id}.",
            input="price",
        )
        return listing_id
    listing = db.get(Security, price.security_id)
    if listing is None or listing.company_id != project.company_id:
        res.add(
            "error",
            "listing_mismatch",
            "The price is not for a listing of this company.",
            input="price",
        )
        return listing_id
    if listing_id is not None and listing.id != listing_id:
        res.add(
            "error",
            "listing_mismatch",
            "The price is for another listing than the run's.",
            input="price",
        )
    newer = db.scalar(
        select(PriceObservation.id).where(
            PriceObservation.security_id == price.security_id,
            PriceObservation.provider_code == price.provider_code,
            PriceObservation.trade_date == price.trade_date,
            PriceObservation.close_basis == price.close_basis,
            PriceObservation.id > price.id,
        )
    )
    if newer is not None:
        res.add(
            "error",
            "input_superseded",
            "The price observation has a newer revision.",
            input="price",
        )
    inputs["market:price"] = {
        "value": price.close,
        "unit": "currency",
        "currency": price.currency,
        "provenance_kind": "provider_market",
        "source_type": "price_observation",
        "source_ref": f"price_observation:{price.id}",
        "as_of": price.trade_date,
        "close_basis": price.close_basis,
        "provider_code": price.provider_code,
        "warnings": _dated(res, "price", price.trade_date, v, months=False),
    }
    used: set[int] = set()
    if price.currency != currency:
        fx_row: FxRate | None = None
        conversion = ""
        for fid in request.fx_rate_ids:
            row = db.get(FxRate, fid)
            if row is None:
                continue
            if (row.base, row.quote) == (price.currency, currency):
                fx_row, conversion = row, "direct"
            elif (row.base, row.quote) == (currency, price.currency):
                fx_row, conversion = row, "reciprocal"
            if fx_row is not None:
                used.add(fid)
                break
        if fx_row is None:
            res.add(
                "error",
                "input_missing",
                f"The price is in {price.currency}; name a stored "
                f"{price.currency}/{currency} FX rate (either direction).",
                input="fx",
            )
        else:
            newer_fx = db.scalar(
                select(FxRate.id).where(
                    FxRate.base == fx_row.base,
                    FxRate.quote == fx_row.quote,
                    FxRate.obs_date == fx_row.obs_date,
                    FxRate.provider_series_id == fx_row.provider_series_id,
                    FxRate.id > fx_row.id,
                )
            )
            if newer_fx is not None:
                res.add(
                    "error", "input_superseded", "The FX rate has a newer revision.", input="fx"
                )
            inputs["market:fx"] = {
                "value": None,
                "source_value": fx_row.rate,
                "source_unit": "ratio",
                "unit": "ratio",
                "conversion": conversion,
                "published_direction": f"{fx_row.quote} per 1 {fx_row.base}",
                "from_currency": price.currency,
                "to_currency": currency,
                "provenance_kind": "provider_market",
                "source_type": "fx_rate",
                "source_ref": f"fx_rate:{fx_row.id}",
                "provider_series_id": fx_row.provider_series_id,
                "as_of": fx_row.obs_date,
                "warnings": _dated(res, "fx", fx_row.obs_date, v, months=False),
            }
    for fid in request.fx_rate_ids:
        if fid not in used:
            res.add(
                "error",
                "invalid_input",
                f"FX rate {fid} is not needed or does not match the "
                "price and reporting currencies.",
                input="fx",
            )
    return listing.id


# --------------------------------------------------------------------------- gates


def _class_gates(res: Resolution, block: Block, cls: str, request: RunRequest) -> None:
    if cls == "needs_review":
        res.add(
            "gate",
            "classification_unconfirmed",
            "The company's financial classification must be confirmed before saving a run.",
        )
    if block.policy == "blocked":
        res.add(
            "error",
            "classification_blocked",
            f"{request.methodology} is not permitted for {cls} companies (Release 1 policy).",
            classification=cls,
        )
    acks = {a.code: a for a in request.acknowledgements}
    for code in block.acknowledgements:
        ack = acks.get(code)
        if ack is None or not _meaningful(ack.reason):
            res.add(
                "gate",
                "acknowledgement_required",
                f"Acknowledge {code} with a reason (≥ {MIN_REASON} characters).",
                ack=code,
            )
    for code in acks:
        if code not in block.acknowledgements:
            res.add(
                "error",
                "invalid_acknowledgement",
                f"{code} is not required for this run.",
                ack=code,
            )


def _check_gates(
    db: Session,
    res: Resolution,
    dataset: FinancialDataset,
    period: FiscalPeriodRow,
    request: RunRequest,
) -> list[dict[str, Any]]:
    failed = [
        c
        for c in stored_checks(db, dataset)
        if c.status == "failed"
        and c.severity == "error"
        and c.period is not None
        and (c.period.period_type, c.period.end) == (period.period_type, period.end_date)
    ]
    overrides = {(o.check_code, o.subject): o for o in request.check_overrides}
    applied: list[dict[str, Any]] = []
    for c in failed:
        o = overrides.pop((c.check_code, c.subject), None)
        if o is None or not _meaningful(o.reason):
            res.add(
                "gate",
                "checks_failed",
                f"Dataset check {c.check_code} ({c.subject}) failed for {period.end_date}; "
                "override it with a reason to proceed.",
                check_code=c.check_code,
                subject=c.subject,
                check_message=c.message,
            )
        else:
            applied.append(
                {
                    "check_code": c.check_code,
                    "subject": c.subject,
                    "message": c.message,
                    "reason": o.reason.strip(),
                }
            )
    for code, subject in overrides:
        res.add(
            "error",
            "invalid_check_override",
            f"No failed error check {code} ({subject}) for this period.",
            check_code=code,
            subject=subject,
        )
    return applied


# --------------------------------------------------------------------------- entry point


def resolve(db: Session, project: ValuationProject, request: RunRequest) -> Resolution:
    res = Resolution()
    if request.methodology not in METHODS:
        res.add("error", "invalid_methodology", f"Unknown methodology {request.methodology!r}.")
        return res
    scenario = db.get(Scenario, request.scenario_id)
    if scenario is None or scenario.project_id != project.id:
        res.add("error", "not_found", f"No scenario {request.scenario_id} in this project.")
        return res
    res.scenario = scenario
    if scenario.revision_no != request.expected_scenario_revision:
        res.add(
            "error",
            "revision_conflict",
            f"The scenario changed (revision {scenario.revision_no}, you sent "
            f"{request.expected_scenario_revision}); reload and retry.",
            current_revision=scenario.revision_no,
        )
    # Dataset: exactly the pinned version, hash-confirmed; never substituted.
    dataset = db.get(FinancialDataset, request.dataset_id)
    if request.dataset_id != project.dataset_id or dataset is None:
        res.add(
            "error",
            "dataset_mismatch",
            "The run must use the project's pinned dataset "
            f"({project.dataset_id}); rebase the project to use another.",
            pinned_dataset_id=str(project.dataset_id),
        )
        return res
    res.dataset = dataset
    if dataset.content_sha256 != request.dataset_content_sha256:
        res.add(
            "error",
            "dataset_hash_mismatch",
            "The dataset's content hash differs from the one you confirmed.",
            content_sha256=dataset.content_sha256,
        )
    if dataset.status == "discarded":
        res.add("error", "dataset_discarded", "The pinned dataset was discarded; rebase.")
        return res
    if dataset.status != "locked":
        res.add(
            "gate",
            "dataset_not_locked",
            f"Dataset v{dataset.version_no} is a draft: "
            "previews are allowed, saved runs need a locked dataset.",
        )
    period = db.get(FiscalPeriodRow, request.balance_sheet_period_id)
    if period is None or period.company_id != project.company_id or period.period_type != "FY":
        res.add(
            "error",
            "invalid_period",
            "The balance-sheet period must be a fiscal year of "
            "this company (no stub periods in Phase 5).",
        )
        return res
    values = _values_at(db, dataset, period)
    if not values:
        res.add(
            "error",
            "invalid_period",
            f"Dataset v{dataset.version_no} has no values for {period.end_date}.",
        )
        return res
    v = request.valuation_date
    if v < period.end_date:
        res.add(
            "error",
            "invalid_dates",
            f"The valuation date {v} is before the balance-sheet date {period.end_date}.",
        )
    company = db.get(Company, project.company_id)
    assert company is not None
    currency = dataset.currency
    cls = effective_classification(company)
    res.classification = cls
    row = current_block(db, cls if cls != "needs_review" else "non_financial", request.methodology)
    assert row is not None
    block = block_from_row(row)
    res.block_id = row.id
    _class_gates(res, block, cls, request)
    if block.policy == "blocked":
        return res
    applied_checks = _check_gates(db, res, dataset, period, request)
    state = asm.current_state(db, scenario)
    inputs: dict[str, Any] = {}
    _assumption_inputs(db, res, state, request.methodology, block, v, currency, inputs)
    lease = state.value("lease_treatment") if request.methodology == "dcf_fcff" else None
    _dataset_inputs(res, block, lease, values, dataset, period, request.input_overrides, inputs)
    needs_price = (
        request.methodology == "dcf_fcff" and state.value("capital_structure_basis") == "market"
    )
    listing_id = _market_inputs(db, res, request, project, currency, needs_price, inputs)
    if v > period.end_date:
        res.add(
            "warning",
            "dividends_not_modelled",
            "Dividends between the balance-sheet date "
            "and the valuation date are not deducted in the roll-forward (Phase 5).",
        )
    if res.errors:
        return res
    method = METHODS[request.methodology]
    res.document = jsonable(
        {
            "format": DOCUMENT_FORMAT,
            "resolver_version": RESOLVER_VERSION,
            "project_id": str(project.id),
            "scenario_id": str(scenario.id),
            "scenario_revision": scenario.revision_no,
            "methodology": request.methodology,
            "method_version": method["version"],
            "company_id": str(project.company_id),
            "listing_id": str(listing_id) if listing_id else None,
            "currency": currency,
            "classification": cls,
            "block": {
                "id": str(row.id),
                "version": row.block_version,
                "definition_sha256": row.definition_sha256,
            },
            "dataset": {
                "id": str(dataset.id),
                "version_no": dataset.version_no,
                "basis": dataset.basis,
                "status": dataset.status,
                "content_sha256": dataset.content_sha256,
                "mapping_version": dataset.mapping_version,
                "facts_as_of": dataset.facts_as_of,
            },
            "balance_sheet_period": {
                "id": str(period.id),
                "fiscal_year": period.fiscal_year,
                "start_date": period.start_date,
                "end_date": period.end_date,
            },
            "valuation_date": v,
            "inputs": inputs,
            "acknowledgements": sorted(
                ({"code": a.code, "reason": a.reason.strip()} for a in request.acknowledgements),
                key=lambda a: a["code"],
            ),
            "check_overrides": applied_checks,
            "input_overrides": sorted(
                (
                    _override_json(o) | {"input": o.input, "line_item": o.line_item}
                    for o in request.input_overrides
                ),
                key=lambda o: (o["input"], o["line_item"] or ""),
            ),
            "warnings": [w.as_dict() for w in res.warnings],
            "engine": engine_versions(),
        }
    )
    return res
