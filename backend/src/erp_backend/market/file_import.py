"""Market-data file import: validate, preview, then explicit ingestion (§4.2, §4.6; D16, D55).

Two versioned templates (documented in ``docs/operations/market-data-import.md``, header
files under ``templates/market/``):

* ``market_prices.v1`` — daily OHLCV per listing, with the close basis declared per row;
* ``corporate_actions.v1`` — splits and cash dividends per listing.

Workflow:

1. :func:`validate` parses the file (:mod:`erp_backend.market.tabular`), checks structure
   and every record, resolves listings and compares each record with what is already
   stored. It only reads the database and returns a :class:`ValidationReport` in which
   every record is ``accepted`` (new), ``duplicate`` (already stored, or repeated in the
   file), ``conflict`` (same key, different values) or ``rejected`` (invalid).
2. :func:`ingest` re-validates the same bytes, refuses unless the caller names the file's
   SHA-256 (taken from the reviewed report), refuses file-level errors, and refuses any
   skipped record unless ``allow_partial`` is given. It then archives the exact bytes
   (hash-verified), writes one append-only ``source.imports`` row and the accepted records
   with ``import_id`` + ``import_row`` provenance, and an audit event — in the caller's
   transaction.

Never done: currency conversion or inference, substituting an adjusted close for a close
(or the reverse), overwriting or revising an existing observation (a conflicting record is
reported and skipped), creating listings.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend import audit
from erp_backend.ingestion import archive
from erp_backend.market.listings import (
    MIC,
    ListingError,
    check_currency,
    find_listings,
    normalize_mic,
    normalize_ticker,
)
from erp_backend.market.model import AdjCloseBasis, CloseBasis, PriceBar, validate_bar
from erp_backend.market.store import insert_new
from erp_backend.market.tabular import FileFormatError, RawTable, read_table
from erp_backend.models import CorporateAction, PriceObservation, Security, SourceImport

PROVIDER_CODE = "manual_import"
CURRENCY_SOURCE = "import_file"
CROSS_SOURCE_TOLERANCE = Decimal("0.005")  # §4.5: differences above 0.5% are flagged
MAX_SKIPPED_STORED = 5000
SATURDAY = 5  # date.weekday()

_DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_INTEGER = re.compile(r"^-?[0-9]+$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class Status(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class ImportRefusedError(Exception):
    """Ingestion refused; nothing was written."""


@dataclass(frozen=True)
class Column:
    name: str
    required: bool  # the column must be present in the header


@dataclass(frozen=True)
class Template:
    code: str
    columns: tuple[Column, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


PRICES_V1 = Template(
    "market_prices.v1",
    (
        Column("ticker", True),
        Column("exchange_mic", True),
        Column("date", True),
        Column("open", False),
        Column("high", False),
        Column("low", False),
        Column("close", True),
        Column("close_basis", True),
        Column("adj_close", False),
        Column("adj_close_basis", False),
        Column("volume", False),
        Column("currency", True),
    ),
)
ACTIONS_V1 = Template(
    "corporate_actions.v1",
    (
        Column("ticker", True),
        Column("exchange_mic", True),
        Column("ex_date", True),
        Column("kind", True),
        Column("ratio_numerator", False),
        Column("ratio_denominator", False),
        Column("amount", False),
        Column("currency", False),
    ),
)
TEMPLATES = {t.code: t for t in (PRICES_V1, ACTIONS_V1)}
# Imports must state the basis; "unverified" is a provider-adapter state, not a declaration.
IMPORT_CLOSE_BASES = (CloseBasis.RAW.value, CloseBasis.SPLIT_ADJUSTED.value)
IMPORT_ADJ_BASES = tuple(b.value for b in AdjCloseBasis)
ACTION_KINDS = ("split", "cash_dividend")


@dataclass(frozen=True)
class RecordIssue:
    level: str  # "error" rejects the record; "warning" keeps it
    code: str
    message: str
    column: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "column": self.column,
        }


@dataclass(frozen=True)
class RecordResult:
    row: int
    locator: str
    status: Status
    listing: str | None
    on_date: date | None
    issues: tuple[RecordIssue, ...]
    values: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "locator": self.locator,
            "status": self.status.value,
            "listing": self.listing,
            "date": self.on_date.isoformat() if self.on_date else None,
            "issues": [i.as_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class ValidationReport:
    template: str
    filename: str
    file_sha256: str
    bytes: int
    file_kind: str | None
    file_errors: tuple[str, ...]
    records: tuple[RecordResult, ...]

    def count(self, status: Status) -> int:
        return sum(1 for r in self.records if r.status is status)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "records": len(self.records),
            **{s.value: self.count(s) for s in Status},
            "warnings": sum(1 for r in self.records for i in r.issues if i.level == "warning"),
        }

    @property
    def skipped(self) -> tuple[RecordResult, ...]:
        return tuple(r for r in self.records if r.status in (Status.CONFLICT, Status.REJECTED))

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "filename": self.filename,
            "file_sha256": self.file_sha256,
            "bytes": self.bytes,
            "file_errors": list(self.file_errors),
            "summary": self.summary,
            "records": [r.as_dict() for r in self.records],
        }


# --------------------------------------------------------------------------- cell parsing


class _Row:
    """Cell access and issue collection for one data row."""

    def __init__(self, cells: Mapping[str, str]) -> None:
        self.cells = cells
        self.issues: list[RecordIssue] = []

    def error(self, code: str, message: str, column: str | None = None) -> None:
        self.issues.append(RecordIssue("error", code, message, column))

    def warn(self, code: str, message: str, column: str | None = None) -> None:
        self.issues.append(RecordIssue("warning", code, message, column))

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def text(self, column: str, *, required: bool) -> str | None:
        value = self.cells.get(column, "").strip()
        if value == "":
            if required:
                self.error("missing_value", f"{column} is required", column)
            return None
        return value

    def positive_decimal(self, column: str, *, required: bool) -> Decimal | None:
        text = self.text(column, required=required)
        if text is None:
            return None
        if not _DECIMAL.fullmatch(text):
            self.error(
                "invalid_number",
                f"{column} {text!r} is not a plain decimal (digits, optional '.', no "
                "thousands separators, exponents or symbols)",
                column,
            )
            return None
        try:
            value = Decimal(text)
        except InvalidOperation:  # pragma: no cover - the pattern already excludes this
            self.error("invalid_number", f"{column} {text!r} is not a number", column)
            return None
        if value <= 0:
            self.error("non_positive", f"{column} must be greater than 0 (got {text})", column)
            return None
        return value

    def volume(self, column: str) -> int | None:
        text = self.text(column, required=False)
        if text is None:
            return None
        if not _INTEGER.fullmatch(text):
            self.error("invalid_integer", f"{column} {text!r} is not a whole number", column)
            return None
        value = int(text)
        if value < 0:
            self.error("negative_volume", f"{column} must not be negative (got {text})", column)
            return None
        return value

    def iso_date(self, column: str, today: date) -> date | None:
        text = self.text(column, required=True)
        if text is None:
            return None
        try:
            if not _ISO_DATE.fullmatch(text):
                raise ValueError
            value = date.fromisoformat(text)
        except ValueError:
            self.error("invalid_date", f"{column} {text!r} is not a date in YYYY-MM-DD", column)
            return None
        if value > today:
            self.error("future_date", f"{column} {text} is after today ({today})", column)
            return None
        if value.weekday() >= SATURDAY:
            self.warn("weekend_date", f"{column} {text} is a weekend day", column)
        return value

    def choice(self, column: str, allowed: Iterable[str], *, required: bool) -> str | None:
        text = self.text(column, required=required)
        if text is None:
            return None
        options = tuple(allowed)
        if text not in options:
            self.error(
                f"invalid_{column}",
                f"{column} {text!r} is not one of: {', '.join(options)}",
                column,
            )
            return None
        return text

    def currency(self, column: str, *, required: bool) -> str | None:
        text = self.text(column, required=required)
        if text is None:
            return None
        try:
            return check_currency(text)
        except ListingError as exc:
            self.error("unsupported_currency", str(exc), column)
            return None


# --------------------------------------------------------------------------- validation


class _Listings:
    """Resolve and cache ticker + MIC → exactly one security."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.cache: dict[tuple[str, str], list[Security]] = {}

    def resolve(self, row: _Row) -> tuple[Security | None, str | None]:
        ticker = row.text("ticker", required=True)
        mic = row.text("exchange_mic", required=True)
        if ticker is None or mic is None:
            return None, None
        ticker, mic = normalize_ticker(ticker), normalize_mic(mic)
        label = f"{ticker} {mic}"
        if not MIC.fullmatch(mic):
            row.error(
                "invalid_mic", f"exchange_mic {mic!r} is not a 4-character MIC", "exchange_mic"
            )
            return None, label
        key = (ticker, mic)
        if key not in self.cache:
            self.cache[key] = find_listings(self.db, ticker, mic)
        found = self.cache[key]
        if not found:
            row.error(
                "unknown_listing",
                f"No listing {label}; listings are created by company resolution or manual "
                "entry (D15), never by an import",
            )
            return None, label
        if len(found) > 1:
            row.error("ambiguous_listing", f"{len(found)} listings match {label}")
            return None, label
        return found[0], label


