# Next Session Handoff

**Updated:** 2026-11-07, Session 15 (CNI acceptance pushed; Phase 5b scenarios + sensitivities, D63). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D63, pending P2–P6, P9, P13–P15), `KNOWN_LIMITATIONS.md` (§4c–§4r), `docs/operations/valuation-api.md`, `docs/architecture/valuation-api.md`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** CNI acceptance (`c73c7d2`, `6c37343`, `af6d3d9`) pushed fast-forward (owner-authorized). **Phase 5b: 3 local commits ahead of `origin`** (`d06b453` backend, `82a53c9` UI, docs + handoff) — **not pushed; awaits owner authorization.**
- **Owner-owned, untracked:** root transcript `2026-11-07-055858-…txt` (leave it).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev DB:** Alembic **`0018`** (`val.sensitivity_runs`). **Session 15 (owner-approved):** CNI classification confirmed `non_financial` (record 7); **CNI v3 `9dbc995c-…` locked** (SHA `9b99aa2f…0f33162`, irreversible). Project "CNI — real-data acceptance (FY2025 base)" `05339b10-…`, base scenario `d6935c81-…` rev 3 (no other scenarios); **1 run**, 1 WACC × g grid (25 cells). Also the archived Session 13 project. No rate observations, FX, prices or reference datasets; all providers disabled.
- **`make check` (Session 15, stack up):** check-ignored, lint, typecheck, engine **109**, backend **818**, frontend **119** — pass; `make api-types` no diff. UI checks on the rebuilt stack pass (`SESSION_LOG.md`).

## Capabilities

| Area | Status |
|---|---|
| SEC pipeline, datasets, conflicts, classification, imports, listings, rates/FX/reference (D42–D58) | Implemented |
| Data API (D59) / actions API (D60) / workflow UI (D61) | Implemented; lock + ingestion **pending P15** |
| **Valuation (D62)** | Projects, append-only assumptions, resolver, FCFF/FCFE runs (constant WACC), gates, content idempotency, reproduction, UI with preview, outputs, trace |
| **Phase 5b (D63)** | Scenarios (inheritance, cycle checks, archive), probabilities + weighted summary, one-/two-way/tornado sensitivity grids (immutable, ≤ 100 cells), 34 routes, UI tabs. Open: assumption impact, regression beta (P9) |

## Outstanding

- **CNI real-data acceptance: complete and pushed.** Run `9e3f5853-…` (CAD 115.99/share, output `0e15ac8c…`) still resubmits as existing and reproduces after Phase 5b. CNI has no bull/bear scenarios: their assumptions are owner decisions.
- **Phase 5b complete locally** (acceptance below met); push needs the owner's authorization.
- **P15 open** (lock → admin + analyst; import → admin; plus raw-archive mount for API ingestion). **Also open:** P9, P13, P14, P2–P5.
- Limitations: §4q (no stub, three lease treatments, dividends B→V) and §4r (single-parent inheritance, latest-run weighting, sensitivities hold RONIC at the run's value, 28-digit WACC in outputs).

## Next milestone (recommended): M4 completion — exports and audit history

Why first: the last Release 1 items (MASTER_BRIEF §22: Excel-compatible exports, basic audit history UI); no P9/P15 dependency.
1. **Exports (CSV, Excel-compatible, no new dependency):** run (outputs, flows, WACC build-up, bridge, provenance lineage, engine/module versions, hashes, output kinds) and sensitivity grids, from stored server results only (§21); `GET` routes, UI download buttons. **Owner decision:** approve a spreadsheet library (e.g. `openpyxl`) for native `.xlsx`, or keep CSV.
2. **Assumption/audit history UI:** per scenario and project, previous → new value, user, time, reason (§17.2); `valuation_impact` remains open.

Acceptance: exports reproduce stored values exactly with source attribution and engine version; tests (backend + vitest); `make check`; docs; handoff.

## Rules

- `m2-ingestion` only. Commit per stage; push a completed, validated milestone (fast-forward to `origin/m2-ingestion`) **only with the owner's explicit authorization**; never force-push, merge, rebase or rewrite history.
- No provider activation or network requests, and no new dependencies without approval.
- Never on the owner's behalf: lock datasets, confirm classifications/conflicts, import files, register listings, declare currencies — unless a brief explicitly authorizes it. No synthetic data in the dev DB: tests use `erp_test`, and frontend tests mock `fetch`.
- After a backend schema change, run `make api-types`. Commit, formatting and handoff conventions: `CLAUDE.md` (`/handoff`).

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git fetch && git rev-list --left-right --count origin/m2-ingestion...HEAD   # expect 0 0
make up-dev        # background; several minutes; builds the UI into the web image
make check         # expect backend 818, engine 109, frontend 119
make down-dev      # when finished
```

UI `https://127.0.0.1:8444/` (or `npm run dev` → `:5173`); API docs `/api/docs`; CLI `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`; DB `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
