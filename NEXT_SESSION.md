# Next Session Handoff

**Updated:** 2026-11-07, Session 13 (M3 phase 5: valuation API **design only**). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D61, pending P1–P16), `KNOWN_LIMITATIONS.md` (§4c–§4q), `docs/architecture/valuation-api.md` (proposed), `docs/operations/`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** `origin/m2-ingestion` = `771bb0b` (rates & FX views). Session 13 added **one local, unpushed commit**: the valuation API design + this handoff (docs only). Do not push it as a milestone.
- **Owner-owned, untracked:** root transcript `2026-11-07-055858-…txt` (leave it).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev DB (Session 12 state; not re-checked, stack down):** Alembic **`0016`**; `audit.api_actions` empty; 6 companies, 7 listings, 19 registered rate series; no rate observations, FX rates, prices or reference datasets. All 7 providers disabled; no live provider request made.
- **Session 13 (docs only, stack down):** check-ignored, lint, typecheck, engine **109**, frontend **105** pass; backend not run (no backend change). Last full `make check`: Session 12, backend **734**.

## Capabilities

| Area | Status |
|---|---|
| SEC pipeline, datasets, conflicts, classification (D42–D53); file imports and listings (D55–D58) | Implemented |
| Data API (D59) / workflow actions API (D60, `0016`) | 24 GET / 16 audited POST; lock + ingestion **pending P15** |
| **Workflow UI (D61)** | Workspace, statements, conflicts, datasets, imports, actions, classification, listings, reference data, rates & FX (read-only) |
| Valuation workflow (project → assumptions → DCF → trace) | **Designed, not approved (P16)**: `docs/architecture/valuation-api.md`; no API, schema or UI; engine (M1) standalone |

## Outstanding limitations

- **P15 open:** lock and ingestion are unavailable for every role (proposal: lock → admin + analyst, import → admin; two `ROLE_PERMISSIONS` edits + tests; API ingestion also needs the raw-archive mount).
- **UI (§4p):** empty rates/FX until a first fetch (P9); tables only (D28 libraries unapproved); no Playwright suite.
- **Valuation permissions:** `valuation:read/write` are already mapped in `rbac.py` since M0 but unused (design Q2).
- **Owner decisions open:** **P16**, P15, P9, P13, P14, P2–P5.

## Next milestone (recommended)

**M3 phase 5 implementation is blocked only by P16** (owner approval of `docs/architecture/valuation-api.md`, answering Q1–Q12 in its §12). Do not start migrations, routes or UI before it. P15, P9 and the raw-archive mount do not block implementation; they block use on real data (a locked dataset in-app, stored Rf/FX/prices, in-app imports).

Once P16 is approved, follow the design's §13 in stages (commit each):
migration `0017` + blocks → project/assumption services → input resolver → run service → API (§9, `make api-types`) → UI (D61 rules) → docs, decision record replacing P16.

**Acceptance:** endpoints with D60 conventions, migration and tests; runs refused for unlocked datasets, `needs_review`/blocked classifications, unapproved assumptions and look-ahead inputs; a stored run reproduces to an identical output hash; UI project → assumptions → DCF → trace with vitest; `make check` green; docs and handoff.

If P16 is still open: owner decisions only (P16, P15); no substantial unblocked engineering remains on existing endpoints.

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
