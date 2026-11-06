"""Golden-master regression (Blueprint §6.5, §13.1).

Stores each benchmark's output hash together with the engine and module versions. If the
engine output changes while the versions are unchanged, the test fails: any change to a
calculation must bump the affected module version. Regenerate deliberately with
``ERP_UPDATE_GOLDEN=1 make test-engine`` after bumping versions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from engine_fixtures import load, to_inputs
from erp_engine.api import run_valuation
from erp_engine.version import ENGINE_VERSION, MODULE_VERSIONS

GOLDEN = Path(__file__).parent / "golden" / "outputs.json"
CASES = ["B1", "B1-VD", "B2", "B3"]


def _snapshot() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for name in CASES:
        r = run_valuation(to_inputs(load(name)))
        out[name] = {
            "output_hash": r.output_hash,
            "input_hash": r.input_hash,
            "per_share": str(r.per_share.value),
        }
    return out


def test_outputs_unchanged_unless_versions_bumped() -> None:
    current = {
        "engine_version": ENGINE_VERSION,
        "module_versions": MODULE_VERSIONS,
        "cases": _snapshot(),
    }
    if os.environ.get("ERP_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        pytest.skip("golden file regenerated")
    stored = json.loads(GOLDEN.read_text())
    versions_same = (
        stored["engine_version"] == ENGINE_VERSION and stored["module_versions"] == MODULE_VERSIONS
    )
    if versions_same:
        assert current["cases"] == stored["cases"], (
            "Engine outputs changed without a version bump. Bump ENGINE_VERSION/MODULE_VERSIONS "
            "in erp_engine/version.py, then regenerate with ERP_UPDATE_GOLDEN=1."
        )
    else:
        pytest.fail(
            "Versions changed: regenerate the golden file with ERP_UPDATE_GOLDEN=1 "
            "and review the per-share differences in the diff."
        )
