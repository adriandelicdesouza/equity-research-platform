# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3. History: `SESSION_LOG.md`.

## Verified state

- **Architecture:** Revision 2 APPROVED.
  - D27 amended and D35 added on 2026-11-06.
  - `docs/methodology/dcf.md` is **APPROVED** and authoritative for R1 DCF.
- **Git:**
  - `main` @ `0c42321` (M0 merged).
  - Branch `m1-engine` holds the methodology commit and is not merged.
  - **No remote; nothing pushed.**
- **Dev stack `erp-dev`:** may be running, loopback only (8091/8444/5433). `make ps-dev` to check.
- **Engine:** no calculation code yet (`erp_engine` has only its version).

## Decisions needing the owner

1. **Approve two dev-only dependencies** for the independent reference: `mpmath` (high-precision arithmetic) and `import-linter` (enforces that `engine/tests/reference/` never imports `erp_engine`). Both are Python packages, added through `uv` with the existing `exclude-newer` lock. No system changes.
2. **SEC User-Agent contact string:** before M2.
3. **Off-server backup destination:** before production (M5).
4. **Minecraft co-residency test:** deferred to M5. Do not start `minecraft.service`.

## Exact next task (after decision 1)

**Build the independent reference valuation:** `engine/tests/reference/`, Blueprint §13.1.1.

1. Add `mpmath` and `import-linter` to the root `[dependency-groups] dev`. Then run `uv lock` and `make setup`.
2. Add an import-linter contract forbidding `engine/tests/reference` from importing `erp_engine`, and run it in `make lint`.
3. Implement the reference from `docs/methodology/dcf.md` **only**, using a different formulation:
   - backward recursion for discounting;
   - the economic-profit identity check;
   - `Fraction` for integer exponents and `mpmath` (50 digits) for fractional ones.
   - Inputs come from plain JSON fixtures.
4. Add benchmark fixtures B1, B1-VD, B2, B3, B4 (§14), and assert the reference reproduces them:
   - within 1e-9 relative on final values;
   - exactly where a rational value exists.
5. Commit on `m1-engine`.

**Verification:** `make check` green. The import-linter contract fails if a deliberate `import erp_engine` is added to a reference file (try it, then revert). All benchmarks pass.

**After that:** `erp_engine` core modules (Blueprint §6.1, methodology doc), then the policy-guard tests (Blueprint §13.1).

## Prerequisites at startup

```bash
git switch m1-engine && git status --short && git log --oneline -3
make ps-dev || make up-dev     # backend tests need the dev DB
make check
```

Use `make` targets or `.tools/bin/uv run --all-packages …`. Run long jobs in the background.
