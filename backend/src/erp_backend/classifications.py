"""Classification service: store suggestions, confirm with a reason (§7.6.1; D24, D53).

Every write is audited. A suggestion never changes the effective class; only a
confirmation does (``market.companies.financial_classification``). Until a company has a
confirmation its effective class is ``needs_review``, which the engine refuses to save.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.classification import CLASSIFIER_VERSION, CONFIRMABLE, Suggestion, suggest
from erp_backend.ingestion.sec_ingest import stored_facts
from erp_backend.models import Company, CompanyClassification

MIN_REASON = 10


class ClassificationError(Exception):
    pass


def _latest(db: Session, company_id: uuid.UUID, kind: str) -> CompanyClassification | None:
    return db.scalars(
        select(CompanyClassification)
        .where(CompanyClassification.company_id == company_id, CompanyClassification.kind == kind)
        .order_by(CompanyClassification.id.desc())
        .limit(1)
    ).first()


def effective_classification(company: Company) -> str:
    return company.financial_classification or "needs_review"


@dataclass(frozen=True)
class SuggestResult:
    suggestion: Suggestion
    row: CompanyClassification
    created: bool


def suggest_classification(
    db: Session, company: Company, *, user_id: uuid.UUID | None = None
) -> SuggestResult:
    """Compute and store a suggestion; nothing is written when the evidence is unchanged."""
    suggestion = suggest(company.sic_code, stored_facts(db, company.id))
    evidence = suggestion.as_dict()
    previous = _latest(db, company.id, "suggested")
    if previous is not None and previous.evidence == evidence:
        return SuggestResult(suggestion, previous, created=False)
    row = CompanyClassification(
        company_id=company.id,
        kind="suggested",
        classification=suggestion.classification,
        evidence=evidence,
        classifier_version=CLASSIFIER_VERSION,
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="classification_suggested",
        entity_type="company",
        entity_id=str(company.id),
        user_id=user_id,
        before={"suggested": previous.classification if previous else None},
        after={"suggested": suggestion.classification, "rationale": suggestion.rationale},
    )
    return SuggestResult(suggestion, row, created=True)


def confirm_classification(
    db: Session,
    company: Company,
    classification: str,
    *,
    reason: str,
    user_id: uuid.UUID | None = None,
) -> CompanyClassification:
    """Analyst confirmation; may differ from the suggestion (the reason says why)."""
    if classification not in CONFIRMABLE:
        raise ClassificationError(
            f"Cannot confirm {classification!r}; choose one of {', '.join(CONFIRMABLE)}"
        )
    text = (reason or "").strip()
    if len(text) < MIN_REASON or not any(ch.isalpha() for ch in text):
        raise ClassificationError(
            f"A meaningful reason of at least {MIN_REASON} characters is required"
        )
    suggestion = _latest(db, company.id, "suggested")
    row = CompanyClassification(
        company_id=company.id,
        kind="confirmed",
        classification=classification,
        evidence={
            "suggestion_id": suggestion.id if suggestion else None,
            "suggested": suggestion.classification if suggestion else None,
            "agrees_with_suggestion": bool(suggestion)
            and suggestion is not None
            and suggestion.classification == classification,
        },
        reason=text,
        created_by=user_id,
    )
    db.add(row)
    before = company.financial_classification
    company.financial_classification = classification
    db.flush()
    audit.record(
        db,
        action="classification_confirmed",
        entity_type="company",
        entity_id=str(company.id),
        user_id=user_id,
        before={"financial_classification": before},
        after={
            "financial_classification": classification,
            "confirmation_id": row.id,
            "suggested": suggestion.classification if suggestion else None,
        },
        reason=text,
    )
    return row
