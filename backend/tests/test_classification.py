"""Financial-business classification: suggestion logic, confirmation workflow (§7.6.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from erp_backend.classification import CLASSES, CONFIRMABLE, suggest
from erp_backend.classifications import (
    ClassificationError,
    confirm_classification,
    effective_classification,
    suggest_classification,
)
from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.models import AuditEvent, Company, CompanyClassification
from erp_engine.inputs import Classification
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .test_datasets import add_facts

END = date(2024, 12, 31)


def bs(taxonomy: str = "us-gaap", **values: str) -> list[RawFact]:
    return [
        RawFact(taxonomy, concept, "USD", Decimal(v), None, END, None, None, "10-K",
                date(2025, 2, 1), "0000000001-25-000001", None)
        for concept, v in values.items()
    ]  # fmt: skip


def test_classes_match_the_engine_policy_gate() -> None:
    assert set(CLASSES) == {c.value for c in Classification}
    assert "needs_review" not in CONFIRMABLE


@pytest.mark.parametrize(
    ("sic", "facts", "expected", "why"),
    [
        # RY-like: bank SIC and IFRS deposits dominate liabilities; insurance is secondary.
        ("6029", bs("ifrs-full", Assets="2000", Liabilities="1900", DepositsFromBanks="100",
                    BalancesOnTermDepositsFromCustomers="900",
                    InsuranceContractsThatAreLiabilities="20"), "bank", "agree"),
        ("6029", bs(Assets="100", Liabilities="90"), "bank", "no strong XBRL"),
        ("3571", bs(Assets="100", Liabilities="60"), "non_financial", "no financial SIC"),
        (None, bs("ifrs-full", Assets="100", Liabilities="90",
                  InsuranceContractsThatAreLiabilities="50"), "insurer", "no SIC"),
        ("7372", bs(Assets="100", Liabilities="90", Deposits="60"), "needs_review",
         "non-financial"),
        ("6311", bs(Assets="100", Liabilities="90", Deposits="60"), "needs_review",
         "suggests insurer, XBRL suggests bank"),
        ("6411", bs(Assets="100", Liabilities="50"), "asset_manager_broker", "no strong XBRL"),
        ("6512", bs(Assets="100", Liabilities="50"), "needs_review", "always needs review"),
        ("6798", bs(Assets="100", Liabilities="50", RealEstateInvestmentPropertyNet="80"), "reit",
         "agree"),
        ("6770", bs(Assets="100", Liabilities="5", InvestmentOwnedAtFairValue="90"),
         "investment_holding", "agree"),
    ],
)  # fmt: skip
def test_suggestion_rules(sic: str | None, facts: list[RawFact], expected: str, why: str) -> None:
    s = suggest(sic, facts)
    assert s.classification == expected and why in s.rationale


def test_evidence_strength_dominance_and_latest_balance_sheet() -> None:
    old = [
        RawFact("us-gaap", "Deposits", "USD", Decimal("80"), None, date(2020, 12, 31), None,
                None, "10-K", date(2021, 2, 1), "0000000001-21-000001", None),
        RawFact("us-gaap", "Liabilities", "USD", Decimal("90"), None, date(2020, 12, 31), None,
                None, "10-K", date(2021, 2, 1), "0000000001-21-000001", None),
    ]  # fmt: skip
    s = suggest("3571", [*old, *bs(Assets="100", Liabilities="90", Deposits="10")])
    assert s.balance_sheet_date == END  # only the latest balance sheet counts
    (e,) = s.evidence
    assert (e.cls, e.strength, e.ratio) == ("bank", "weak", Decimal("0.1111"))
    assert s.classification == "non_financial" and "weak" in s.notes[0]
    # A total concept is used instead of its components (no double counting).
    t = suggest(None, bs("ifrs-full", Assets="100", Liabilities="100", DepositsFromCustomers="40",
                         BalancesOnTermDepositsFromCustomers="30"))  # fmt: skip
    assert t.evidence[0].concepts == {"DepositsFromCustomers": "40"}
    assert suggest("6029", []).notes == ("no balance sheet with total assets in the stored facts",)


# ---- Service ---------------------------------------------------------------------------------


@pytest.fixture
def bank(db: Session) -> Company:
    c = Company(legal_name="Bank Co", sic_code="6022", reporting_currency="USD",
                accounting_standard="US_GAAP")  # fmt: skip
    db.add(c)
    db.flush()
    f = ("10-K", date(2025, 2, 1), "0000000001-25-000001")
    add_facts(db, c, [("Assets", "1000", None, END, *f), ("Liabilities", "900", None, END, *f),
                      ("Deposits", "700", None, END, *f)])  # fmt: skip
    return c


def test_suggestion_is_stored_once_and_never_becomes_effective(db: Session, bank: Company) -> None:
    first = suggest_classification(db, bank)
    assert first.created and first.row.classification == "bank"
    assert first.row.evidence["xbrl_evidence"][0]["strength"] == "strong"
    assert not suggest_classification(db, bank).created  # unchanged evidence: no new row
    assert effective_classification(bank) == "needs_review"  # suggestion is not a decision
    assert (
        db.scalar(select(func.count()).where(AuditEvent.action == "classification_suggested")) == 1
    )


def test_confirmation_requires_a_reason_and_is_audited(db: Session, bank: Company) -> None:
    suggest_classification(db, bank)
    with pytest.raises(ClassificationError, match="meaningful reason"):
        confirm_classification(db, bank, "bank", reason="ok")
    with pytest.raises(ClassificationError, match="Cannot confirm"):
        confirm_classification(db, bank, "needs_review", reason="Not sure about this one yet.")
    row = confirm_classification(
        db, bank, "bank", reason="State commercial bank; deposits are 78% of liabilities."
    )
    assert bank.financial_classification == "bank" == effective_classification(bank)
    assert row.evidence["agrees_with_suggestion"] is True
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "classification_confirmed")
    ).one()
    assert event.before == {"financial_classification": None}
    assert event.after["suggested"] == "bank"  # type: ignore[index]
    # The analyst may disagree with the suggestion; the history keeps both.
    confirm_classification(db, bank, "specialty_lender", reason="Deposits are brokered funding.")
    kinds = db.scalars(
        select(CompanyClassification.kind).where(CompanyClassification.company_id == bank.id)
        .order_by(CompanyClassification.id)
    ).all()  # fmt: skip
    assert kinds == ["suggested", "confirmed", "confirmed"]
    assert bank.financial_classification == "specialty_lender"


def test_classification_history_is_append_only(db: Session, bank: Company) -> None:
    suggest_classification(db, bank)
    with pytest.raises(DBAPIError, match="confirmed_needs_reason"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO market.company_classifications (company_id, kind, classification, "
                "evidence, reason) VALUES (:c, 'confirmed', 'bank', '{}', 'short')"
            ),
            {"c": bank.id},
        )
    for statement in (
        "UPDATE market.company_classifications SET classification = 'insurer'",
        "DELETE FROM market.company_classifications",
    ):
        with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
            db.execute(text(statement))
