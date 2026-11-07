# Next Session Handoff

**Updated:** 2026-11-07, Session 12 (M3: read-only rates & FX views). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D61, pending P1–P15), `KNOWN_LIMITATIONS.md` (§4c–§4p), `docs/operations/` (UI guide `workflow-ui.md`).

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** Rates & FX views (`82aa6c1` + this handoff) pushed fast-forward to `origin/m2-ingestion` at the end of Session 12, on top of M3 phase 4 (`56ca96c`).
- **Owner-owned, untracked:** root transcript `2026-11-07-055858-…txt` (leave it).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev DB:** Alembic **`0016`**; `audit.api_actions` empty; 6 companies, 7 listings, 19 registered rate series; no rate observations, FX rates, prices or reference datasets. All 7 providers disabled; no live provider request made.
- **`make check` (Session 12, stack up):** check-ignored, lint, typecheck, engine **109**, backend **734**, frontend **105** — pass. `make api-types` leaves no diff. Fresh clone of the final code: `npm ci`, tsc, ESLint, 105 tests, build pass.

## Capabilities

| Area | Status |
|---|---|
| SEC pipeline, datasets, conflicts, classification (D42–D53); file imports and listings (D55–D58) | Implemented |
| Data API (D59) / workflow actions API (D60, `0016`) | 24 GET / 16 audited POST; lock + ingestion **pending P15** |
| **Workflow UI (D61)** | Workspace, statements + provenance, conflicts, datasets, imports, action history; **phase 4:** classification confirmation, listings (prices, corporate actions, trading currency, registration), reference data; **rates & FX** (series, history, revisions, stored FX direction; read-only) |
| Valuation workflow (project → assumptions → DCF → trace) | **Not started**: no valuation API exists; engine (M1) is standalone |

## Outstanding limitations

- **P15 open:** lock and ingestion are unavailable for every role (proposal: lock → admin + analyst, import → admin; two `ROLE_PERMISSIONS` edits + tests; API ingestion also needs the raw-archive mount).
- **UI (§4p):** rates/FX views show empty states until a first fetch is approved (P9); tables only (D28 libraries unapproved); no Playwright E2E suite; no attribution of others' actions.
- **Owner decisions open:** P15, P9, P13, P14, P2–P5.

## Next milestone (recommended)

No substantial UI work remains on existing endpoints. **M3 phase 5 (valuation workflow: project → assumptions → DCF → trace, Blueprint §14.1) is not ready.** Prerequisites, in order:
1. **Owner decides P15** (proposal: lock → admin + analyst; import → admin).
2. **Implement P15** (small): two `ROLE_PERMISSIONS` grants + tests; raw-archive mount on the API container; docs (§4o/§4p). The UI enables itself from `/auth/me`.
3. **Valuation API design** (proposed decision, owner approval): project/run model; inputs from one locked dataset plus stored Rf/ERP/CRP/beta/spread references (`suggested` until approved); percentage points → fractions at the input boundary; immutable, hashed runs; classification blocks (e.g. `bank`); `valuation:read/write`.

Phase 5 acceptance: endpoints (D60 conventions, migration, tests); reproducible runs refused for unlocked datasets and blocked classifications; UI project, assumptions, DCF output, trace inspector with vitest; `make check` green; docs, handoff.

## Rules

- `m2-ingestion` only. Commit per stage; push a completed, validated milestone (fast-forward to `origin/m2-ingestion`) **only with the owner's explicit authorization**; never force-push, merge, rebase or rewrite history.
- No provider activation or network requests, and no new dependencies without approval.
- Never on the owner's behalf: lock datasets, confirm classifications/conflicts, import files, register listings, declare currencies. No synthetic data in the dev DB: tests use `erp_test`, and frontend tests mock `fetch`.
- After a backend schema change, run `make api-types`. Commit, formatting and handoff conventions: `CLAUDE.md` (`/handoff`).

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git fetch && git rev-list --left-right --count origin/m2-ingestion...HEAD   # expect 0 0
make up-dev        # background; several minutes; builds the UI into the web image
make check         # expect backend 734, engine 109, frontend 105
make down-dev      # when finished
```

UI `https://127.0.0.1:8444/` (or `npm run dev` → `:5173`); API docs `/api/docs`; CLI `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`; DB `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
