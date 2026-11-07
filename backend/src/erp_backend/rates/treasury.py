"""US Treasury daily par yield curve adapter (Blueprint §0.4, §4.2, §7.5.1; D58).

Network access is **gated**: :func:`treasury_client` refuses unless the ``us_treasury``
provider is enabled in ``source.providers`` (seeded disabled; the first live request needs
the owner's approval, P9). Nothing here enables it. Parsing works offline on archived or
fixture payloads only.

Payload: the Treasury "Daily Treasury Par Yield Curve Rates" CSV for one calendar year
(§0.4 probe, 2026-11-06: HTTP 200)::

    Date,"1 Mo","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr",...,"10 Yr","20 Yr","30 Yr"
    11/05/2026,4.12,4.10,...

* ``Date`` is ``MM/DD/YYYY`` (US order; anything else is rejected, never guessed).
* Each tenor column is its own series (``UST_PAR_<tenor>``, :mod:`erp_backend.rates.model`).
  Columns appear and disappear over the years (e.g. 2 Mo from 2018, 4 Mo from 2022), so a
  column may be absent and a cell may be blank: both are *missing*, reported, not stored.
* A column that is not in the registry is reported (``unregistered_column``) and ignored —
  a new Treasury tenor is added deliberately, never mapped by guesswork.
* Values are percentage points as published. ``N/A`` and blanks are missing.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from erp_backend.ingestion.http import ProviderClient, gated_client
from erp_backend.rates.model import (
    Issue,
    IssueLevel,
    ParsedSeries,
    ParseError,
    RateObservation,
    check_date,
    check_rate,
    dedupe,
    parse_published_decimal,
    rate_series_for,
)

CODE = "us_treasury"
BASE = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
)
RATE_PER_SECOND = 1.0  # source.providers seed (0003)
USER_AGENT = "equity-research-platform/0.1 (+self-hosted research tool)"
FIRST_YEAR = 1990  # the par yield curve series starts in 1990
MISSING_TOKENS = frozenset({"", "N/A", "NA", "ND"})
_US_DATE = re.compile(r"^([0-9]{2})/([0-9]{2})/([0-9]{4})$")


def year_url(year: int, today: date | None = None) -> tuple[str, dict[str, Any]]:
    current = (today or datetime.now(UTC).date()).year
    if not FIRST_YEAR <= year <= current:
        raise ValueError(f"year must be between {FIRST_YEAR} and {current}")
    params = {
        "type": "daily_treasury_yield_curve",
        "field_tdr_date_value": str(year),
        "_format": "csv",
    }
    return BASE.format(year=year), params


def treasury_client(db: Session, transport: httpx.BaseTransport | None = None) -> ProviderClient:
    """The only way to obtain a Treasury client; refuses while the provider is disabled."""
    return gated_client(db, CODE, USER_AGENT, RATE_PER_SECOND, transport)


def _us_date(text: str) -> date:
    m = _US_DATE.fullmatch(text.strip())
    if not m:
        raise ValueError(f"{text!r} is not MM/DD/YYYY")
    month, day, year = (int(g) for g in m.groups())
    return date(year, month, day)


def parse_par_yield_csv(payload: bytes, today: date | None = None) -> ParsedSeries:
    today = today or datetime.now(UTC).date()
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(f"Payload is not UTF-8 ({exc.reason})") from exc
    stripped = text.lstrip()
    if stripped.startswith(("<", "{")):
        raise ParseError("Payload is not a CSV (HTML or JSON response)")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ParseError(f"Malformed CSV: {exc}") from exc
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ParseError("Empty payload")
    header = [h.strip() for h in rows[0]]
    if not header or header[0] != "Date":
        raise ParseError(f"First column must be 'Date', got {header[:1]!r}")
    if len(set(header)) != len(header):
        raise ParseError("Duplicate column names in the header")

    registry = rate_series_for(CODE)
    issues: list[Issue] = []
    columns: list[tuple[int, str]] = []  # (index, series code)
    for index, name in enumerate(header[1:], start=1):
        spec = registry.get(name)
        if spec is None:
            issues.append(
                Issue(
                    IssueLevel.WARNING,
                    "unregistered_column",
                    f"column {name!r} is not a registered Treasury par-yield tenor; ignored",
                )
            )
            continue
        columns.append((index, spec.code))
    if not columns:
        raise ParseError("No registered tenor columns in the payload")

    missing = 0
    candidates: list[tuple[tuple[Any, ...], Any, RateObservation]] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "field_count",
                    f"line {line_no}: {len(row)} fields, header has {len(header)}; row rejected",
                )
            )
            continue
        try:
            on = _us_date(row[0])
        except ValueError as exc:
            issues.append(
                Issue(IssueLevel.ERROR, "invalid_date", f"line {line_no}: {exc}; row rejected")
            )
            continue
        if not check_date(on, today, f"line {line_no}", issues):
            continue
        for index, code in columns:
            cell = row[index].strip()
            if cell.upper() in MISSING_TOKENS:
                missing += 1
                continue
            try:
                value = parse_published_decimal(cell)
            except ValueError as exc:
                issues.append(Issue(IssueLevel.ERROR, "invalid_number", f"{code}: {exc}", on))
                continue
            if check_rate(value, code, on, issues):
                candidates.append(((code, on), value, RateObservation(code, on, value)))

    observations = dedupe(candidates, issues, "rate")
    observations.sort(key=lambda o: (o.series_code, o.obs_date))
    return ParsedSeries(
        CODE,
        tuple(observations),
        (),
        tuple(issues),
        missing,
        tuple(code for _, code in columns),
    )


class UsTreasury:
    code = CODE

    def parse(self, payload: bytes, today: date | None = None) -> ParsedSeries:
        return parse_par_yield_csv(payload, today)
