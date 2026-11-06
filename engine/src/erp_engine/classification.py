"""Financial-business policy gate (methodology §13, D24)."""

from __future__ import annotations

from erp_engine.core.errors import EngineError
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.inputs import Classification

BLOCKED = {
    Classification.BANK,
    Classification.INSURER,
    Classification.SPECIALTY_LENDER,
    Classification.INVESTMENT_HOLDING,
}


def gate(classification: Classification, report: ValidationReport) -> None:
    if classification in BLOCKED:
        raise EngineError(
            "dcf_not_permitted",
            f"DCF (FCFF/FCFE) is not permitted for {classification.value} in R1.",
        )
    if classification is Classification.REIT:
        report.add(
            "reit_fair_value_gains",
            Severity.WARNING,
            "REIT: confirm fair-value gains are excluded from cash flows; NAV/AFFO "
            "methods (R4) are usually more appropriate.",
        )
    elif classification is Classification.ASSET_MANAGER_BROKER:
        report.add(
            "client_cash_excluded",
            Severity.WARNING,
            "Asset manager/broker: confirm client and segregated cash are excluded "
            "from bridge cash.",
        )
    elif classification is Classification.NEEDS_REVIEW:
        report.add(
            "classification_needs_review",
            Severity.WARNING,
            "Financial classification must be confirmed before saving.",
            blocks_save=True,
        )