def _check_listing_currency(row: _Row, security: Security, currency: str | None) -> None:
    if currency is None:
        return
    if security.trading_currency is None:
        row.error(
            "listing_currency_not_set",
            f"{security.ticker} {security.exchange_mic} has no declared trading currency; "
            "declare it first (listing-set-currency) — it is never inferred from a file",
            "currency",
        )
    elif currency != security.trading_currency:
        row.error(
            "currency_mismatch",
            f"currency {currency} differs from the listing's trading currency "
            f"{security.trading_currency}; not converted",
            "currency",
        )


def _parse_price(
    row: _Row, security: Security | None, today: date
) -> tuple[date | None, dict[str, Any] | None]:
    on = row.iso_date("date", today)
    open_ = row.positive_decimal("open", required=False)
    high = row.positive_decimal("high", required=False)
    low = row.positive_decimal("low", required=False)
    close = row.positive_decimal("close", required=True)
    adj_close = row.positive_decimal("adj_close", required=False)
    volume = row.volume("volume")
    currency = row.currency("currency", required=True)
    close_basis = row.choice("close_basis", IMPORT_CLOSE_BASES, required=True)
    adj_basis = row.choice("adj_close_basis", IMPORT_ADJ_BASES, required=False)
    adj_present = row.text("adj_close", required=False) is not None
    basis_present = row.text("adj_close_basis", required=False) is not None
    if adj_present and not basis_present:
        row.error(
            "missing_value",
            "adj_close_basis is required when adj_close is given",
            "adj_close_basis",
        )
    if basis_present and not adj_present:
        row.error("unexpected_value", "adj_close_basis given without adj_close", "adj_close_basis")
    if security is not None:
        _check_listing_currency(row, security, currency)
    if not row.ok or on is None or close is None or security is None:
        return on, None
    bar = PriceBar(on, open_, high, low, close, adj_close, volume)
    for issue in validate_bar(bar):
        if issue.level.value == "error":
            row.error(issue.code, issue.message)
        else:
            row.warn(issue.code, issue.message)
    if not row.ok:
        return on, None
    return on, {
        "security_id": security.id,
        "trade_date": on,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "adj_close": adj_close,
        "volume": volume,
        "currency": currency,
        "close_basis": close_basis,
        # The column is NOT NULL (0010); without an adjusted close there is no claim about it.
        "adj_close_basis": adj_basis or AdjCloseBasis.UNVERIFIED.value,
    }


