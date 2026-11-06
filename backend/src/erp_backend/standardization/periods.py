"""Company-specific fiscal periods from reported windows (Blueprint §4.4 step 2).

Periods come from the reporting windows themselves, never from the XBRL ``fy``/``fp``
fields (which describe the *filing*, and are wrong for prior-period comparatives) and never
from an assumed calendar year:

* **Fiscal years** are the 350–380-day windows the company reported (52/53-week years
  included). If two annual windows overlap (e.g. a fiscal year-end change), the one
  carrying more values is kept and the other is reported as rejected.
* **Fiscal-year label**: the calendar year in which the fiscal year ends, except that a
  52/53-week year ending in the first 7 days of January belongs to the previous year
  (e.g. a year ending 2021-01-02 is FY2020). Retail years ending late January/February
  take the year they end in (FY2025 ends 2025-02-01).
* **Quarters** (84–98-day windows) and **year-to-date** windows (``H1`` = 6 months,
  ``9M`` = 9 months, starting on the fiscal-year start) are placed in the fiscal year that
  contains them. A quarter after the latest reported fiscal year belongs to the next,
  still-open fiscal year, which starts the day after the latest year-end.
* The quarter number counts quarters from the fiscal-year start (91.3125-day quarters).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

ANNUAL_DAYS = range(350, 381)
QUARTER_DAYS = range(84, 99)
QUARTER_LENGTH = 365.25 / 4
OPEN_YEAR_SPAN = timedelta(days=380)  # how far past the last year-end an open year reaches
PERIOD_ORDER = {"FY": 0, "Q1": 1, "H1": 2, "Q2": 3, "9M": 4, "Q3": 5, "Q4": 6, "LTM": 7}
PERIOD_TYPES = tuple(PERIOD_ORDER)
YTD_TYPES = {2: "H1", 3: "9M"}

Window = tuple[date, date]


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    period_type: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def weeks(self) -> int | None:
        """52/53 for 52/53-week fiscal years; None for calendar-based periods."""
        if self.period_type != "FY":
            return None
        return {364: 52, 371: 53}.get(self.days)

    def sort_key(self) -> tuple[int, int, date, date]:
        return (self.fiscal_year, PERIOD_ORDER[self.period_type], self.start, self.end)

    def as_dict(self) -> dict[str, object]:
        return {
            "fiscal_year": self.fiscal_year,
            "period_type": self.period_type,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "weeks": self.weeks,
        }


def fiscal_year_label(end: date) -> int:
    return end.year - 1 if end.month == 1 and end.day <= 7 else end.year


def _days(window: Window) -> int:
    return (window[1] - window[0]).days + 1


@dataclass(frozen=True)
class FiscalCalendar:
    years: tuple[FiscalPeriod, ...]  # FY periods, ascending
    rejected: tuple[Window, ...]  # overlapping annual windows that were not used

    @classmethod
    def from_windows(cls, weights: Mapping[Window, int]) -> FiscalCalendar:
        """Fiscal years from annual windows; ``weights`` = values per window (tie-break)."""
        annual = [w for w in weights if _days(w) in ANNUAL_DAYS]
        ranked = sorted(annual, key=lambda w: (-weights[w], w[1], w[0]))
        accepted: list[Window] = []
        labels: set[int] = set()
        rejected: list[Window] = []
        for w in ranked:
            label = fiscal_year_label(w[1])
            overlaps = any(w[0] <= a[1] and a[0] <= w[1] for a in accepted)
            if overlaps or label in labels:
                rejected.append(w)
                continue
            accepted.append(w)
            labels.add(label)
        years = tuple(
            sorted(
                (FiscalPeriod(fiscal_year_label(e), "FY", s, e) for s, e in accepted),
                key=lambda p: p.end,
            )
        )
        return cls(years, tuple(sorted(rejected)))

    def year_for(self, start: date, end: date) -> tuple[int, date, date | None] | None:
        """(label, fiscal-year start, fiscal-year end or None if still open) containing
        the window, or None if no fiscal year contains it."""
        for fy in self.years:
            if fy.start <= start and end <= fy.end:
                return fy.fiscal_year, fy.start, fy.end
        if self.years:
            last = self.years[-1]
            open_start = last.end + timedelta(days=1)
            if open_start <= start and end < open_start + OPEN_YEAR_SPAN:
                return last.fiscal_year + 1, open_start, None
        return None

    def classify(self, start: date, end: date) -> FiscalPeriod | None:
        """The fiscal period a duration window represents, or None."""
        days = _days((start, end))
        for fy in self.years:
            if (fy.start, fy.end) == (start, end):
                return fy
        found = self.year_for(start, end)
        if found is None:
            return None
        label, fy_start, fy_end = found
        n = round(((end - fy_start).days + 1) / QUARTER_LENGTH)
        if days in QUARTER_DAYS and 1 <= n <= 4:
            if n == 1 and start != fy_start:
                return None
            if n == 4 and end != fy_end:
                return None
            return FiscalPeriod(label, f"Q{n}", start, end)
        if start == fy_start and n in YTD_TYPES and days not in ANNUAL_DAYS:
            return FiscalPeriod(label, YTD_TYPES[n], start, end)
        return None


def resolve_periods(
    windows: Iterable[tuple[Window, int]], calendar: FiscalCalendar
) -> tuple[dict[Window, FiscalPeriod], list[Window]]:
    """Map duration windows to periods, keeping one window per (fiscal year, type).

    When two windows claim the same period (e.g. a restated comparative with a shifted
    start), the one with more values wins (then the earlier end, then the earlier start);
    the others are returned as conflicting and left unassigned.
    """
    candidates: dict[tuple[int, str], list[tuple[int, Window, FiscalPeriod]]] = {}
    for window, weight in windows:
        period = calendar.classify(*window)
        if period is not None:
            key = (period.fiscal_year, period.period_type)
            candidates.setdefault(key, []).append((weight, window, period))
    assigned: dict[Window, FiscalPeriod] = {}
    conflicting: list[Window] = []
    for options in candidates.values():
        options.sort(key=lambda o: (-o[0], o[1][1], o[1][0]))
        assigned[options[0][1]] = options[0][2]
        conflicting.extend(o[1] for o in options[1:])
    return assigned, sorted(conflicting)
