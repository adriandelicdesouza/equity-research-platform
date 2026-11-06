"""Deterministic first-reported / latest-reported fact selection (Blueprint §4.4, D11).

Candidates for one fact key (taxonomy, concept, unit, start, end) are totally ordered by
  1. filed date (ascending)
  2. original before amendment (form ending in "/A") for the same filed date
  3. accession number (ascending, unique)
First-reported is the minimum, latest-reported the maximum. Only candidates filed on or
before ``facts_as_of`` are eligible, which makes rebuilds reproducible. If one filing
reports a key with different values, the key is AMBIGUOUS and no value is selected.
SEC companyfacts carries no ``decimals``; values are compared exactly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from erp_backend.ingestion.sec_parse import RawFact

FactKey = tuple[str, str, str, date | None, date]


class Policy(StrEnum):
    LATEST_REPORTED = "latest_reported"
    FIRST_REPORTED = "first_reported"


class Status(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"


def fact_key(f: RawFact) -> FactKey:
    return (f.taxonomy, f.concept, f.unit, f.period_start, f.period_end)


def order_key(f: RawFact) -> tuple[date, int, str]:
    return (f.filed_date, 1 if f.form.upper().endswith("/A") else 0, f.accession_no)


@dataclass(frozen=True)
class Selection:
    key: FactKey
    status: Status
    first: RawFact | None
    latest: RawFact | None
    candidates: int
    ambiguous_accessions: tuple[str, ...] = ()

    @property
    def restated(self) -> bool:
        return (
            self.first is not None
            and self.latest is not None
            and self.first.value != self.latest.value
        )

    def chosen(self, policy: Policy) -> RawFact | None:
        if self.status is Status.AMBIGUOUS:
            return None
        return self.first if policy is Policy.FIRST_REPORTED else self.latest

    def value(self, policy: Policy) -> Decimal | None:
        chosen = self.chosen(policy)
        return chosen.value if chosen else None


def select(facts: Iterable[RawFact], facts_as_of: date | None = None) -> dict[FactKey, Selection]:
    groups: dict[FactKey, list[RawFact]] = defaultdict(list)
    for f in facts:
        if facts_as_of is None or f.filed_date <= facts_as_of:
            groups[fact_key(f)].append(f)
    out: dict[FactKey, Selection] = {}
    for key, cands in groups.items():
        by_accession: dict[str, set[Decimal]] = defaultdict(set)
        for c in cands:
            by_accession[c.accession_no].add(c.value)
        conflicting = tuple(sorted(a for a, vals in by_accession.items() if len(vals) > 1))
        if conflicting:
            out[key] = Selection(key, Status.AMBIGUOUS, None, None, len(cands), conflicting)
            continue
        ordered = sorted(cands, key=order_key)
        out[key] = Selection(key, Status.OK, ordered[0], ordered[-1], len(cands))
    return out


def restatements(selections: dict[FactKey, Selection]) -> list[Selection]:
    return sorted((s for s in selections.values() if s.restated), key=lambda s: s.key[1:])