def _parse_action(
    row: _Row, security: Security | None, today: date
) -> tuple[date | None, dict[str, Any] | None]:
    on = row.iso_date("ex_date", today)
    kind = row.choice("kind", ACTION_KINDS, required=True)
    values: dict[str, Any] = {
        "ratio_numerator": None,
        "ratio_denominator": None,
        "amount": None,
        "currency": None,
        "currency_source": None,
    }
    if kind == "split":
        num = row.positive_decimal("ratio_numerator", required=True)
        den = row.positive_decimal("ratio_denominator", required=True)
        for column in ("amount", "currency"):
            if row.text(column, required=False) is not None:
                row.error("unexpected_value", f"{column} must be empty for a split", column)
        if num is not None and den is not None and num == den:
            row.error("identity_split", f"split ratio {num}/{den} changes nothing")
        values.update(ratio_numerator=num, ratio_denominator=den)
    elif kind == "cash_dividend":
        amount = row.positive_decimal("amount", required=True)
        currency = row.currency("currency", required=True)
        for column in ("ratio_numerator", "ratio_denominator"):
            if row.text(column, required=False) is not None:
                row.error("unexpected_value", f"{column} must be empty for a dividend", column)
        if (
            currency is not None
            and security is not None
            and security.trading_currency is not None
            and currency != security.trading_currency
        ):
            row.warn(
                "dividend_currency_differs",
                f"dividend currency {currency} differs from the listing's trading currency "
                f"{security.trading_currency}; stored as declared, not converted",
                "currency",
            )
        values.update(amount=amount, currency=currency, currency_source=CURRENCY_SOURCE)
    if not row.ok or on is None or kind is None or security is None:
        return on, None
    return on, {"security_id": security.id, "kind": kind, "ex_date": on, **values}


