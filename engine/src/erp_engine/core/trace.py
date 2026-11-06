"""Calculation trace (Blueprint §6.4): every important number is a node with its inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class TraceNode:
    id: str
    label: str
    value: str
    unit: str
    rule: str
    inputs: tuple[str, ...]
    module: str


@dataclass
class Trace:
    nodes: dict[str, TraceNode] = field(default_factory=dict)

    def record(
        self,
        node_id: str,
        label: str,
        value: Decimal,
        *,
        unit: str,
        rule: str,
        module: str,
        inputs: tuple[str, ...] = (),
    ) -> Decimal:
        self.nodes[node_id] = TraceNode(node_id, label, str(value), unit, rule, inputs, module)
        return value

    def to_dict(self) -> dict[str, dict[str, object]]:
        return {
            k: {
                "label": n.label,
                "value": n.value,
                "unit": n.unit,
                "rule": n.rule,
                "inputs": list(n.inputs),
                "module": n.module,
            }
            for k, n in sorted(self.nodes.items())
        }
