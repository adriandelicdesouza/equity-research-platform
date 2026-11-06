"""Core: numerics, registry, timeline, trace, validation, determinism, output kinds."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, getcontext, localcontext

import pytest
from engine_fixtures import load, to_inputs
from erp_engine.api import run_valuation
from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import PRECISION, calc, dpow, make_context
from erp_engine.core.registry import Module, execute, resolve
from erp_engine.core.timeline import (
    Convention,
    cash_flow_times,
    discount_factor,
    period_ends,
    year_fraction,
)
from erp_engine.core.validation import Severity, ValidationReport


def test_calc_context_is_isolated_from_caller() -> None:
    @calc
    def third() -> Decimal:
        return Decimal(1) / Decimal(3)

    with localcontext() as ctx:
        ctx.prec = 5
        value = third()
    assert len(str(value)) == PRECISION + 2  # "0." + 34 digits
    assert getcontext().prec != 5


def test_dpow_integral_and_fractional() -> None:
    @calc
    def run() -> tuple[Decimal, Decimal]:
        return dpow(Decimal("1.1"), Decimal(3)), dpow(Decimal(4), Decimal("0.5"))

    integral, root = run()
    assert integral == Decimal("1.331")
    assert root == Decimal(2)
    with pytest.raises(EngineError):
        dpow(Decimal(-1), Decimal("0.5"))


def _mod(name: str, req: set[str], prov: set[str]) -> Module:
    def run(ctx: dict[str, object]) -> None:
        for p in prov:
            ctx[p] = name

    return Module(name, "1", frozenset(req), frozenset(prov), run)


def test_registry_orders_by_dependencies() -> None:
    order = resolve([_mod("c", {"b"}, {"c"}), _mod("a", set(), {"a"}), _mod("b", {"a"}, {"b"})], [])
    assert [m.name for m in order] == ["a", "b", "c"]


def test_registry_rejects_cycles_missing_and_duplicate_providers() -> None:
    with pytest.raises(EngineError, match="circular_dependency"):
        resolve([_mod("x", {"y"}, {"x"}), _mod("y", {"x"}, {"y"})], [])
    with pytest.raises(EngineError, match="missing_dependency"):
        resolve([_mod("x", {"nowhere"}, {"x"})], [])
    with pytest.raises(EngineError, match="duplicate_provider"):
        resolve([_mod("x", set(), {"k"}), _mod("y", set(), {"k"})], [])


def test_registry_enforces_module_contract() -> None:
    broken = Module("liar", "1", frozenset(), frozenset({"promised"}), lambda ctx: None)
    with pytest.raises(EngineError, match="module_contract"):
        execute([broken], {})


@calc
def _df(t: str, rates: list[str], n: int) -> Decimal:
    return discount_factor(Decimal(t), [Decimal(r) for r in rates], period_ends(n, None))


def test_discount_factor_compounds_per_period_rates() -> None:
    with localcontext(make_context()):
        expected = Decimal(1) / (Decimal("1.1") * Decimal("1.2"))
    assert _df("2", ["0.10", "0.20"], 2) == expected
    half = _df("1.5", ["0.10", "0.21"], 2)
    with localcontext(make_context()):
        assert abs(half - Decimal(1) / (Decimal("1.1") * Decimal("1.1"))) < Decimal("1e-30")


def test_timeline_stub_mid_period_times() -> None:
    t = year_fraction(date(2025, 6, 30), date(2025, 12, 31))
    assert cash_flow_times(3, t, Convention.MID_PERIOD) == [
        t / 2,
        t + Decimal("0.5"),
        t + Decimal("1.5"),
    ]
    with pytest.raises(EngineError, match="invalid_horizon"):
        period_ends(21, None)


def test_validation_report_errors_block_save() -> None:
    rep = ValidationReport()
    rep.add("info", Severity.INFO, "x")
    assert not rep.blocks_save
    rep.add("warn", Severity.WARNING, "y", blocks_save=True)
    assert rep.blocks_save and not rep.has_errors


def test_determinism_and_hash_sensitivity() -> None:
    inputs = to_inputs(load("B2"))
    a, b = run_valuation(inputs), run_valuation(inputs)
    assert a.output_hash == b.output_hash and a.input_hash == b.input_hash
    assert a.trace == b.trace
    changed = run_valuation(replace(inputs, discount_rates=(Decimal("0.1000001"),)))
    assert changed.input_hash != a.input_hash and changed.output_hash != a.output_hash


def test_trace_links_price_to_inputs() -> None:
    r = run_valuation(to_inputs(load("B2")))
    price = r.trace["dilution.price"]
    assert "rollforward.equity_v" in price["inputs"]  # type: ignore[operator]
    assert r.trace["rollforward.equity_v"]["inputs"][0] == "bridge.equity_b"  # type: ignore[index]
    assert r.engine_version and r.module_versions["dilution"]


def test_output_kinds_flow_ev_to_equity_to_per_share() -> None:
    r = run_valuation(to_inputs(load("B1")))
    assert (r.primary.kind.value, r.equity_v.kind.value, r.per_share.kind.value) == (
        "enterprise_value",
        "equity_value",
        "per_share_value",
    )
