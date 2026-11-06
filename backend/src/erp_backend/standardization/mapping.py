"""Versioned standard line-item taxonomy and concept mappings (Blueprint §3.3, §4.4, §5.2).

Source of truth is YAML under ``mappings/<version>/``: ``line_items.yaml`` plus one file
per XBRL taxonomy (``us-gaap.yaml``, ``ifrs-full.yaml``). A version is immutable once
loaded into the database; :func:`content_sha256` fingerprints it so the loading migration
can refuse an edited version. Loading is pure (no database) and fully validated.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

STATEMENTS = ("IS", "BS", "CF")
PERIODS = ("duration", "instant")
VALUE_TYPES = ("flow", "stock", "per_share", "count")
SIGNS = ("natural", "expense", "cash_flow")
TAXONOMY_FILES = ("us-gaap", "ifrs-full")
CODE_RE = re.compile(r"^(IS|BS|CF)\.[A-Z][A-Z0-9_]*$")
CONCEPT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
VERSION_RE = re.compile(r"^v[1-9][0-9]*$")


class MappingError(ValueError):
    """The mapping files are malformed or inconsistent."""


@dataclass(frozen=True)
class LineItem:
    code: str
    label: str
    statement: str
    period: str
    value_type: str
    sign: str
    parent: str | None = None
    subtotal: tuple[tuple[int, str], ...] = ()
    derived_only: bool = False
    sort_order: int = 0

    @property
    def is_subtotal(self) -> bool:
        return bool(self.subtotal)


@dataclass(frozen=True)
class Candidate:
    taxonomy: str
    concept: str
    line_item: str
    priority: int  # 1 = highest
    sign: int  # +1 or -1


@dataclass(frozen=True)
class MappingSet:
    version: str
    sha256: str
    line_items: Mapping[str, LineItem]
    candidates: Mapping[tuple[str, str], tuple[Candidate, ...]]  # (taxonomy, code) → ordered

    def for_line_item(self, taxonomy: str, code: str) -> tuple[Candidate, ...]:
        return self.candidates.get((taxonomy, code), ())

    @property
    def taxonomies(self) -> tuple[str, ...]:
        return tuple(sorted({t for t, _ in self.candidates}))


def version_dir(root: Path, version: str) -> Path:
    if not VERSION_RE.match(version):
        raise MappingError(f"Invalid mapping version {version!r} (expected v1, v2, …)")
    return root / version


def _files(directory: Path) -> list[Path]:
    files = [directory / "line_items.yaml"] + [directory / f"{t}.yaml" for t in TAXONOMY_FILES]
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        raise MappingError(f"{directory}: missing {', '.join(missing)}")
    return files


def content_sha256(root: Path, version: str) -> str:
    """Fingerprint of a mapping version: SHA-256 over each file's name and bytes, in order."""
    digest = hashlib.sha256()
    for f in _files(version_dir(root, version)):
        data = f.read_bytes()
        digest.update(f"{f.name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MappingError(f"{path.name}: invalid YAML: {exc}") from exc


def _choice(raw: Mapping[str, Any], key: str, allowed: tuple[str, ...], where: str) -> str:
    value = raw.get(key)
    if value not in allowed:
        raise MappingError(f"{where}: {key} must be one of {', '.join(allowed)}, got {value!r}")
    return str(value)


def _subtotal(raw: Any, where: str) -> tuple[tuple[int, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise MappingError(f"{where}: subtotal must be a non-empty list")
    out = []
    for term in raw:
        if not isinstance(term, str) or term[:1] not in "+-" or not CODE_RE.match(term[1:]):
            raise MappingError(f"{where}: subtotal term {term!r} must look like +IS.CODE")
        out.append((1 if term[0] == "+" else -1, term[1:]))
    return tuple(out)


def parse_line_items(doc: Any) -> dict[str, LineItem]:
    if not isinstance(doc, dict) or not isinstance(doc.get("line_items"), list):
        raise MappingError("line_items.yaml: top-level 'line_items' list required")
    items: dict[str, LineItem] = {}
    for i, raw in enumerate(doc["line_items"]):
        if not isinstance(raw, dict):
            raise MappingError(f"line_items[{i}]: mapping expected")
        code = raw.get("code")
        if not isinstance(code, str) or not CODE_RE.match(code):
            raise MappingError(f"line_items[{i}]: invalid code {code!r}")
        if code in items:
            raise MappingError(f"{code}: duplicate code")
        unknown = set(raw) - {
            "code", "label", "parent", "period", "value_type", "sign", "subtotal", "derived_only"
        }  # fmt: skip
        if unknown:
            raise MappingError(f"{code}: unknown fields {sorted(unknown)}")
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            raise MappingError(f"{code}: label required")
        period = _choice(raw, "period", PERIODS, code)
        value_type = _choice(raw, "value_type", VALUE_TYPES, code)
        if (value_type == "stock") != (period == "instant") and value_type != "count":
            raise MappingError(f"{code}: value_type {value_type} is inconsistent with {period}")
        items[code] = LineItem(
            code=code,
            label=label.strip(),
            statement=code.split(".", 1)[0],
            period=period,
            value_type=value_type,
            sign=_choice(raw, "sign", SIGNS, code),
            parent=raw.get("parent"),
            subtotal=_subtotal(raw.get("subtotal"), code),
            derived_only=bool(raw.get("derived_only", False)),
            sort_order=i + 1,
        )
    for item in items.values():
        if item.parent is not None and item.parent not in items:
            raise MappingError(f"{item.code}: unknown parent {item.parent}")
        for _, child in item.subtotal:
            if child not in items:
                raise MappingError(f"{item.code}: subtotal references unknown {child}")
            if child == item.code:
                raise MappingError(f"{item.code}: subtotal references itself")
        if item.derived_only and not item.subtotal:
            raise MappingError(f"{item.code}: derived_only items need a subtotal definition")
    return items


def parse_concepts(
    doc: Any, expected_taxonomy: str, items: Mapping[str, LineItem]
) -> dict[tuple[str, str], tuple[Candidate, ...]]:
    where = f"{expected_taxonomy}.yaml"
    if not isinstance(doc, dict) or doc.get("taxonomy") != expected_taxonomy:
        raise MappingError(f"{where}: 'taxonomy: {expected_taxonomy}' required")
    mappings = doc.get("mappings")
    if not isinstance(mappings, dict):
        raise MappingError(f"{where}: top-level 'mappings' mapping required")
    out: dict[tuple[str, str], tuple[Candidate, ...]] = {}
    for code, raw_list in mappings.items():
        if code not in items:
            raise MappingError(f"{where}: unknown line item {code}")
        if items[code].derived_only:
            raise MappingError(f"{where}: {code} is derived_only and cannot be mapped")
        if not isinstance(raw_list, list) or not raw_list:
            raise MappingError(f"{where}: {code} needs a non-empty candidate list")
        seen: set[str] = set()
        candidates = []
        for priority, raw in enumerate(raw_list, start=1):
            if isinstance(raw, str):
                concept, sign = raw, 1
            elif isinstance(raw, dict) and set(raw) <= {"concept", "sign"} and "concept" in raw:
                concept, sign = raw["concept"], raw.get("sign", 1)
            else:
                raise MappingError(f"{where}: {code} candidate {priority} is malformed")
            if not isinstance(concept, str) or not CONCEPT_RE.match(concept):
                raise MappingError(f"{where}: {code} has invalid concept {concept!r}")
            if sign not in (1, -1) or isinstance(sign, bool):
                raise MappingError(f"{where}: {code}/{concept} sign must be 1 or -1")
            if concept in seen:
                raise MappingError(f"{where}: {code} lists {concept} twice")
            seen.add(concept)
            candidates.append(Candidate(expected_taxonomy, concept, code, priority, sign))
        out[(expected_taxonomy, code)] = tuple(candidates)
    return out


def load(root: Path, version: str) -> MappingSet:
    directory = version_dir(root, version)
    files = _files(directory)
    items = parse_line_items(_yaml(files[0]))
    candidates: dict[tuple[str, str], tuple[Candidate, ...]] = {}
    for taxonomy, path in zip(TAXONOMY_FILES, files[1:], strict=True):
        candidates.update(parse_concepts(_yaml(path), taxonomy, items))
    return MappingSet(version, content_sha256(root, version), items, candidates)
