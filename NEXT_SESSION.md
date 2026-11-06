# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3. History: `SESSION_LOG.md`.

## Verified state

- **Architecture:** Revision 2 APPROVED (D27 amended, D35 added).
- **Methodology:** `docs/methodology/dcf.md` APPROVED and authoritative.
- **Git:**
  - `main` @ `0c42321` (M0).
  - `m1-engine` holds the methodology commit and the **reference-valuation commit**; not merged.
  - **No remote; nothing pushed.**
- **Independent reference:** done. It lives in `engine/tests/reference/` and reproduces benchmarks B1, B1-VD, B2, B3 and B4.
  - Independence is enforced by import-linter (`make lint`) and an AST test.
- **Engine:** `erp_engine` still has no calculation code.
- **Dev stack `erp-dev`:** running, loopback only. Backend tests need it.

## Decisions needing the owner

1. **SEC User-Agent contact string:** before M2.
2. **Off-server backup destination:** before production (M5).
3. **Minecraft co-residency test:** deferred to M5. Do not start `minecraft.service`.

Nothing blocks the next task.

## Exact next task

**M1, step 3: `erp_engine` core and the FCFF/FCFE DCF**, implemented from the methodology document (Blueprint §6.1–6.6). The engine must not read the reference code.

1. `erp_engine/core/`:
   - `numbers` (Decimal context: precision 34, `ROUND_HALF_EVEN`);
   - `units` (currency, scale);
   - `periods`/timeline (Actual/365.25, stub);
   - `series`;
   - `trace` (`TraceNode`);
   - `validation` (Check, Severity);
   - `registry` (graphlib DAG, cycle detection);
   - `errors`;
   - `version`.
2. `outputs.py`: `OutputKind` and `ValuationOutput` (§6.6, D18).
3. `forecast/`, `dcf/` (fcff, fcfe, discounting with a cumulative DF, stub A/B, terminal with D27/D35, leases), `bridge/` (EV only), `dilution/` (TSM fixed point), `wacc/` (ERP/CRP, beta, Kd, weights).
4. Tests:
   - engine vs reference within 1e-12 relative (exact-arithmetic cases) and 1e-10 (transcendental), on the benchmark fixtures and on Hypothesis inputs;
   - the policy-guard tests from Blueprint §13.1;
   - determinism (identical output hash).
5. Commit on `m1-engine` at meaningful checkpoints (core → DCF → WACC → guards).

**Verification:**
- `make check` green.
- Engine matches every benchmark.
- Each policy guard has a failing-input test.
- `mypy --strict` clean on `erp_engine`.

## Prerequisites at startup

```bash
git switch m1-engine && git status --short && git log --oneline -4
make ps-dev || make up-dev
make check
```

Use `make` targets or `.tools/bin/uv run --all-packages …`. Run long jobs in the background. No new dependencies are expected for step 3; ask first if any are needed.
