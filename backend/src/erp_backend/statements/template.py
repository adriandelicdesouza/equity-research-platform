"""The ``financial_statements.v1`` template: pure parsing and record checks (D57).

Layout (one grid; documented in ``docs/operations/financial-statement-import.md``)::

    item,<period label>,<period label>,…[,source_page]
    template,financial_statements.v1          ← metadata rows: key, value
    issuer_ticker,…
    period_type,FY,FY                         ← period rows: one cell per period column
    fiscal_year,2023,2024
    period_start,2023-01-01,2024-01-01
    period_end,2023-12-31,2024-12-31
    IS.REVENUE,1000,1100,45                   ← line-item rows: code or exact label

The grid comes from the format boundary (:mod:`erp_backend.market.tabular`) as cell text,
so a future XLSX reader plugs in unchanged. Everything here is pure: issuer resolution,
the fiscal calendar and comparisons with stored data are in :mod:`.file_import`.

Per non-empty value cell one **record** is produced and classified:

* ``unmapped`` — the row key is neither a standard code nor a standard label;
* ``ambiguous`` — a label naming several items, or one item and period given twice in the
  file with different values (neither value is used);
* ``rejected`` — invalid (number format, a derived-only item, a balance in a YTD column…);
* ``duplicate`` — repeats an identical record of the same file;
* otherwise a candidate, compared later with stored imports and SEC values.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from erp_backend.market.tabular import RawTable
from erp_backend.standardization.mapping import CODE_RE, LineItem, MappingSet
from erp_backend.standardization.resolve import expected_unit

TEMPLATE = "financial_statements.v1"
SCALES = {"units": 0, "thousands": 3, "millions": 6, "billions": 9}
STANDARDS = ("US_GAAP", "IFRS", "OTHER")
PERIOD_TYPES = ("FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M")
INSTANT_COLUMNS = ("FY", "Q1", "Q2", "Q3")  # balances attach to the FY or quarter ending then
DAYS = {"FY": range(350, 381), "H1": range(175, 196), "9M": range(266, 288)} | {
    q: range(84, 99) for q in ("Q1", "Q2", "Q3", "Q4")
}
META_REQUIRED = (
    "template", "issuer_ticker", "issuer_mic", "currency", "scale", "share_scale",
    "accounting_standard", "source_document",
)  # fmt: skip
META_OPTIONAL = ("mapping_version",)
PERIOD_KEYS = ("period_type", "fiscal_year", "period_start", "period_end")
PERIOD_OPTIONAL = ("period_source_document",)
PAGE_COLUMN = "source_page"
ITEM_COLUMN = "item"

_DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_YEAR = re.compile(r"^[0-9]{4}$")


class Status(StrEnum):
    ACCEPTED = "accepted"  # new; will be stored
    CONFLICTING = "conflicting"  # new but differs from a stored import or SEC value; stored
    DUPLICATE = "duplicate"  # identical record already stored or earlier in the file
    UNMAPPED = "unmapped"  # no standard line item
    AMBIGUOUS = "ambiguous"  # several items, or different values for one cell in the file
    REJECTED = "rejected"  # invalid


STORED = (Status.ACCEPTED, Status.CONFLICTING)
SKIPPED = (Status.UNMAPPED, Status.AMBIGUOUS, Status.REJECTED)


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class PeriodColumn:
    index: int  # cell index in the grid
    label: str
    period_type: str
    fiscal_year: int
    start: date
    end: date
    source_document: str

    def window(self) -> tuple[str, date, date]:
        return (self.period_type, self.start, self.end)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "period_type": self.period_type,
            "fiscal_year": self.fiscal_year,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "source_document": self.source_document,
        }


@dataclass
class Record:
    row: int
    locator: str
    key: str  # the row key as written
    column: PeriodColumn
    text: str  # the cell as written
    status: Status = Status.ACCEPTED
    issues: list[Issue] = field(default_factory=list)
    item: LineItem | None = None
    reported_value: Decimal | None = None
    scale: int = 0
    value: Decimal | None = None
    unit: str | None = None
    source_page: str | None = None

    def error(self, status: Status, code: str, message: str) -> None:
        self.issues.append(Issue("error", code, message))
        self.status = status

    def warn(self, code: str, message: str) -> None:
        self.issues.append(Issue("warning", code, message))

    @property
    def cell_key(self) -> tuple[str, str, date, date] | None:
        if self.item is None:
            return None
        return (self.item.code, *self.column.window())

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "locator": self.locator,
            "status": self.status.value,
            "key": self.key,
            "line_item": self.item.code if self.item else None,
            "period": self.column.label,
            "value": self.text,
            "normalized_value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "issues": [i.as_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class Metadata:
    issuer_ticker: str
    issuer_mic: str
    currency: str
    scale: int
    share_scale: int
    accounting_standard: str
    source_document: str
    mapping_version: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "issuer_ticker": self.issuer_ticker,
            "issuer_mic": self.issuer_mic,
            "currency": self.currency,
            "scale": self.scale,
            "share_scale": self.share_scale,
            "accounting_standard": self.accounting_standard,
            "source_document": self.source_document,
            "mapping_version": self.mapping_version,
        }


@dataclass
class ParsedFile:
    errors: list[str]
    metadata: Metadata | None = None
    columns: list[PeriodColumn] = field(default_factory=list)
    item_rows: list[tuple[int, str, str, tuple[str, ...]]] = field(default_factory=list)
    page_index: int | None = None


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def parse_grid(table: RawTable) -> ParsedFile:
    """Structure only: header, metadata, period columns, item rows. No mapping yet."""
    out = ParsedFile(errors=[])
    header = [h.strip() for h in table.header]
    if not header or header[0].lower() != ITEM_COLUMN:
        out.errors.append(f"The first header cell must be {ITEM_COLUMN!r}")
        return out
    labels = header[1:]
    page_hits = [i for i, h in enumerate(header) if h.lower() == PAGE_COLUMN]
    if len(page_hits) > 1:
        out.errors.append(f"Duplicate {PAGE_COLUMN!r} column")
    out.page_index = page_hits[0] if page_hits else None
    period_idx = [i for i in range(1, len(header)) if i != out.page_index]
    if not period_idx:
        out.errors.append("No period columns in the header")
    if any(header[i] == "" for i in period_idx):
        out.errors.append("A period column has an empty label")
    dup = sorted({h for h in labels if h and labels.count(h) > 1})
    if dup:
        out.errors.append(f"Duplicate column labels: {', '.join(dup)}")

    meta: dict[str, str] = {}
    period_rows: dict[str, tuple[int, tuple[str, ...]]] = {}
    for raw in table.rows:
        cells = tuple(c.strip() for c in raw.cells)
        if len(cells) > len(header):
            out.errors.append(f"{raw.locator}: {len(cells)} cells; the header has {len(header)}")
            continue
        cells = cells + ("",) * (len(header) - len(cells))
        key = cells[0]
        lowered = key.lower()
        if lowered in META_REQUIRED or lowered in META_OPTIONAL:
            if lowered in meta:
                out.errors.append(f"{raw.locator}: metadata {lowered!r} given twice")
            elif any(c for c in cells[2:]):
                out.errors.append(f"{raw.locator}: metadata {lowered!r} takes one value cell")
            else:
                meta[lowered] = cells[1]
        elif lowered in PERIOD_KEYS or lowered in PERIOD_OPTIONAL:
            if lowered in period_rows:
                out.errors.append(f"{raw.locator}: period row {lowered!r} given twice")
            else:
                period_rows[lowered] = (raw.number, cells)
        elif key == "":
            if any(cells):
                out.errors.append(f"{raw.locator}: the row has values but no item")
        else:
            out.item_rows.append((raw.number, raw.locator, key, cells))

    out.metadata = _metadata(meta, out.errors)
    out.columns = _columns(header, period_idx, period_rows, out.metadata, out.errors)
    if out.page_index is not None:
        for name, (_, cells) in period_rows.items():
            if cells[out.page_index]:
                out.errors.append(f"period row {name!r} must leave {PAGE_COLUMN!r} empty")
    if not out.item_rows:
        out.errors.append("No line-item rows")
    return out


def _metadata(meta: Mapping[str, str], errors: list[str]) -> Metadata | None:
    missing = [k for k in META_REQUIRED if not meta.get(k)]
    if missing:
        errors.append(f"Missing metadata: {', '.join(missing)}")
        return None
    ok = True
    if meta["template"] != TEMPLATE:
        errors.append(f"template is {meta['template']!r}; this importer reads {TEMPLATE!r}")
        ok = False
    scales = {}
    for key in ("scale", "share_scale"):
        if meta[key].lower() not in SCALES:
            errors.append(f"{key} {meta[key]!r} is not one of: {', '.join(SCALES)}")
            ok = False
        else:
            scales[key] = SCALES[meta[key].lower()]
    if meta["accounting_standard"] not in STANDARDS:
        errors.append(
            f"accounting_standard {meta['accounting_standard']!r} is not one of: "
            f"{', '.join(STANDARDS)}"
        )
        ok = False
    if not re.fullmatch(r"[A-Z]{3}", meta["currency"]):
        errors.append(f"currency {meta['currency']!r} is not an ISO 4217 code (e.g. CAD, USD)")
        ok = False
    if not ok:
        return None
    return Metadata(
        meta["issuer_ticker"].upper(), meta["issuer_mic"].upper(), meta["currency"],
        scales["scale"], scales["share_scale"], meta["accounting_standard"],
        meta["source_document"], meta.get("mapping_version") or None,
    )  # fmt: skip


def _columns(
    header: list[str],
    period_idx: list[int],
    rows: Mapping[str, tuple[int, tuple[str, ...]]],
    meta: Metadata | None,
    errors: list[str],
) -> list[PeriodColumn]:
    missing = [k for k in PERIOD_KEYS if k not in rows]
    if missing:
        errors.append(f"Missing period rows: {', '.join(missing)}")
        return []
    out: list[PeriodColumn] = []
    for i in period_idx:
        label = header[i]
        cell = {k: rows[k][1][i] for k in PERIOD_KEYS}
        where = f"column {label!r}"
        ptype = cell["period_type"]
        if ptype not in PERIOD_TYPES:
            errors.append(f"{where}: period_type {ptype!r} is not one of {', '.join(PERIOD_TYPES)}")
            continue
        if not _YEAR.fullmatch(cell["fiscal_year"]):
            errors.append(f"{where}: fiscal_year {cell['fiscal_year']!r} is not a 4-digit year")
            continue
        dates = []
        for k in ("period_start", "period_end"):
            try:
                if not _ISO_DATE.fullmatch(cell[k]):
                    raise ValueError
                dates.append(date.fromisoformat(cell[k]))
            except ValueError:
                errors.append(f"{where}: {k} {cell[k]!r} is not a date in YYYY-MM-DD")
        if len(dates) != 2:
            continue
        start, end = dates
        days = (end - start).days + 1
        if days not in DAYS[ptype]:
            errors.append(
                f"{where}: {ptype} from {start} to {end} is {days} days, outside "
                f"{DAYS[ptype].start}–{DAYS[ptype].stop - 1}"
            )
            continue
        override = rows.get("period_source_document", (0, ("",) * len(header)))[1][i]
        document = override or (meta.source_document if meta else "")
        out.append(PeriodColumn(i, label, ptype, int(cell["fiscal_year"]), start, end, document))
    windows = [c.window() for c in out]
    for w in sorted({w for w in windows if windows.count(w) > 1}):
        errors.append(f"Period {w[0]} {w[1]}..{w[2]} appears in more than one column")
    return out


def label_index(mapping: MappingSet) -> dict[str, list[str]]:
    """Normalized label → codes (several codes = an ambiguous label)."""
    index: dict[str, list[str]] = defaultdict(list)
    for item in mapping.line_items.values():
        index[_norm(item.label)].append(item.code)
    return index


def records(parsed: ParsedFile, mapping: MappingSet, today: date) -> list[Record]:
    """One record per non-empty value cell, mapped and checked; in-file duplicates and
    in-file conflicts are classified here. Requires a structurally valid file."""
    assert parsed.metadata is not None
    meta = parsed.metadata
    labels = label_index(mapping)
    out: list[Record] = []
    for number, locator, key, cells in parsed.item_rows:
        page = cells[parsed.page_index] if parsed.page_index is not None else ""
        for col in parsed.columns:
            text = cells[col.index]
            if text == "":
                continue
            rec = Record(number, f"{locator}, column {col.label!r}", key, col, text)
            rec.source_page = page or None
            _map(rec, key, mapping, labels)
            if rec.item is not None:
                _check(rec, meta, today)
            out.append(rec)
    _in_file(out)
    return out


def _map(rec: Record, key: str, mapping: MappingSet, labels: Mapping[str, list[str]]) -> None:
    if CODE_RE.fullmatch(key):
        item = mapping.line_items.get(key)
        if item is None:
            rec.error(
                Status.UNMAPPED, "unknown_code", f"{key} is not a line item of {mapping.version}"
            )
        else:
            rec.item = item
        return
    codes = labels.get(_norm(key), [])
    if not codes:
        rec.error(
            Status.UNMAPPED,
            "unmapped_label",
            f"{key!r} is neither a standard code nor a standard label of {mapping.version}",
        )
    elif len(codes) > 1:
        rec.error(
            Status.AMBIGUOUS,
            "ambiguous_label",
            f"{key!r} names several line items ({', '.join(sorted(codes))}); use the code",
        )
    else:
        rec.item = mapping.line_items[codes[0]]
        rec.warn("mapped_by_label", f"{key!r} mapped to {codes[0]} by its label")


def _check(rec: Record, meta: Metadata, today: date) -> None:
    item = rec.item
    assert item is not None
    if item.derived_only:
        rec.error(
            Status.REJECTED,
            "derived_only",
            f"{item.code} is computed by the platform, never imported",
        )
        return
    if rec.column.end > today:
        rec.error(Status.REJECTED, "future_period", f"period ends {rec.column.end}, after today")
        return
    if item.period == "instant" and rec.column.period_type not in INSTANT_COLUMNS:
        rec.error(
            Status.REJECTED,
            "balance_in_ytd_column",
            f"{item.code} is a balance; give it in the FY or quarter (Q1–Q3) column ending "
            "on the balance-sheet date",
        )
        return
    if not _DECIMAL.fullmatch(rec.text):
        rec.error(
            Status.REJECTED,
            "invalid_number",
            f"{rec.text!r} is not a plain decimal (digits, optional '-' and '.', no thousands "
            "separators, parentheses, exponents or symbols)",
        )
        return
    reported = Decimal(rec.text)
    if item.value_type == "per_share":
        scale = 0
    elif item.value_type == "count":
        scale = meta.share_scale
    else:
        scale = meta.scale
    if item.value_type == "count" and reported < 0:
        rec.error(Status.REJECTED, "negative_count", f"{item.code} cannot be negative")
        return
    if item.sign == "expense" and reported < 0:
        rec.warn(
            "negative_expense",
            f"{item.code} is stored as a positive cost; a negative value is a credit — "
            "check the sign",
        )
    rec.reported_value = reported
    rec.scale = scale
    rec.value = reported.scaleb(scale)
    rec.unit = expected_unit(item, meta.currency)


def _in_file(recs: list[Record]) -> None:
    by_key: dict[tuple[str, str, date, date], list[Record]] = defaultdict(list)
    for r in recs:
        if r.status is Status.ACCEPTED and r.cell_key is not None:
            by_key[r.cell_key].append(r)
    for group in by_key.values():
        if len({r.value for r in group}) > 1:
            for r in group:
                r.error(
                    Status.AMBIGUOUS,
                    "conflict_in_file",
                    "this item and period appear in the file with different values; neither "
                    "is used",
                )
            continue
        for r in group[1:]:
            r.status = Status.DUPLICATE
            r.warn("duplicate_in_file", f"repeats {group[0].locator}")
