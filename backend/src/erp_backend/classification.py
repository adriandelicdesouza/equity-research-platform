"""Financial-business classification: suggestion from SIC + XBRL evidence (§7.6.1; D24, D53).

Pure. The suggestion is evidence for the analyst, never the classification used for a
valuation: only an analyst-confirmed class is (until then the effective class is
``needs_review``, which the engine's policy gate refuses to save). Class names equal the
engine's ``Classification`` values, so the engine's R1 policy applies unchanged (FCFF
blocked for bank, insurer, specialty_lender, investment_holding; warnings for reit and
asset_manager_broker).

Evidence:

* **SIC** (from SEC submissions) mapped per Blueprint §7.6.1. SIC is evidence, not truth:
  6500–6599 (real-estate operators and developers) always need review.
* **XBRL balance-sheet dominance** at the latest balance-sheet date (latest-reported
  values): deposits / total liabilities (bank), insurance-contract liabilities / total
  liabilities (insurer), loans / total assets without bank deposits (specialty lender),
  investment property / total assets (REIT), investments at fair value / total assets
  (investment holding). A ratio at or above its threshold is *strong* evidence; a
  present concept below it is *weak* (listed, not decisive).

Decision: SIC class and strongest XBRL class agree → that class. They disagree (including
a financial XBRL signal against a non-financial SIC) → ``needs_review``. Only one of them
→ that one (an IFRS filer without a usable SIC relies on XBRL). Neither → non_financial.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from erp_backend.ingestion.sec_parse import RawFact
from erp_backend.ingestion.selection import order_key

CLASSIFIER_VERSION = "1.0.0"
CLASSES = (
    "bank", "insurer", "specialty_lender", "reit", "asset_manager_broker",
    "investment_holding", "non_financial", "needs_review",
)  # fmt: skip
CONFIRMABLE = tuple(c for c in CLASSES if c != "needs_review")

# Blueprint §7.6.1 SIC lists (SEC SIC code titles in comments).
SIC_CLASSES: Mapping[str, frozenset[str]] = {
    # 6021 national / 6022 state / 6029 other commercial banks; 6035/6036 savings institutions
    "bank": frozenset({"6021", "6022", "6029", "6035", "6036"}),
    # 6311 life, 6321 accident & health, 6324 hospital & medical plans, 6331 fire, marine &
    # casualty, 6351 surety, 6361 title, 6399 insurance carriers NEC
    "insurer": frozenset({"6311", "6321", "6324", "6331", "6351", "6361", "6399"}),
    # 6141 personal credit, 6153 short-term business credit, 6159 misc. business credit,
    # 6162 mortgage bankers, 6172 finance lessors, 6199 finance services
    "specialty_lender": frozenset({"6141", "6153", "6159", "6162", "6172", "6199"}),
    "reit": frozenset({"6798"}),  # real estate investment trusts
    # 6200/6211 security brokers & dealers, 6221 commodity brokers, 6282 investment advice,
    # 6411 insurance agents & brokers (not risk-bearing insurers)
    "asset_manager_broker": frozenset({"6200", "6211", "6221", "6282", "6411"}),
    "investment_holding": frozenset({"6770", "6799"}),  # blank checks; investors NEC
}
REVIEW_SIC_RANGE = range(6500, 6600)  # real-estate operators/developers (6798 is separate)

# XBRL dominance rules: class → (numerator concepts, denominator, threshold).
# Within a numerator, a total concept wins over its components (listed after "|").
RULES: Mapping[str, tuple[tuple[tuple[str, ...], ...], str, Decimal]] = {
    "bank": (
        (("Deposits",), ("DepositsFromCustomers", "BalancesOnTermDepositsFromCustomers",
                         "BalancesOnDemandDepositsFromCustomers"), ("DepositsFromBanks",)),
        "Liabilities", Decimal("0.25"),
    ),
    "insurer": (
        (("LiabilityForFuturePolicyBenefits",), ("LiabilityForClaimsAndClaimsAdjustmentExpense",),
         ("InsuranceContractsThatAreLiabilities", "LiabilitiesArisingFromInsuranceContracts")),
        "Liabilities", Decimal("0.25"),
    ),
    "specialty_lender": (
        (("LoansAndLeasesReceivableNetReportedAmount",
          "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss"),
         ("LoansAndAdvancesToCustomers",)),
        "Assets", Decimal("0.5"),
    ),
    "reit": ((("RealEstateInvestmentPropertyNet",), ("InvestmentProperty",)),
             "Assets", Decimal("0.5")),
    "investment_holding": ((("InvestmentOwnedAtFairValue",),), "Assets", Decimal("0.5")),
}  # fmt: skip
PRECEDENCE = ("bank", "insurer", "specialty_lender", "investment_holding", "reit")
TAXONOMIES = ("us-gaap", "ifrs-full")


@dataclass(frozen=True)
class Evidence:
    cls: str
    strength: str  # strong | weak
    ratio: Decimal
    threshold: Decimal
    concepts: Mapping[str, str]  # concept → value used
    denominator: str
    denominator_value: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "class": self.cls,
            "strength": self.strength,
            "ratio": str(self.ratio),
            "threshold": str(self.threshold),
            "concepts": dict(sorted(self.concepts.items())),
            "denominator": [self.denominator, self.denominator_value],
        }


@dataclass(frozen=True)
class Suggestion:
    classification: str
    sic_code: str | None
    sic_class: str | None
    balance_sheet_date: date | None
    evidence: tuple[Evidence, ...]
    rationale: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "classifier_version": CLASSIFIER_VERSION,
            "classification": self.classification,
            "sic": {"code": self.sic_code, "class": self.sic_class},
            "balance_sheet_date": self.balance_sheet_date.isoformat()
            if self.balance_sheet_date
            else None,
            "xbrl_evidence": [e.as_dict() for e in self.evidence],
            "rationale": self.rationale,
            "notes": list(self.notes),
        }


def sic_class(sic: str | None) -> str | None:
    if not sic:
        return None
    for cls, codes in SIC_CLASSES.items():
        if sic in codes:
            return cls
    return None


def _latest_balance_sheet(facts: Iterable[RawFact]) -> tuple[date | None, dict[str, Decimal]]:
    """Latest-reported instant values (per concept) at the latest date with total assets."""
    instants = [f for f in facts if f.period_start is None and f.taxonomy in TAXONOMIES]
    ends = [f.period_end for f in instants if f.concept == "Assets"]
    if not ends:
        return None, {}
    when = max(ends)
    latest: dict[str, RawFact] = {}
    for f in sorted((f for f in instants if f.period_end == when), key=order_key):
        latest[f.concept] = f  # later filings overwrite earlier ones (latest-reported)
    return when, {c: f.value for c, f in latest.items()}


def _evidence(values: Mapping[str, Decimal]) -> list[Evidence]:
    out = []
    for cls, (groups, denominator, threshold) in RULES.items():
        used: dict[str, Decimal] = {}
        for group in groups:  # first present concept of each group counts once
            hit = next((c for c in group if c in values), None)
            if hit is not None:
                used[hit] = values[hit]
        base = values.get(denominator)
        if not used or not base or base <= 0:
            continue
        ratio = (sum(used.values(), Decimal(0)) / base).quantize(Decimal("0.0001"))
        strength = "strong" if ratio >= threshold else "weak"
        concepts = {c: str(v) for c, v in used.items()}
        out.append(Evidence(cls, strength, ratio, threshold, concepts, denominator, str(base)))
    return sorted(out, key=lambda e: (PRECEDENCE.index(e.cls), e.strength))


def suggest(sic: str | None, facts: Iterable[RawFact]) -> Suggestion:
    when, values = _latest_balance_sheet(facts)
    evidence = tuple(_evidence(values))
    strong = [e.cls for e in evidence if e.strength == "strong"]
    weak = [e.cls for e in evidence if e.strength == "weak"]
    by_sic = sic_class(sic)
    xbrl = strong[0] if strong else None
    notes = []
    if len(strong) > 1:
        notes.append(f"also strong: {', '.join(strong[1:])} (precedence {', '.join(PRECEDENCE)})")
    if weak:
        notes.append(f"weak (below threshold): {', '.join(weak)}")
    if not values:
        notes.append("no balance sheet with total assets in the stored facts")

    if sic and sic.isdigit() and int(sic) in REVIEW_SIC_RANGE:
        result, why = "needs_review", f"SIC {sic} (real-estate operators) always needs review"
    elif by_sic and xbrl:
        if by_sic == xbrl:
            result, why = by_sic, f"SIC {sic} and XBRL evidence agree"
        else:
            result, why = "needs_review", f"SIC {sic} suggests {by_sic}, XBRL suggests {xbrl}"
    elif by_sic:
        result, why = by_sic, f"SIC {sic}; no strong XBRL confirmation"
    elif xbrl:
        financial_sic = sic is not None and sic.isdigit() and 6000 <= int(sic) <= 6999
        if sic and not financial_sic:
            result, why = "needs_review", f"XBRL suggests {xbrl} but SIC {sic} is non-financial"
        else:
            result, why = xbrl, f"XBRL evidence ({'no SIC' if not sic else f'SIC {sic}'})"
    else:
        result, why = "non_financial", "no financial SIC and no strong XBRL evidence"
    return Suggestion(result, sic, by_sic, when, evidence, why, tuple(notes))
