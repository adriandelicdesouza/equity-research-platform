"""The frontend's generated API types match the backend OpenAPI schema (D28, D61).

``frontend/src/api/schema.gen.ts`` is generated from ``create_app().openapi()``; a change to a
response or request model that is not regenerated (``make api-types``) fails here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "frontend" / "scripts" / "gen_api_types.py"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_api_types", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_frontend_types_are_current() -> None:
    assert _generator().main(["--check"]) == 0, "schema.gen.ts is stale: run `make api-types`"


def test_generator_renders_contract_shapes() -> None:
    text = _generator().render(
        {
            "components": {
                "schemas": {
                    "Cell": {
                        "type": "object",
                        "required": ["value", "tags"],
                        "properties": {
                            "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "kind": {"enum": ["a", "b"]},
                            "extra": {"type": "object", "additionalProperties": True},
                        },
                    },
                    "Role": {"type": "string", "enum": ["admin", "analyst"]},
                }
            }
        }
    )
    assert "export interface Cell {" in text
    assert "  value: string | null;" in text
    assert "  tags: string[];" in text
    assert '  kind?: "a" | "b";' in text
    assert "  extra?: Record<string, unknown>;" in text
    assert 'export type Role = "admin" | "analyst";' in text
