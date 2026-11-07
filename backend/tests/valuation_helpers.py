"""Controlled test data for the valuation workflow (M3 phase 5).

Datasets are built through the real financial-statement import path (D57) and locked with the
real ``lock_dataset`` service, inside the test's rolled-back transaction on ``erp_test``. The
figures reproduce methodology benchmark B1/B3 at the balance-sheet date (FY2024):
revenue 1,000; operating NWC 100 (receivables 60 + inventory 70 − payables 30); cash 150;
debt 400 (0 short-term + 400 long-term); 100 shares; no preferred, NCI or investments.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from erp_backend.datasets import lock_dataset
from erp_backend.models import Company, FinancialDataset, FiscalPeriodRow, Security
from erp_backend.statements import template as tpl
from erp_backend.statements.file_import import ingest
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2] / "mappings"
TODAY = date(2025, 6, 30)
REASON = "Imported annual report figures verified against the printed statements."
FY23 = ("FY2023", "FY", "2023", "2023-01-01", "2023-12-31")
FY24 = ("FY2024", "FY", "2024", "2024-01-01", "2024-12-31")
B = date(2024, 12, 31)

B1_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("IS.REVENUE", "900", "1000"),
    ("IS.EBIT", "180", "200"),
    ("IS.PRETAX_INCOME", "170", "190"),
    ("IS.INCOME_TAX", "42.5", "47.5"),
    ("CF.DA", "45", "50"),
    ("CF.CAPEX", "-60", "-70"),
    ("BS.RECEIVABLES", "55", "60"),
    ("BS.INVENTORY", "65", "70"),
    ("BS.PAYABLES", "28", "30"),
    ("BS.CASH", "140", "150"),
    ("BS.ST_INVESTMENTS", "0", "0"),
    ("BS.ST_DEBT", "0", "0"),
    ("BS.LT_DEBT", "380", "400"),
    ("BS.PREFERRED_EQUITY", "0", "0"),
    ("BS.NCI", "0", "0"),
    ("BS.TOTAL_ASSETS", "2000", "2200"),
    ("BS.TOTAL_LIABILITIES", "1200", "1300"),
    ("BS.TOTAL_EQUITY", "800", "900"),
    ("BS.SHARES_OUTSTANDING", "100", "100"),
)


@dataclass(frozen=True)
class Fixture:
    company: Company
    listing: Security
    dataset: FinancialDataset
    period: FiscalPeriodRow  # FY2024 (balance-sheet date B)


def statement_csv(
    items: tuple[tuple[str, str, str], ...], *, ticker: str, currency: str = "USD"
) -> bytes:
    meta = {
        "template": "financial_statements.v1",
        "issuer_ticker": ticker,
        "issuer_mic": "XNYS",
        "currency": currency,
        "scale": "units",
        "share_scale": "units",
        "accounting_standard": "US_GAAP",
        "source_document": "Synthetic valuation test statements (controlled test data)",
    }
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["item", FY23[0], FY24[0]])
    for k, v in meta.items():
        w.writerow([k, v])
    for i, key in enumerate(tpl.PERIOD_KEYS):
        w.writerow([key, FY23[i + 1], FY24[i + 1]])
    for item in items:
        w.writerow(item)
    return out.getvalue().encode()


def make_company(
    db: Session,
    *,
    ticker: str = "VLT",
    classification: str | None = "non_financial",
    currency: str = "USD",
    trading_currency: str = "USD",
) -> tuple[Company, Security]:
    company = Company(
        legal_name=f"{ticker} Valuation Test Co",
        reporting_currency=currency,
        accounting_standard="US_GAAP",
        accounting_standard_inferred=False,
        financial_classification=classification,
    )
    db.add(company)
    db.flush()
    listing = Security(
        company_id=company.id,
        ticker=ticker,
        exchange_name="New York Stock Exchange",
        exchange_mic="XNYS",
        trading_currency=trading_currency,
    )
    db.add(listing)
    db.flush()
    return company, listing


def build_dataset(
    db: Session,
    company: Company,
    listing: Security,
    raw_root: Path,
    *,
    items: tuple[tuple[str, str, str], ...] = B1_ITEMS,
    lock: bool = True,
) -> FinancialDataset:
    data = statement_csv(items, ticker=listing.ticker, currency=company.reporting_currency or "USD")
    result = ingest(
        db,
        filename="statements.csv",
        data=data,
        source_name="Analyst transcription (valuation tests)",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        raw_root=raw_root,
        mappings_root=ROOT,
        default_mapping_version="v2",
        today=TODAY,
    )
    assert result.dataset is not None, result.report
    dataset = result.dataset.dataset
    if lock:
        dataset = lock_dataset(db, dataset.id, mappings_root=ROOT, reason=REASON)
    db.flush()
    return dataset


def fy_period(db: Session, company_id: uuid.UUID, end: date = B) -> FiscalPeriodRow:
    return db.scalars(
        select(FiscalPeriodRow).where(
            FiscalPeriodRow.company_id == company_id,
            FiscalPeriodRow.period_type == "FY",
            FiscalPeriodRow.end_date == end,
        )
    ).one()


def setup(
    db: Session,
    raw_root: Path,
    *,
    ticker: str = "VLT",
    classification: str | None = "non_financial",
    lock: bool = True,
    items: tuple[tuple[str, str, str], ...] = B1_ITEMS,
) -> Fixture:
    company, listing = make_company(db, ticker=ticker, classification=classification)
    dataset = build_dataset(db, company, listing, raw_root, items=items, lock=lock)
    return Fixture(company, listing, dataset, fy_period(db, company.id))


# B1 / B3 assumptions (fractions; methodology docs/methodology/dcf.md §14).
B1_YEARS = 3


def b1_assumptions(
    *, terminal_form: str = "simple", status: str = "approved"
) -> list[dict[str, Any]]:
    """Assumption changes reproducing B1 (FCFF, WACC 10% built as Rf 4% + 1.2 × 5%, target
    D/E 0 so WACC = Ke = 10%). Values are decimal strings as the API takes them."""
    analyst = {"type": "analyst", "reference": "Benchmark B1 (methodology §14)"}
    rf_source = {**analyst, "as_of": "2024-12-31", "currency": "USD"}
    changes: list[dict[str, Any]] = [
        {"key": "horizon_years", "value": "3"},
        {"key": "convention", "choice": "end_of_period"},
        {"key": "lease_treatment", "choice": "none"},
        {"key": "terminal_form", "choice": terminal_form},
        {"key": "terminal_growth", "value": "0.02"},
        {"key": "risk_free_rate", "value": "0.04", "source": rf_source},
        {"key": "erp_basis", "choice": "mature_plus_crp"},
        {"key": "mature_market_erp", "value": "0.05"},
        {"key": "beta_method", "choice": "analyst_levered"},
        {"key": "beta_levered", "value": "1.2"},
        {"key": "marginal_tax_rate", "value": "0.25"},
        {"key": "cost_of_debt_method", "choice": "direct"},
        {"key": "pre_tax_cost_of_debt", "value": "0.06"},
        {"key": "capital_structure_basis", "choice": "target"},
        {"key": "target_debt_to_equity", "value": "0"},
    ]
    if terminal_form == "value_driver":
        changes.append({"key": "ronic", "value": "0.10"})
    for k, g in enumerate(("0.10", "0.08", "0.06"), start=1):
        for key, value in (
            ("revenue_growth", g),
            ("ebit_margin", "0.20"),
            ("tax_rate", "0.25"),
            ("da_pct", "0.05"),
            ("capex_pct", "0.07"),
            ("nwc_pct", "0.10"),
        ):
            changes.append({"key": key, "period_index": k, "value": value})
    out = []
    for c in changes:
        c.setdefault("source", analyst)
        c["status"] = status
        out.append(c)
    return out


def b3_assumptions(status: str = "approved") -> list[dict[str, Any]]:
    """B1 operating drivers + B3 financing: Ke 12% (Rf 4% + 1.6 × 5%), Kd 6%, cash yield 3%,
    debt 450 / 500 / 510, cash constant at 150."""
    changes = [
        c
        for c in b1_assumptions(status=status)
        if c["key"]
        not in {
            "beta_levered",
            "lease_treatment",
            "capital_structure_basis",
            "target_debt_to_equity",
        }
    ]
    analyst = {"type": "analyst", "reference": "Benchmark B3 (methodology §14)"}
    changes.append({"key": "beta_levered", "value": "1.6", "source": analyst, "status": status})
    changes.append({"key": "cash_yield", "value": "0.03", "source": analyst, "status": status})
    for k, (debt, cash) in enumerate((("450", "150"), ("500", "150"), ("510", "150")), start=1):
        changes.append(
            {
                "key": "debt_forecast",
                "period_index": k,
                "value": debt,
                "source": analyst,
                "status": status,
            }
        )
        changes.append(
            {
                "key": "cash_forecast",
                "period_index": k,
                "value": cash,
                "source": analyst,
                "status": status,
            }
        )
    return changes


B1_EV = Decimal("1858.818182")
B1_PRICE = Decimal("16.088182")
B1_VD_PRICE = Decimal("15.564694")
B3_EQUITY = Decimal("1468.334662")
B3_PRICE = Decimal("14.683347")


# ----------------------------------------------------------------------- market & reference data


def rate_observation(
    db: Session, raw_root: Path, *, value: str, obs_date: date = B, series: str = "UST_PAR_10Y"
) -> Any:
    """Store one rate observation (percentage points) with an archived synthetic fetch, as the
    rates store does (D58). Controlled test data on ``erp_test`` only."""
    from erp_backend.ingestion import archive
    from erp_backend.models import ProviderFetch, RateObservationRow, RateSeries

    provider = db.get(RateSeries, series).provider_code  # type: ignore[union-attr]
    stored = archive.store(raw_root, provider, f"{series} {obs_date} {value}".encode())
    fetch = ProviderFetch(
        provider_code=provider,
        url="https://example.invalid/synthetic",
        http_status=200,
        content_sha256=stored.sha256,
        payload_path=stored.relative_path,
        bytes=stored.size,
        stored_bytes=stored.stored_size,
    )
    db.add(fetch)
    db.flush()
    row = RateObservationRow(
        series_code=series,
        provider_code=provider,
        obs_date=obs_date,
        value=Decimal(value),
        fetch_id=fetch.id,
    )
    db.add(row)
    db.flush()
    return row


def reference_dataset(
    db: Session, raw_root: Path, *, template: str, data: bytes, edition: date
) -> uuid.UUID:
    """Ingest a Damodaran-format CSV through the real reference import (D58)."""
    from erp_backend.reference.damodaran import ingest as ref_ingest

    result = ref_ingest(
        db,
        filename="data.csv",
        data=data,
        template=template,
        edition_date=edition,
        source_name="Synthetic Damodaran-format test file",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        raw_root=raw_root,
        today=edition,
    )
    assert result.dataset_id is not None
    return result.dataset_id


ERP_CSV = (
    b"as_of_date,index,implied_erp,t_bond_rate,method\n"
    b"2024-12-01,SP500,5.00%,4.20%,Trailing 12-month cash yield\n"
)
