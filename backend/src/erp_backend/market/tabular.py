"""File-format boundary for tabular imports (Blueprint §4.6; D55).

A reader turns file bytes into a :class:`RawTable`: a header and data rows of **cell text**,
each row with a human-readable locator. Everything after this point (template mapping,
validation, duplicate/conflict detection, persistence) works on ``RawTable`` only and does
not know which file format the data came from.

Only CSV is implemented. XLSX is a deliberate gap: it needs a spreadsheet library, which
needs the owner's approval (no new dependency without it). An XLSX reader must:

* read one named sheet and yield its cells as text exactly as the analyst would read them
  — dates as ISO ``YYYY-MM-DD``, numbers as the exact decimal text of the stored value
  (never a binary-float rendering), empty cells as ``""``; formulas are refused, not
  evaluated (no formula engine, CLAUDE.md);
* set ``locator`` to ``"<sheet>!<row>"`` and ``kind`` to ``"excel"``.

It then plugs into :func:`read_table` without any other change.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath

MAX_BYTES = 20 * 1024 * 1024  # a 20-year daily history is well under 1 MB


class FileFormatError(ValueError):
    """The file as a whole cannot be read (encoding, structure, unsupported format)."""


@dataclass(frozen=True)
class RawRow:
    number: int  # 1-based data-row number; stored as the observation's ``import_row``
    locator: str  # where to find it in the original file, e.g. "line 12"
    cells: tuple[str, ...]


@dataclass(frozen=True)
class RawTable:
    kind: str  # "csv" or "excel" (source.imports.kind)
    header: tuple[str, ...]
    rows: tuple[RawRow, ...]


def read_csv(data: bytes) -> RawTable:
    """Strict CSV: UTF-8 (a leading BOM is tolerated), comma-delimited, ``"`` quoting,
    first non-empty line is the header. Blank lines are ignored."""
    if len(data) > MAX_BYTES:
        raise FileFormatError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileFormatError(
            f"File is not valid UTF-8 ({exc.reason} at byte {exc.start})"
        ) from exc
    if "\x00" in text:
        raise FileFormatError("File contains NUL bytes; not a text CSV")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header: tuple[str, ...] | None = None
    rows: list[RawRow] = []
    try:
        for cells in reader:
            if not cells or all(c.strip() == "" for c in cells):
                continue
            if header is None:
                header = tuple(cells)
                continue
            rows.append(RawRow(len(rows) + 1, f"line {reader.line_num}", tuple(cells)))
    except csv.Error as exc:
        raise FileFormatError(f"Malformed CSV at line {reader.line_num}: {exc}") from exc
    if header is None:
        raise FileFormatError("File is empty (no header row)")
    return RawTable("csv", header, tuple(rows))


def _xlsx_not_approved(data: bytes) -> RawTable:
    raise FileFormatError(
        "XLSX import is not available yet: it needs a spreadsheet library, which needs the "
        "owner's approval. Export the sheet as CSV (UTF-8) instead."
    )


READERS: dict[str, Callable[[bytes], RawTable]] = {
    ".csv": read_csv,
    ".xlsx": _xlsx_not_approved,
    ".xlsm": _xlsx_not_approved,
    ".xls": _xlsx_not_approved,
}


def read_table(filename: str, data: bytes) -> RawTable:
    """Pick the reader from the file extension (case-insensitive)."""
    suffix = PurePath(filename).suffix.lower()
    reader = READERS.get(suffix)
    if reader is None:
        raise FileFormatError(f"Unsupported file type {suffix or '(none)'!r}; expected .csv")
    return reader(data)
