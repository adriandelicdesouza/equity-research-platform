"""Valuation output kinds (Blueprint §6.6, D18).

Conversions between kinds happen only in ``erp_engine.bridge`` (EV → equity) and
``erp_engine.dilution`` (equity → per share).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from erp_engine.core.errors import EngineError


class OutputKind(StrEnum):
    ENTERPRISE_VALUE = "enterprise_value"
    EQUITY_VALUE = "equity_value"
    PER_SHARE_VALUE = "per_share_value"


@dataclass(frozen=True)
class ValuationOutput:
    kind: OutputKind
    value: Decimal
    currency: str
    valuation_date: date
    methodology: str
    basis: str = "minority, marketable"
    trace_id: str = ""


def require_kind(output: ValuationOutput, kind: OutputKind, consumer: str) -> None:
    if output.kind is not kind:
        raise EngineError(
            "output_kind_mismatch",
            f"{consumer} requires {kind.value}, got {output.kind.value} from {output.methodology}.",
        )
