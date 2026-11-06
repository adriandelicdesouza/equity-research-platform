# Next Session Handoff

**Updated:** 2026-11-06, end of Session 2. Full history: `SESSION_LOG.md`.

## Verified state at handoff

- **Architecture:** Revision 2 **APPROVED** (2026-11-06). Implementation authorized per the Blueprint §14.1 roadmap.
- **Git:**
  - Branch `m0-foundation` @ `bdc1ac8`: M0 complete except the open items below.
  - `main` @ `4855614`: docs only. **Not merged.**
  - **No remote configured; nothing pushed.**
  - Handoff committed on `m0-foundation` (see `git log`).
- **Dev stack `erp-dev`:** running (`web`, `api`, `db`), loopback only on 8091 (HTTP), 8444 (HTTPS), 5433 (DB).
- **Dev admin:** user `admin`, password in `secrets/dev/initial_admin_password`.
- **Checks:** last `make check` all green (ruff, eslint, mypy --strict, tsc, 29 pytest, 2 vitest).

## Decisions needing the owner

1. **Minecraft co-residency test: DEFERRED to M5** by the owner (2026-11-06). Do not start `minecraft.service`; ask again at M5.
2. **Merge `m0-foundation` into `main`.** Commit of the handoff was authorized; the merge itself has not been explicitly confirmed. Ask first.
3. **SEC User-Agent contact string.** Needed before M2; goes in `.env` only.
4. **Off-server backup destination.** Needed before production (M5 gate), not before development.

## Prerequisites at startup

```bash
cd ~/equity-research-platform
git status --short && git log --oneline -3 --all   # expect handoff commit, bdc1ac8, 4855614, a7b6556
make ps-dev || make up-dev                         # web/api/db healthy
make check                                         # all green
```

Use `make` targets or `.tools/bin/uv run --all-packages …`; plain `uv run` removes the workspace packages. Run long jobs in the background.

## Exact next task

**Merge M0 (with confirmation), then start M1.**

1. M0 is closed (Minecraft test deferred to M5). Confirm the merge of `m0-foundation` into `main`, then create branch `m1-engine`.
2. (Former step, now at M5) Minecraft co-residency: `make measure-memory` idle and under `loadgen.py`, with Minecraft running. Pass: no OOM kill, host MemAvailable > 500 MiB, no sustained swap growth.
3. M1, step 1: write `docs/methodology/dcf.md`. Cover:
   - discounting conventions;
   - stub Modes A/B and roll-forward (Blueprint §7.4);
   - FCFF and FCFE (FCFE has no bridge, §7.3);
   - terminal value and coherence checks (§7.6);
   - WACC with ERP/CRP and beta (§7.5);
   - leases (§7.7.1); dilution (§7.7.2); output kinds (§6.6).
   Include at least 3 hand-worked benchmark examples with every intermediate number.
4. M1, step 2: independent reference in `engine/tests/reference/` (`Fraction`/`mpmath`, backward recursion, economic-profit identity). It must not import `erp_engine`; add an import-linter contract. See Blueprint §13.1.1.
5. M1, step 3: `erp_engine` modules (Blueprint §6.1), then the policy-guard tests (§13.1).

## Verification criteria for M1, step 1

- Every convention in the methodology doc cites its Blueprint section.
- Benchmarks are computed independently of any code.
- No engine code exists before the reference spec.
