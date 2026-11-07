"""Value types shared by dataset construction and derivations (no logic beyond formatting)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from erp_backend.standardization.periods import FiscalPeriod


def decimal_str(value: Decimal) -> str:
    """Canonical decimal text: no exponent, no trailing zeros, no negative zero."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class DatasetValue:
    period: FiscalPeriod
    line_item: str
    status: str  # ok | ambiguous
    value: Decimal | None
    unit: str
    provenance: str  # reported | derived | analyst | manual_import
    concept: str
    priority: int
    sign: int
    source_fact_ids: tuple[int, ...]
    detail: Mapping[str, Any] = field(default_factory=dict)  # JSON-native values only
    # Imported values (D57): the source.statement_import_values row and its import.
    # Serialized only when set, so datasets without imports keep their exact bytes.
    import_value_id: int | None = None
    import_id: str | None = None

    def sort_key(self) -> tuple[Any, ...]:
        return (*self.period.sort_key(), self.line_item)

    def as_dict(self) -> dict[str, Any]:
        extra: dict[str, Any] = (
            {}
            if self.import_value_id is None
            else {"import_value_id": self.import_value_id, "import_id": self.import_id}
        )
        return extra | {
            "period": [self.period.period_type, self.period.start.isoformat(),
                       self.period.end.isoformat()],
            "line_item": self.line_item,
            "status": self.status,
            "value": None if self.value is None else decimal_str(self.value),
            "unit": self.unit,
            "provenance": self.provenance,
            "concept": self.concept,
            "priority": self.priority,
            "sign": self.sign,
            "source_fact_ids": list(self.source_fact_ids),
            "detail": dict(self.detail),
        }  # fmt: skip
