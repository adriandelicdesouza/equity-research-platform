"""Bank of Canada Valet adapter: GoC benchmark bond yields and daily FX (§0.4, §4.2; D58).

Network access is **gated**: :func:`boc_client` refuses unless the ``bank_of_canada``
provider is enabled in ``source.providers`` (seeded disabled; P9). Parsing works offline on
archived or fixture payloads only.

Payload: Valet ``/valet/observations/<series,...>/json`` (§0.4 probe for ``FXUSDCAD`` and
``BD.CDN.10YR.DQ.YLD``, 2026-11-06: HTTP 200)::

    {"terms": {...},
     "seriesDetail": {"FXUSDCAD": {"label": "USD/CAD",
                                   "description": "US dollar to Canadian dollar daily ...",
                                   "dimension": {"key": "d", "name": "date"}}, ...},
     "observations": [{"d": "2026-11-03", "FXUSDCAD": {"v": "1.3650"},
                       "BD.CDN.10YR.DQ.YLD": {"v": "3.77"}}, ...]}

* Dates are ISO ``YYYY-MM-DD``.
* A series absent from an observation, ``null``, or ``{"v": ""}`` is **missing** for that
  date: reported, never stored, never filled.
* **FX direction is explicit.** Every registered FX series has a base and a quote
  (``FXUSDCAD``: 1 USD = *rate* CAD). The payload's ``seriesDetail.label`` must read
  ``"<base>/<quote>"``; a different label rejects the series (its direction can't be
  trusted), an absent label is a warning (direction taken from the registry).
* Series that are not registered are reported and not stored.

Not verified, therefore not assumed: the format of observations the Bank publishes as
unavailable (handled generically as missing) and the label format of FX series other than
``FXUSDCAD`` (checked against the registry; a mismatch rejects rather than guesses).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.orm import Session

from erp_backend.ingestion.http import ProviderClient, gated_client
from erp_backend.rates.model import (
    FxObservation,
    Issue,
    IssueLevel,
    ParsedSeries,
    ParseError,
    RateObservation,
    check_date,
    check_rate,
    dedupe,
    fx_series_for,
    parse_published_decimal,
    rate_series_for,
)

CODE = "bank_of_canada"
BASE = "https://www.bankofcanada.ca/valet/observations/{series}/json"
RATE_PER_SECOND = 1.0  # source.providers seed (0003)
USER_AGENT = "equity-research-platform/0.1 (+self-hosted research tool)"
FX_HARD_MAX = Decimal("100000")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SERIES_ID = re.compile(r"^[A-Z0-9][A-Z0-9._]{1,63}$")


def registered_series() -> tuple[str, ...]:
    return tuple(sorted({*rate_series_for(CODE), *fx_series_for(CODE)}))


def observations_url(
    series: list[str] | tuple[str, ...], start: date, end: date
) -> tuple[str, dict[str, Any]]:
    if not series:
        raise ValueError("at least one series is required")
    known = set(registered_series())
    for s in series:
        if not _SERIES_ID.fullmatch(s) or s not in known:
            raise ValueError(f"{s!r} is not a registered Bank of Canada series")
    if end < start:
        raise ValueError("end before start")
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    return BASE.format(series=",".join(sorted(series))), params


def boc_client(db: Session, transport: httpx.BaseTransport | None = None) -> ProviderClient:
    """The only way to obtain a Valet client; refuses while the provider is disabled."""
    return gated_client(db, CODE, USER_AGENT, RATE_PER_SECOND, transport)


def _cell(raw: Any) -> str | None:
    """Text of an observation cell; None when the provider marks it missing."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        value = raw.get("v")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(f"unexpected value {value!r}")
        text = str(value).strip()
        return text or None
    raise ValueError(f"unexpected cell {raw!r}")


