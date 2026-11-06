"""Builders for SYNTHETIC SEC-format payloads used only in automated tests.

Shapes follow the public SEC JSON formats (company_tickers_exchange, submissions,
companyfacts). Companies, CIKs and values here are fictitious test data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

ACME_CIK = 9_990_001
MAPLE_CIK = 9_990_002  # IFRS, Canadian, 40-F filer


def ticker_map(rows: list[tuple[int, str, str, str | None]]) -> bytes:
    return json.dumps(
        {"fields": ["cik", "name", "ticker", "exchange"], "data": [list(r) for r in rows]}
    ).encode()


def submissions(
    cik: int,
    name: str,
    tickers: list[str],
    exchanges: list[str],
    *,
    sic: str = "3571",
    fye: str = "1231",
    state: str = "DE",
    filings: list[tuple[str, str, str, str]] | None = None,
) -> bytes:
    filings = filings or [("0009990001-24-000002", "10-K", "2024-02-01", "2023-12-31")]
    return json.dumps(
        {
            "cik": str(cik),
            "name": name,
            "tickers": tickers,
            "exchanges": exchanges,
            "sic": sic,
            "sicDescription": "Test industry",
            "fiscalYearEnd": fye,
            "stateOfIncorporation": state,
            "addresses": {"business": {"stateOrCountry": state}},
            "filings": {
                "recent": {
                    "accessionNumber": [f[0] for f in filings],
                    "form": [f[1] for f in filings],
                    "filingDate": [f[2] for f in filings],
                    "reportDate": [f[3] for f in filings],
                    "primaryDocument": [f"doc{i}.htm" for i in range(len(filings))],
                }
            },
        }
    ).encode()


def obs(
    val: Any,
    end: str,
    *,
    start: str | None = None,
    accn: str,
    form: str,
    filed: str,
    fy: int | None = None,
    fp: str | None = None,
) -> dict[str, Any]:
    o: dict[str, Any] = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        o["start"] = start
    if fy is not None:
        o["fy"], o["fp"] = fy, fp
    return o


def companyfacts(cik: int, facts: dict[str, dict[str, dict[str, list[dict[str, Any]]]]]) -> bytes:
    """facts: taxonomy -> concept -> unit -> observations."""
    body = {
        tax: {c: {"label": c, "units": units} for c, units in concepts.items()}
        for tax, concepts in facts.items()
    }
    return json.dumps({"cik": cik, "entityName": "Test", "facts": body}).encode()


def quarter_ends(last: date, n: int) -> list[tuple[date, date]]:
    """n consecutive (start, end) quarters ending at ``last`` (~91-day quarters)."""
    out = []
    end = last
    for _ in range(n):
        start = end - timedelta(days=90)
        out.append((start, end))
        end = start - timedelta(days=1)
    return list(reversed(out))


def interim_facts(
    quarters: list[tuple[date, date]],
    *,
    tax: str = "us-gaap",
    form: str = "10-Q",
    unit: str = "USD",
    skip: dict[int, set[str]] | None = None,
) -> dict[str, dict[str, dict[str, list[dict[str, Any]]]]]:
    names = {
        "us-gaap": (
            "Revenues",
            "OperatingIncomeLoss",
            "NetIncomeLoss",
            "CashAndCashEquivalentsAtCarryingValue",
            "Assets",
            "NetCashProvidedByUsedInOperatingActivities",
        ),
        "ifrs-full": (
            "Revenue",
            "ProfitLossFromOperatingActivities",
            "ProfitLoss",
            "CashAndCashEquivalents",
            "Assets",
            "CashFlowsFromUsedInOperatingActivities",
        ),
    }[tax]
    items = ("revenue", "operating_income", "net_income", "cash", "total_assets", "cfo")
    skip = skip or {}
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for i, (start, end) in enumerate(quarters):
        accn = f"0009990001-{end.year % 100:02d}-{i + 100:06d}"
        filed = (end + timedelta(days=35)).isoformat()
        for item, concept in zip(items, names, strict=True):
            if item in skip.get(i, set()):
                continue
            instant = item in {"cash", "total_assets"}
            o = obs(
                1000 + i,
                end.isoformat(),
                start=None if instant else start.isoformat(),
                accn=accn,
                form=form,
                filed=filed,
            )
            out.setdefault(concept, {}).setdefault(unit, []).append(o)
    return {tax: out}
