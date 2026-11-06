"""Calculation module registry and dependency resolution (Blueprint §6.2).

Modules declare the named quantities they require and provide. The registry orders them
topologically (stdlib ``graphlib``); cycles and unprovided requirements are errors. This
operates on named financial quantities between modules — it is not a cell formula engine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from erp_engine.core.errors import EngineError

Context = dict[str, Any]


@dataclass(frozen=True)
class Module:
    name: str
    version: str
    requires: frozenset[str]
    provides: frozenset[str]
    run: Callable[[Context], None]


def resolve(modules: Iterable[Module], available: Iterable[str]) -> list[Module]:
    mods = list(modules)
    have = set(available)
    providers: dict[str, str] = {}
    for m in mods:
        for key in m.provides:
            if key in providers or key in have:
                raise EngineError("duplicate_provider", f"'{key}' is provided more than once.")
            providers[key] = m.name
    graph: dict[str, set[str]] = {}
    for m in mods:
        deps = set()
        for key in m.requires:
            if key in have:
                continue
            if key not in providers:
                raise EngineError("missing_dependency", f"Module '{m.name}' requires '{key}'.")
            deps.add(providers[key])
        graph[m.name] = deps
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise EngineError("circular_dependency", f"Cycle between modules: {exc.args[1]}") from exc
    by_name = {m.name: m for m in mods}
    return [by_name[n] for n in order]


def execute(modules: Iterable[Module], ctx: Context) -> list[Module]:
    ordered = resolve(modules, ctx.keys())
    for m in ordered:
        m.run(ctx)
        missing = [k for k in m.provides if k not in ctx]
        if missing:
            raise EngineError("module_contract", f"Module '{m.name}' did not provide {missing}.")
    return ordered
