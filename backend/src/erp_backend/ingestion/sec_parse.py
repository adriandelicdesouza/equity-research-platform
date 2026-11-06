"""Pure parsers for SEC EDGAR JSON payloads (no I/O).

Numbers are parsed as ``Decimal`` (never float) so reported values are stored exactly.
Malformed payloads raise :class:`SecParseError`; the raw payload is already archived.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

CANADIAN_CODES = {"A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "B0", "Z4"}
US_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "PR",
}  # fmt: skip
EXCHANGE_MIC = {
    "Nasdaq": "XNAS",
    "NYSE": "XNYS",
    "NYSE American": "XASE",
    "NYSE Arca": "ARCX",
    "CBOE": "XCBO",
    "OTC": "OTCM",
}


class SecParseError(Exception):
    pass


def _json(payload: bytes) -> Any:
    try:
        return json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SecParseError(f"Invalid JSON: {exc}") from exc


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise SecParseError(f"Invalid date {value!r}") from exc


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SecParseError(f"Invalid integer {value!r}") from exc


@dataclass(frozen=True)
class TickerRow:
    cik: int
    name: str
    ticker: str
    exchange: str | None


def parse_ticker_map(payload: bytes) -> list[TickerRow]:
    doc = _json(payload)
    try:
        fields = list(doc["fields"])
        idx = {k: fields.index(k) for k in ("cik", "name", "ticker", "exchange")}
        rows = doc["data"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SecParseError("Ticker map lacks fields/data") from exc
    out = []
    for r in rows:
        ticker = str(r[idx["ticker"]] or "").strip().upper()
        if not ticker:
            continue
        exchange = r[idx["exchange"]]
        out.append(
            TickerRow(
                _int(r[idx["cik"]]),
                str(r[idx["name"]]),
                ticker,
                str(exchange) if exchange else None,
            )
        )
    if not out:
        raise SecParseError("Ticker map is empty")
    return out


@dataclass(frozen=True)
class FilingRow:
    accession_no: str
    form: str
    filed_date: date
    period_of_report: date | None
    primary_document: str | None


@dataclass(frozen=True)
class Submission:
    cik: int
    name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    sic: str | None
    sic_description: str | None
    fiscal_year_end_mmdd: str | None
    state_of_incorporation: str | None
    country: str | None
    filings: tuple[FilingRow, ...]


def _country(code: str | None) -> str | None:
    if not code:
        return None
    return "US" if code in US_CODES else "CA" if code in CANADIAN_CODES else None


def parse_submissions(payload: bytes) -> Submission:
    doc = _json(payload)
    try:
        recent = doc["filings"]["recent"]
        cols = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
        n = len(recent["accessionNumber"])
        filings = tuple(
            FilingRow(
                str(recent["accessionNumber"][i]),
                str(recent["form"][i]),
                _date(recent["filingDate"][i]) or date.min,
                _date(recent["reportDate"][i]) if "reportDate" in recent else None,
                (str(recent["primaryDocument"][i]) or None)
                if "primaryDocument" in recent
                else None,
            )
            for i in range(n)
            if all(c in recent for c in cols[:3])
        )
        business = (doc.get("addresses") or {}).get("business") or {}
        fye = doc.get("fiscalYearEnd")
        return Submission(
            cik=_int(doc["cik"]),
            name=str(doc["name"]),
            tickers=tuple(str(t).upper() for t in doc.get("tickers") or ()),
            exchanges=tuple(str(e) if e else "" for e in doc.get("exchanges") or ()),
            sic=str(doc["sic"]) if doc.get("sic") else None,
            sic_description=doc.get("sicDescription") or None,
            fiscal_year_end_mmdd=str(fye) if fye and len(str(fye)) == 4 else None,
            state_of_incorporation=doc.get("stateOfIncorporation") or None,
            # Business address first; fall back to the incorporation code when absent.
            country=_country(business.get("stateOrCountry"))
            or _country(doc.get("stateOfIncorporation")),
            filings=filings,
        )
    except (KeyError, TypeError) as exc:
        raise SecParseError(f"Submissions payload missing field: {exc}") from exc


@dataclass(frozen=True)
class RawFact:
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    filed_date: date
    accession_no: str
    frame: str | None

    @property
    def duration_days(self) -> int | None:
        if self.period_start is None:
            return None
        return (self.period_end - self.period_start).days + 1


def parse_companyfacts(payload: bytes) -> tuple[int, list[RawFact]]:
    doc = _json(payload)
    try:
        cik = _int(doc["cik"])
        taxonomies = doc["facts"]
    except (KeyError, TypeError) as exc:
        raise SecParseError("companyfacts payload lacks cik/facts") from exc
    facts: list[RawFact] = []
    for taxonomy, concepts in taxonomies.items():
        for concept, body in concepts.items():
            for unit, observations in (body.get("units") or {}).items():
                for o in observations:
                    end = _date(o.get("end"))
                    filed = _date(o.get("filed"))
                    if end is None or filed is None or "accn" not in o or "val" not in o:
                        raise SecParseError(f"Incomplete observation for {taxonomy}:{concept}")
                    value = o["val"]
                    if not isinstance(value, Decimal):
                        raise SecParseError(f"Non-numeric value for {taxonomy}:{concept}")
                    fy = o.get("fy")
                    facts.append(
                        RawFact(
                            taxonomy=str(taxonomy),
                            concept=str(concept),
                            unit=str(unit),
                            value=value,
                            period_start=_date(o.get("start")),
                            period_end=end,
                            fiscal_year=int(fy) if fy is not None else None,
                            fiscal_period=o.get("fp"),
                            form=str(o.get("form") or ""),
                            filed_date=filed,
                            accession_no=str(o["accn"]),
                            frame=o.get("frame"),
                        )
                    )
    return cik, facts