_PRICE_VALUES = (
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "currency",
    "adj_close_basis",
)
_ACTION_VALUES = ("ratio_numerator", "ratio_denominator", "amount", "currency")
_STORED_FIELDS = (
    "security_id", "trade_date", "close_basis", "kind", "ex_date", *_PRICE_VALUES, *_ACTION_VALUES
)  # fmt: skip


def _key(template: Template, v: Mapping[str, Any]) -> tuple[Any, ...]:
    if template is PRICES_V1:
        return (v["security_id"], v["trade_date"], v["close_basis"])
    return (v["security_id"], v["kind"], v["ex_date"])


def _observed(template: Template, v: Mapping[str, Any]) -> tuple[Any, ...]:
    names = _PRICE_VALUES if template is PRICES_V1 else _ACTION_VALUES
    return tuple(v[n] for n in names)


def _describe(template: Template, observed: tuple[Any, ...]) -> str:
    names = _PRICE_VALUES if template is PRICES_V1 else _ACTION_VALUES
    return ", ".join(
        f"{n}={val}" for n, val in zip(names, observed, strict=True) if val is not None
    )


def _existing(
    db: Session, template: Template, parsed: list[dict[str, Any]]
) -> dict[tuple[Any, ...], list[tuple[str, tuple[Any, ...]]]]:
    """Stored observations for the keys in the file: key → [(provider, observed values)]."""
    if not parsed:
        return {}
    ids = sorted({v["security_id"] for v in parsed}, key=str)
    model: Any = PriceObservation if template is PRICES_V1 else CorporateAction
    date_col = model.trade_date if template is PRICES_V1 else model.ex_date
    dates = [v["trade_date" if template is PRICES_V1 else "ex_date"] for v in parsed]
    rows = db.scalars(
        select(model)
        .where(model.security_id.in_(ids), date_col.between(min(dates), max(dates)))
        .order_by(model.id)
    )
    out: dict[tuple[Any, ...], list[tuple[str, tuple[Any, ...]]]] = defaultdict(list)
    for r in rows:
        values = {c: getattr(r, c, None) for c in _STORED_FIELDS}
        out[_key(template, values)].append((r.provider_code, _observed(template, values)))
    return out


def _check_basis_consistency(rows: list[tuple[_Row, dict[str, Any] | None]]) -> None:
    """One close basis (and one adjusted-close basis) per listing per file."""
    bases: dict[Any, set[tuple[str, str]]] = defaultdict(set)
    for _row, v in rows:
        if v is not None:
            bases[v["security_id"]].add(("close_basis", v["close_basis"]))
            if v["adj_close"] is not None:
                bases[v["security_id"]].add(("adj_close_basis", v["adj_close_basis"]))
    mixed = {
        sid
        for sid, found in bases.items()
        if len({b for kind, b in found if kind == "close_basis"}) > 1
        or len({b for kind, b in found if kind == "adj_close_basis"}) > 1
    }
    for row, v in rows:
        if v is not None and v["security_id"] in mixed:
            row.error(
                "mixed_price_basis",
                "this listing has more than one close_basis or adj_close_basis in the file; "
                "import each series in its own file",
            )


