"""Accounting integrity checks on a standardized dataset (Blueprint §5.3; D51). Pure.

Every result is ``passed``, ``failed`` or ``not_evaluable`` (an input is missing, ambiguous
or in an unexpected unit). Missing data never passes and values are never altered.

Checks (per period, LTM periods excluded; difference = actual − expected):

* ``BALANCE``: total assets (expected) vs total liabilities + total equity (actual).
* ``SUBTOTAL``: each *reported* (or imported, D57) parent with a ``subtotal`` definition in
  the mapping vs the signed sum of its children (gross profit, SG&A, net income split, short-term
  debt, total equity, total liabilities and equity). A derived parent is not checked:
  it was computed from the same children.
* ``CASH_ROLLFORWARD``: reported net change in cash vs CFO + CFI + CFF + FX effect.
* ``CASH_RECONCILIATION`` (warning; FY only): net change in cash vs the change in balance
  sheet cash from the previous fiscal year-end. Warning because the cash-flow statement
  often includes restricted cash and the balance-sheet line does not.
* ``CROSS_NET_INCOME``: income-statement net income vs the cash-flow starting line.
* ``CROSS_DA``: income-statement D&A vs cash-flow D&A, only where both are reported.
* ``SHARES_DILUTED_GE_BASIC``: diluted weighted shares ≥ basic (1% tolerance).
* ``EPS_BASIC`` / ``EPS_DILUTED`` (warning): EPS × weighted shares vs net income to the
  parent; tolerance max(1% of net income, half a cent × shares) for EPS rounding.
* ``COMPLETENESS`` (dataset level): DCF-required items present as OK values in each of
  the latest ``COMPLETENESS_YEARS`` fiscal years.

Tolerance (balance, subtotal, cash, cross-statement): max(0.1% of the period's total assets
— or of the larger compared value when assets are unavailable — and one unit of reporting
precision, inferred from the inputs' trailing zeros and capped at one million).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from erp_backend.standardization.dataset_types import DatasetValue, decimal_str
from erp_backend.standardization.mapping import MappingSet
from erp_backend.standardization.periods import FiscalPeriod

RELATIVE_TOLERANCE = Decimal("0.001")
SHARES_TOLERANCE = Decimal("0.01")
EPS_HALF_CENT = Decimal("0.005")
MAX_PRECISION_EXPONENT = 6
COMPLETENESS_YEARS = 5
COMPLETENESS_ITEMS = (
    "IS.REVENUE", "IS.EBIT", "IS.PRETAX_INCOME", "IS.INCOME_TAX", "IS.SHARES_DILUTED_WA",
    "CF.CFO", "CF.DA", "CF.CAPEX", "BS.CASH", "BS.TOTAL_ASSETS", "BS.TOTAL_EQUITY",
)  # fmt: skip
CHECK_ORDER = (
    "COMPLETENESS", "BALANCE", "SUBTOTAL", "CASH_ROLLFORWARD", "CASH_RECONCILIATION",
    "CROSS_NET_INCOME", "CROSS_DA", "SHARES_DILUTED_GE_BASIC", "EPS_BASIC", "EPS_DILUTED",
)  # fmt: skip


@dataclass(frozen=True)
class CheckResult:
    check_code: str
    subject: str
    period: FiscalPeriod | None
    severity: str  # error | warning
    status: str  # passed | failed | not_evaluable
    expected: Decimal | None
    actual: Decimal | None
    difference: Decimal | None
    tolerance: Decimal | None
    unit: str | None
    message: str
    inputs: Mapping[str, Any] = field(default_factory=dict)  # JSON-native values only

    def sort_key(self) -> tuple[Any, ...]:
        period = self.period.sort_key() if self.period else (0, -1, date.min, date.min)
        return (CHECK_ORDER.index(self.check_code), period, self.subject)

    def as_dict(self) -> dict[str, Any]:
        def num(x: Decimal | None) -> str | None:
            return None if x is None else decimal_str(x)

        return {
            "check": self.check_code,
            "subject": self.subject,
            "period": None
            if self.period is None
            else [self.period.period_type, self.period.start.isoformat(),
                  self.period.end.isoformat()],
            "severity": self.severity,
            "status": self.status,
            "expected": num(self.expected),
            "actual": num(self.actual),
            "difference": num(self.difference),
            "tolerance": num(self.tolerance),
            "unit": self.unit,
            "message": self.message,
            "inputs": dict(self.inputs),
        }  # fmt: skip


def canonical_bytes(results: Iterable[CheckResult]) -> bytes:
    doc = [r.as_dict() for r in sorted(results, key=CheckResult.sort_key)]
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(results: Iterable[CheckResult]) -> str:
    return hashlib.sha256(canonical_bytes(results)).hexdigest()


def summarize(results: Iterable[CheckResult]) -> dict[str, Any]:
    out: dict[str, dict[str, int]] = {}
    for r in results:
        row = out.setdefault(r.check_code, {"passed": 0, "failed": 0, "not_evaluable": 0})
        row[r.status] += 1
    errors = sum(
        v["failed"] for k, v in out.items() if k not in ("CASH_RECONCILIATION", "EPS_DILUTED")
    )
    return {"by_check": dict(sorted(out.items())), "failed_errors": errors}


def _describe(v: DatasetValue | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {
        "value": None if v.value is None else decimal_str(v.value),
        "status": v.status,
        "provenance": v.provenance,
        "concept": v.concept,
        "unit": v.unit,
        "period": [v.period.period_type, v.period.start.isoformat(), v.period.end.isoformat()],
        "fact_ids": list(v.source_fact_ids),
    }


def _precision(values: Iterable[Decimal]) -> Decimal:
    exponents = [
        min(int(x.normalize().as_tuple().exponent), MAX_PRECISION_EXPONENT)
        for x in values
        if x != 0
    ]
    return Decimal(1).scaleb(min(exponents)) if exponents else Decimal(1)


class _Checker:
    def __init__(self, values: Iterable[DatasetValue], mapping: MappingSet, currency: str) -> None:
        self.mapping = mapping
        self.currency = currency
        self.by_key: dict[tuple[FiscalPeriod, str], DatasetValue] = {}
        self.fy_by_end: dict[Any, FiscalPeriod] = {}
        for v in values:
            if v.period.period_type == "LTM":
                continue
            self.by_key[(v.period, v.line_item)] = v
            if v.period.period_type == "FY":
                self.fy_by_end[v.period.end] = v.period
        self.periods = sorted({p for p, _ in self.by_key}, key=FiscalPeriod.sort_key)
        self.results: list[CheckResult] = []

    def get(self, period: FiscalPeriod, code: str) -> DatasetValue | None:
        return self.by_key.get((period, code))

    def unit_for(self, code: str) -> str:
        vt = self.mapping.line_items[code].value_type
        if vt == "per_share":
            return f"{self.currency}/shares"
        return "shares" if vt == "count" else self.currency

    def _blocked(self, inputs: Mapping[str, DatasetValue | None]) -> str | None:
        missing = sorted(k for k, v in inputs.items() if v is None)
        if missing:
            return "missing input: " + ", ".join(missing)
        ambiguous = sorted(k for k, v in inputs.items() if v is not None and v.status != "ok")
        if ambiguous:
            return "ambiguous input: " + ", ".join(ambiguous)
        wrong = sorted(
            k for k, v in inputs.items() if v is not None and v.unit != self.unit_for(_code(k))
        )
        if wrong:
            return "unexpected unit: " + ", ".join(wrong)
        return None

    def compare(
        self,
        check: str,
        subject: str,
        period: FiscalPeriod | None,
        inputs: Mapping[str, DatasetValue | None],
        expected_terms: list[tuple[int, str]],
        actual_terms: list[tuple[int, str]],
        *,
        severity: str = "error",
        tolerance_basis: str = "assets",
    ) -> None:
        described = {k: _describe(v) for k, v in sorted(inputs.items())}
        blocked = self._blocked(inputs)
        unit = self.currency
        if blocked:
            self.results.append(
                CheckResult(check, subject, period, severity, "not_evaluable", None, None, None,
                            None, unit, blocked, {"values": described})
            )  # fmt: skip
            return

        def total(terms: list[tuple[int, str]]) -> Decimal:
            out = Decimal(0)
            for sign, key in terms:
                value = inputs[key]
                assert value is not None and value.value is not None
                out += sign * value.value
            return out

        expected, actual = total(expected_terms), total(actual_terms)
        difference = actual - expected
        precision = _precision(
            v.value for v in inputs.values() if v is not None and v.value is not None
        )
        assets = self.get(period, "BS.TOTAL_ASSETS") if period is not None else None
        if tolerance_basis == "assets" and assets is not None and assets.status == "ok":
            assert assets.value is not None
            base, basis = abs(assets.value), "0.1% of total assets"
        else:
            base, basis = max(abs(expected), abs(actual)), "0.1% of the larger compared value"
        tolerance = max(base * RELATIVE_TOLERANCE, precision)
        status = "passed" if abs(difference) <= tolerance else "failed"
        self.results.append(
            CheckResult(
                check, subject, period, severity, status, expected, actual, difference, tolerance,
                unit, f"{status}: |difference| {'≤' if status == 'passed' else '>'} tolerance "
                f"({basis}; reporting precision {decimal_str(precision)})",
                {"values": described, "expected": _terms(expected_terms),
                 "actual": _terms(actual_terms)},
            )
        )  # fmt: skip

    # ---- individual checks ---------------------------------------------------------------

    def balance(self, p: FiscalPeriod) -> None:
        codes = ("BS.TOTAL_ASSETS", "BS.TOTAL_LIABILITIES", "BS.TOTAL_EQUITY")
        inputs = {c: self.get(p, c) for c in codes}
        if any(inputs.values()):
            self.compare(
                "BALANCE", "BS", p, inputs, [(1, "BS.TOTAL_ASSETS")],
                [(1, "BS.TOTAL_LIABILITIES"), (1, "BS.TOTAL_EQUITY")],
            )  # fmt: skip

    def subtotals(self, p: FiscalPeriod) -> None:
        for item in self.mapping.line_items.values():
            if not item.subtotal or item.derived_only:
                continue
            parent = self.get(p, item.code)
            # Reported or imported parents only: a derived parent would be compared with
            # itself. (Analyst-entered parents keep D51's behaviour: not checked.)
            if parent is None or parent.provenance not in ("reported", "manual_import"):
                continue
            check = "CASH_ROLLFORWARD" if item.code == "CF.NET_CHANGE_CASH" else "SUBTOTAL"
            inputs = {item.code: parent} | {c: self.get(p, c) for _, c in item.subtotal}
            self.compare(check, item.code, p, inputs, [(1, item.code)], list(item.subtotal))

    def cash_reconciliation(self, p: FiscalPeriod) -> None:
        change = self.get(p, "CF.NET_CHANGE_CASH")
        if p.period_type != "FY" or change is None:
            return
        prior = self.fy_by_end.get(p.start - timedelta(days=1))
        inputs = {
            "CF.NET_CHANGE_CASH": change,
            "BS.CASH": self.get(p, "BS.CASH"),
            "BS.CASH@prior": self.get(prior, "BS.CASH") if prior else None,
        }
        self.compare(
            "CASH_RECONCILIATION", "BS.CASH", p, inputs, [(1, "CF.NET_CHANGE_CASH")],
            [(1, "BS.CASH"), (-1, "BS.CASH@prior")], severity="warning",
        )  # fmt: skip

    def cross_statement(self, p: FiscalPeriod) -> None:
        ni = {c: self.get(p, c) for c in ("IS.NET_INCOME", "CF.NET_INCOME")}
        if any(ni.values()):
            self.compare(
                "CROSS_NET_INCOME", "IS.NET_INCOME", p, ni, [(1, "IS.NET_INCOME")],
                [(1, "CF.NET_INCOME")],
            )  # fmt: skip
        da = {c: self.get(p, c) for c in ("IS.DA", "CF.DA")}
        if all(da.values()):  # Blueprint §5.3: only where both are reported
            self.compare("CROSS_DA", "IS.DA", p, da, [(1, "IS.DA")], [(1, "CF.DA")])

    def shares(self, p: FiscalPeriod) -> None:
        basic, diluted = self.get(p, "IS.SHARES_BASIC_WA"), self.get(p, "IS.SHARES_DILUTED_WA")
        inputs = {"IS.SHARES_BASIC_WA": basic, "IS.SHARES_DILUTED_WA": diluted}
        if basic is None and diluted is None:
            return
        described = {k: _describe(v) for k, v in sorted(inputs.items())}
        blocked = self._blocked(inputs)
        if blocked:
            self.results.append(
                CheckResult("SHARES_DILUTED_GE_BASIC", "IS.SHARES_DILUTED_WA", p, "error",
                            "not_evaluable", None, None, None, None, "shares", blocked,
                            {"values": described})
            )  # fmt: skip
            return
        assert basic is not None and diluted is not None
        assert basic.value is not None and diluted.value is not None
        tolerance = abs(basic.value) * SHARES_TOLERANCE
        difference = diluted.value - basic.value
        status = "passed" if difference >= -tolerance else "failed"
        self.results.append(
            CheckResult("SHARES_DILUTED_GE_BASIC", "IS.SHARES_DILUTED_WA", p, "error", status,
                        basic.value, diluted.value, difference, tolerance, "shares",
                        f"{status}: diluted {'≥' if status == 'passed' else '<'} basic − 1%",
                        {"values": described})
        )  # fmt: skip

    def eps(self, p: FiscalPeriod, kind: str) -> None:
        eps_code, shares_code = f"IS.EPS_{kind}", f"IS.SHARES_{kind}_WA"
        inputs = {
            eps_code: self.get(p, eps_code),
            shares_code: self.get(p, shares_code),
            "IS.NET_INCOME_COMMON": self.get(p, "IS.NET_INCOME_COMMON"),
        }
        if inputs[eps_code] is None:
            return
        check, severity = f"EPS_{kind}", "error" if kind == "BASIC" else "warning"
        described = {k: _describe(v) for k, v in sorted(inputs.items())}
        blocked = self._blocked(inputs)
        if blocked:
            self.results.append(
                CheckResult(check, eps_code, p, severity, "not_evaluable", None, None, None, None,
                            self.currency, blocked, {"values": described})
            )  # fmt: skip
            return
        eps_v, shares_v, ni_v = (
            inputs[eps_code],
            inputs[shares_code],
            inputs["IS.NET_INCOME_COMMON"],
        )
        assert eps_v is not None and shares_v is not None and ni_v is not None
        eps, shares, ni = eps_v.value, shares_v.value, ni_v.value
        assert eps is not None and shares is not None and ni is not None
        actual = eps * shares
        tolerance = max(abs(ni) * SHARES_TOLERANCE, abs(shares) * EPS_HALF_CENT)
        difference = actual - ni
        status = "passed" if abs(difference) <= tolerance else "failed"
        self.results.append(
            CheckResult(check, eps_code, p, severity, status, ni, actual, difference, tolerance,
                        self.currency,
                        f"{status}: EPS × shares vs net income to parent "
                        "(tolerance max(1% of net income, half a cent per share))",
                        {"values": described})
        )  # fmt: skip

    def completeness(self) -> None:
        years = sorted((p for p in self.periods if p.period_type == "FY"), key=lambda p: p.end)
        latest = years[-COMPLETENESS_YEARS:]
        for code in COMPLETENESS_ITEMS:
            present = [p for p in latest if (v := self.get(p, code)) and v.status == "ok"]
            missing = [p.fiscal_year for p in latest if p not in present]
            short = len(latest) < COMPLETENESS_YEARS
            status = "passed" if not missing and not short else "failed"
            message = (
                f"{status}: {len(present)} of the latest {COMPLETENESS_YEARS} fiscal years"
                + (f" (dataset has only {len(latest)})" if short else "")
            )
            self.results.append(
                CheckResult("COMPLETENESS", code, None, "error", status,
                            Decimal(COMPLETENESS_YEARS), Decimal(len(present)),
                            Decimal(len(present) - COMPLETENESS_YEARS), Decimal(0), "years",
                            message, {"fiscal_years": [p.fiscal_year for p in latest],
                                      "missing_years": missing})
            )  # fmt: skip


def _code(key: str) -> str:
    return key.split("@", 1)[0]


def _terms(terms: list[tuple[int, str]]) -> list[str]:
    return [f"{'+' if s > 0 else '-'}{k}" for s, k in terms]


def validate(
    values: Iterable[DatasetValue], mapping: MappingSet, currency: str
) -> list[CheckResult]:
    checker = _Checker(values, mapping, currency)
    checker.completeness()
    for p in checker.periods:
        checker.balance(p)
        checker.subtotals(p)
        checker.cash_reconciliation(p)
        checker.cross_statement(p)
        checker.shares(p)
        checker.eps(p, "BASIC")
        checker.eps(p, "DILUTED")
    return sorted(checker.results, key=CheckResult.sort_key)
