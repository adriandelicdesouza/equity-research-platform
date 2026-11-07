"""Classification blocks (design §7): how a methodology applies to a financial classification.

The source of truth is versioned YAML under ``mappings/valuation-blocks/``, loaded and pinned
by migration (like the D49 line-item mappings) into append-only ``ref.valuation_blocks``. The
loader here is pure; :func:`current_block` reads the stored row the run will cite.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_backend.valuation.canonical import sha256_of

CURRENT_VERSION = "v1"
POLICIES = ("permitted", "permitted_with_warnings", "blocked")
METHODOLOGIES = ("dcf_fcff", "dcf_fcfe")
CLASSIFICATIONS = (
    "non_financial",
    "bank",
    "insurer",
    "specialty_lender",
    "investment_holding",
    "reit",
    "asset_manager_broker",
)
LEASE_TREATMENTS = ("none", "ifrs16_as_debt", "asc842_as_operating")


class BlockError(ValueError):
    """The block file is malformed."""


@dataclass(frozen=True)
class Block:
    classification: str
    methodology: str
    policy: str
    definition: dict[str, Any]

    @property
    def definition_sha256(self) -> str:
        return sha256_of(self.definition)

    @property
    def acknowledgements(self) -> tuple[str, ...]:
        return tuple(self.definition.get("acknowledgements", ()))

    @property
    def line_items(self) -> dict[str, list[str]]:
        return dict(self.definition.get("line_items", {}))

    def lease_line_items(self, treatment: str) -> dict[str, list[str]] | None:
        """Extra bridge inputs for a lease treatment; ``None`` if this block doesn't support it."""
        table: dict[str, dict[str, list[str]]] = self.definition.get("lease_line_items", {})
        return dict(table[treatment]) if treatment in table else None

    @property
    def required_assumptions(self) -> tuple[str, ...]:
        return tuple(self.definition.get("required_assumptions", ()))


@dataclass(frozen=True)
class BlockSet:
    version: str
    sha256: str  # of the YAML file bytes (pinned by the migration)
    blocks: tuple[Block, ...]

    def get(self, classification: str, methodology: str) -> Block | None:
        for b in self.blocks:
            if (b.classification, b.methodology) == (classification, methodology):
                return b
        return None


def path_for(root: Path, version: str) -> Path:
    return root / "valuation-blocks" / f"{version}.yaml"


def _components(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BlockError(f"{where}: expected a non-empty list of signed line-item codes")
    for c in value:
        if not (isinstance(c, str) and c[:1] in "+-" and "." in c):
            raise BlockError(f"{where}: invalid component {c!r}")
    return list(value)


def _parse_block(raw: dict[str, Any]) -> Block:
    cls, method, policy = raw.get("classification"), raw.get("methodology"), raw.get("policy")
    where = f"block {cls}/{method}"
    if cls not in CLASSIFICATIONS or method not in METHODOLOGIES or policy not in POLICIES:
        raise BlockError(f"{where}: unknown classification, methodology or policy")
    definition: dict[str, Any] = {"policy": policy}
    if policy != "blocked":
        definition["acknowledgements"] = sorted(raw.get("acknowledgements") or [])
        definition["line_items"] = {
            k: _components(v, f"{where} {k}") for k, v in sorted(raw["line_items"].items())
        }
        leases = raw.get("lease_line_items") or {}
        if not set(leases) <= set(LEASE_TREATMENTS) or "none" not in leases:
            raise BlockError(f"{where}: lease_line_items must include 'none' and known treatments")
        definition["lease_line_items"] = {
            t: {k: _components(v, f"{where} {t}.{k}") for k, v in sorted((m or {}).items())}
            for t, m in sorted(leases.items())
        }
        definition["required_assumptions"] = list(raw.get("required_assumptions") or [])
        if policy == "permitted_with_warnings" and not definition["acknowledgements"]:
            raise BlockError(f"{where}: a warning policy needs acknowledgement codes")
    return Block(cls, method, policy, definition)


def load(root: Path, version: str = CURRENT_VERSION) -> BlockSet:
    path = path_for(root, version)
    data = path.read_bytes()
    raw = yaml.safe_load(data)
    if not isinstance(raw, dict) or raw.get("version") != version:
        raise BlockError(f"{path.name}: version must be {version!r}")
    blocks = tuple(_parse_block(b) for b in raw.get("blocks") or [])
    keys = [(b.classification, b.methodology) for b in blocks]
    if len(keys) != len(set(keys)):
        raise BlockError("duplicate (classification, methodology) block")
    missing = {(c, m) for c in CLASSIFICATIONS for m in METHODOLOGIES} - set(keys)
    if missing:
        raise BlockError(f"no block for {sorted(missing)}")
    return BlockSet(version, hashlib.sha256(data).hexdigest(), blocks)


def current_block(db: Session, classification: str, methodology: str) -> Any:
    """The stored ``ref.valuation_blocks`` row of the current version, or ``None``."""
    from erp_backend.models import ValuationBlock

    return db.scalars(
        select(ValuationBlock).where(
            ValuationBlock.block_version == CURRENT_VERSION,
            ValuationBlock.classification == classification,
            ValuationBlock.methodology == methodology,
        )
    ).first()


def block_from_row(row: Any) -> Block:
    return Block(row.classification, row.methodology, row.policy, dict(row.definition))