def parse_valet(payload: bytes, today: date | None = None) -> ParsedSeries:
    today = today or datetime.now(UTC).date()
    try:
        doc = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"Payload is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ParseError("Payload is not a JSON object")
    if "observations" not in doc:
        message = doc.get("message") if isinstance(doc.get("message"), str) else None
        raise ParseError(f"Provider error: {message}" if message else "No observations key")
    details = doc.get("seriesDetail")
    observations = doc.get("observations")
    if not isinstance(details, dict) or not isinstance(observations, list):
        raise ParseError("seriesDetail must be an object and observations a list")

    rate_registry = rate_series_for(CODE)
    fx_registry = fx_series_for(CODE)
    issues: list[Issue] = []
    usable: list[str] = []
    for series_id in sorted(details):
        detail = details[series_id] if isinstance(details[series_id], dict) else {}
        if series_id in rate_registry:
            usable.append(series_id)
        elif series_id in fx_registry:
            spec = fx_registry[series_id]
            label = detail.get("label")
            if label is None:
                issues.append(
                    Issue(
                        IssueLevel.WARNING,
                        "fx_direction_from_registry",
                        f"{series_id}: no label in the payload; direction taken from the "
                        f"registry ({spec.expected_label}: {spec.quote} per 1 {spec.base})",
                    )
                )
                usable.append(series_id)
            elif str(label).strip().upper() != spec.expected_label:
                issues.append(
                    Issue(
                        IssueLevel.ERROR,
                        "fx_direction_mismatch",
                        f"{series_id}: label {label!r} does not state {spec.expected_label} "
                        f"({spec.quote} per 1 {spec.base}); series not stored",
                    )
                )
            else:
                usable.append(series_id)
        else:
            issues.append(
                Issue(
                    IssueLevel.WARNING,
                    "unregistered_series",
                    f"{series_id} is not a registered series; not stored",
                )
            )

    missing = 0
    rate_items: list[tuple[tuple[Any, ...], Any, RateObservation]] = []
    fx_items: list[tuple[tuple[Any, ...], Any, FxObservation]] = []
    for n, obs in enumerate(observations, start=1):
        if not isinstance(obs, dict):
            issues.append(Issue(IssueLevel.ERROR, "invalid_observation", f"#{n} is not an object"))
            continue
        raw_date = obs.get("d")
        if not isinstance(raw_date, str) or not _ISO_DATE.fullmatch(raw_date):
            issues.append(
                Issue(IssueLevel.ERROR, "invalid_date", f"#{n}: date {raw_date!r}; rejected")
            )
            continue
        try:
            on = date.fromisoformat(raw_date)
        except ValueError:
            issues.append(
                Issue(IssueLevel.ERROR, "invalid_date", f"#{n}: date {raw_date!r}; rejected")
            )
            continue
        if not check_date(on, today, f"#{n}", issues):
            continue
        for series_id in usable:
            try:
                text = _cell(obs.get(series_id))
                if text is None:
                    missing += 1
                    continue
                value = parse_published_decimal(text)
            except ValueError as exc:
                issues.append(Issue(IssueLevel.ERROR, "invalid_number", f"{series_id}: {exc}", on))
                continue
            if series_id in rate_registry:
                code = rate_registry[series_id].code
                if check_rate(value, code, on, issues):
                    rate_items.append(((code, on), value, RateObservation(code, on, value)))
            else:
                spec = fx_registry[series_id]
                if not Decimal(0) < value < FX_HARD_MAX:
                    issues.append(
                        Issue(
                            IssueLevel.ERROR,
                            "invalid_fx_rate",
                            f"{series_id}: {value} must be positive and plausible",
                            on,
                        )
                    )
                    continue
                fx_items.append(
                    (
                        (series_id, on),
                        value,
                        FxObservation(series_id, spec.base, spec.quote, on, value),
                    )
                )

    rates = dedupe(rate_items, issues, "rate")
    fx = dedupe(fx_items, issues, "fx")
    rates.sort(key=lambda o: (o.series_code, o.obs_date))
    fx.sort(key=lambda o: (o.provider_series_id, o.obs_date))
    return ParsedSeries(CODE, tuple(rates), tuple(fx), tuple(issues), missing, tuple(usable))


class BankOfCanada:
    code = CODE

    def parse(self, payload: bytes, today: date | None = None) -> ParsedSeries:
        return parse_valet(payload, today)
