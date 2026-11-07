"""Canonical JSON and hashes for valuation documents (D50 conventions).

Decimals become normalized strings (``decimal_str``: no exponent, no trailing zeros), keys are
sorted and separators fixed, so identical content always gives identical bytes and SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from erp_backend.standardization.dataset_types import decimal_str


def jsonable(obj: Any) -> Any:
    """JSON-native form with exact decimals as canonical strings."""
    if isinstance(obj, Decimal):
        return decimal_str(obj)
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):  # pragma: no cover - floats never enter valuation documents
        raise TypeError("floats are not allowed in valuation documents")
    if isinstance(obj, Enum):
        return jsonable(obj.value)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    raise TypeError(f"Unsupported type in valuation document: {type(obj).__name__}")


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