def validate(
    db: Session,
    *,
    filename: str,
    data: bytes,
    template: str,
    today: date | None = None,
) -> ValidationReport:
    """Validate a file against a template and the stored data. Reads the database only."""
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise ImportRefusedError(f"Unknown template {template!r}; one of: {', '.join(TEMPLATES)}")
    today = today or datetime.now(UTC).date()
    name = PurePath(filename).name
    sha = hashlib.sha256(data).hexdigest()

    def report(
        file_errors: list[str], records: list[RecordResult], kind: str | None
    ) -> ValidationReport:
        return ValidationReport(
            tpl.code, name, sha, len(data), kind, tuple(file_errors), tuple(records)
        )

    try:
        table = read_table(name, data)
    except FileFormatError as exc:
        return report([str(exc)], [], None)
    file_errors = _check_header(tpl, table)
    if file_errors:
        return report(file_errors, [], table.kind)
    if not table.rows:
        return report(["File has a header but no data rows"], [], table.kind)

    header = [h.strip().lower() for h in table.header]
    listings = _Listings(db)
    parsed: list[tuple[_Row, dict[str, Any] | None, date | None, str | None]] = []
    for raw in table.rows:
        if len(raw.cells) != len(header):
            row = _Row({})
            row.error("field_count", f"{len(raw.cells)} fields; the header has {len(header)}")
            parsed.append((row, None, None, None))
            continue
        row = _Row(dict(zip(header, raw.cells, strict=True)))
        security, label = listings.resolve(row)
        parse = _parse_price if tpl is PRICES_V1 else _parse_action
        on, values = parse(row, security, today)
        parsed.append((row, values, on, label))

    if tpl is PRICES_V1:
        _check_basis_consistency([(r, v) for r, v, _, _ in parsed])

    valid = [v for r, v, _, _ in parsed if v is not None and r.ok]
    stored = _existing(db, tpl, valid)
    statuses = _classify(
        tpl, [(r, v if v is not None and r.ok else None) for r, v, _, _ in parsed], stored
    )

    records = [
        RecordResult(
            raw.number,
            raw.locator,
            status,
            label,
            on,
            tuple(row.issues),
            v if status is Status.ACCEPTED else None,
        )
        for raw, (row, v, on, label), status in zip(table.rows, parsed, statuses, strict=True)
    ]
    return report([], records, table.kind)


def _check_header(tpl: Template, table: RawTable) -> list[str]:
    names = [h.strip().lower() for h in table.header]
    errors: list[str] = []
    if any(n == "" for n in names):
        errors.append("Header has an empty column name")
    duplicates = sorted({n for n in names if n and names.count(n) > 1})
    if duplicates:
        errors.append(f"Duplicate columns: {', '.join(duplicates)}")
    unknown = sorted({n for n in names if n and n not in tpl.names})
    if unknown:
        errors.append(
            f"Unknown columns for {tpl.code}: {', '.join(unknown)} "
            f"(allowed: {', '.join(tpl.names)})"
        )
    missing = [c.name for c in tpl.columns if c.required and c.name not in names]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
    if tpl is PRICES_V1 and ("adj_close" in names) != ("adj_close_basis" in names):
        errors.append("adj_close and adj_close_basis must be given together")
    return errors


def _classify(
    tpl: Template,
    rows: list[tuple[_Row, dict[str, Any] | None]],
    stored: Mapping[tuple[Any, ...], list[tuple[str, tuple[Any, ...]]]],
) -> list[Status]:
    """Duplicate / conflict detection, within the file and against stored observations.

    Same source (``manual_import``): an identical stored record → duplicate; a different
    stored record for the same key → conflict (never revised by an import). Other providers
    are a different source: a close more than 0.5% away is a warning only (§4.5)."""
    by_key: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for i, (_, v) in enumerate(rows):
        if v is not None:
            by_key[_key(tpl, v)].append(i)
    statuses = [Status.REJECTED if v is None else Status.ACCEPTED for _, v in rows]

    for key, indexes in by_key.items():
        observed = [_observed(tpl, rows[i][1]) for i in indexes]  # type: ignore[arg-type]
        if len(set(observed)) > 1:
            for i in indexes:
                rows[i][0].error(
                    "conflict_in_file", "the same key appears with different values in this file"
                )
                statuses[i] = Status.CONFLICT
            continue
        for i in indexes[1:]:
            rows[i][0].warn("duplicate_in_file", f"repeats row {indexes[0] + 1} of this file")
            statuses[i] = Status.DUPLICATE
        mine = [o for provider, o in stored.get(key, []) if provider == PROVIDER_CODE]
        if observed[0] in mine:
            rows[indexes[0]][0].warn("already_stored", "identical record already imported")
            statuses[indexes[0]] = Status.DUPLICATE
        elif mine:
            rows[indexes[0]][0].error(
                "differs_from_stored",
                "a different record for this key was already imported "
                f"({_describe(tpl, mine[-1])}); imports never revise stored observations",
            )
            for i in indexes:
                statuses[i] = Status.CONFLICT
        if tpl is PRICES_V1:
            _cross_source(rows[indexes[0]], stored.get(key, []))
    return statuses


