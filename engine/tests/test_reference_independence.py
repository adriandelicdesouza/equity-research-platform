"""The reference must stay independent of the engine (Blueprint §13.1.1, D26).

Complements the import-linter contract (run by `make lint`) with a direct AST scan, so the
rule is also enforced by `pytest` alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

REFERENCE = Path(__file__).parent / "reference"
FORBIDDEN = ("erp_engine", "erp_backend", "decimal")


def test_reference_never_imports_engine_backend_or_decimal() -> None:
    files = sorted(REFERENCE.glob("*.py"))
    assert files
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN, f"{path.name} imports {name}"