def _cross_source(
    row: tuple[_Row, dict[str, Any] | None], stored: list[tuple[str, tuple[Any, ...]]]
) -> None:
    r, v = row
    assert v is not None
    close_at = _PRICE_VALUES.index("close")
    for provider, observed in stored:
        if provider == PROVIDER_CODE:
            continue
        other = observed[close_at]
        if abs(v["close"] - other) > CROSS_SOURCE_TOLERANCE * max(v["close"], other):
            r.warn(
                "differs_from_other_source",
                f"close {v['close']} vs {other} from {provider} (same basis, > 0.5%)",
                "close",
            )


# --------------------------------------------------------------------------- ingestion


@dataclass(frozen=True)
class IngestResult:
    import_id: uuid.UUID | None
    status: str  # "complete", "partial" or "no_new_records"
    inserted: int
    already_present: int
    skipped: int
    report: ValidationReport


def ingest(
    db: Session,
    *,
    filename: str,
    data: bytes,
    template: str,
    source_name: str,
    expected_sha256: str,
    raw_root: Path,
    source_reference: str | None = None,
    allow_partial: bool = False,
    user_id: uuid.UUID | None = None,
    today: date | None = None,
) -> IngestResult:
    """Store the accepted records of a validated file. The caller commits."""
    source_name = source_name.strip()
    if not source_name:
        raise ImportRefusedError("A source attribution (--source) is required")
    report = validate(db, filename=filename, data=data, template=template, today=today)
    if expected_sha256.strip().lower() != report.file_sha256:
        raise ImportRefusedError(
            f"File SHA-256 is {report.file_sha256}, not the reviewed {expected_sha256}; "
            "validate this exact file first"
        )
    if report.file_errors:
        raise ImportRefusedError("File-level errors: " + "; ".join(report.file_errors))
    skipped = report.skipped
    if skipped and not allow_partial:
        raise ImportRefusedError(
            f"{len(skipped)} record(s) would be skipped "
            f"({report.count(Status.REJECTED)} rejected, {report.count(Status.CONFLICT)} "
            "conflicting); fix the file or pass --allow-partial"
        )
    accepted = [r for r in report.records if r.status is Status.ACCEPTED]
    if not accepted:
        return IngestResult(
            None, "no_new_records", 0, report.count(Status.DUPLICATE), len(skipped), report
        )

    stored = archive.store(raw_root, PROVIDER_CODE, data)
    record = SourceImport(
        id=uuid.uuid4(),
        provider_code=PROVIDER_CODE,
        kind=report.file_kind or "csv",
        template_version=report.template,
        original_filename=report.filename,
        file_sha256=report.file_sha256,
        stored_path=stored.relative_path,
        bytes=report.bytes,
        source_name=source_name,
        source_reference=(source_reference or "").strip() or None,
        status="partial" if skipped else "complete",
        summary=report.summary,
        skipped=[r.as_dict() for r in skipped[:MAX_SKIPPED_STORED]],
        imported_by=user_id,
    )
    db.add(record)
    db.flush()
    model: Any = PriceObservation if report.template == PRICES_V1.code else CorporateAction
    rows = [
        {**r.values, "provider_code": PROVIDER_CODE, "import_id": record.id, "import_row": r.row}
        for r in accepted
        if r.values is not None
    ]
    inserted = insert_new(db, model, rows)
    audit.record(
        db,
        action="market_data_imported",
        entity_type="import",
        entity_id=str(record.id),
        user_id=user_id,
        after={
            "template": report.template,
            "file_sha256": report.file_sha256,
            "filename": report.filename,
            "source_name": source_name,
            "summary": report.summary,
            "inserted": inserted,
        },
    )
    db.flush()
    return IngestResult(
        record.id,
        record.status,
        inserted,
        report.count(Status.DUPLICATE) + (len(rows) - inserted),
        len(skipped),
        report,
    )


def load_import_file(db: Session, import_id: uuid.UUID, raw_root: Path) -> bytes:
    """The original bytes of an import, verified against the recorded SHA-256."""
    record = db.get(SourceImport, import_id)
    if record is None:
        raise ImportRefusedError(f"No import {import_id}")
    return archive.load(raw_root, record.stored_path, record.file_sha256)
