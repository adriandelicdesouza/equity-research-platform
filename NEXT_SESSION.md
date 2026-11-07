# Next Session Handoff

**Updated:** 2026-11-07, Session 15 (CNI real-data acceptance in progress; Session 14: M3 phase 5, D62). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D62, pending P2–P6, P9, P13–P15), `KNOWN_LIMITATIONS.md` (§4c–§4q), `docs/operations/valuation-api.md`, `docs/architecture/valuation-api.md`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** Session 14 commits (stages 1–6 `8c7f50d`, `b5c7b27`, `94e5f9f`, `f4c9184`, `ab87bf6`, then docs + this handoff) pushed fast-forward to `origin/m2-ingestion`, on top of the design commit `611d29e`.
- **Owner-owned, untracked:** root transcript `2026-11-07-055858-…txt` (leave it).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev DB:** Alembic **`0017`**. **Session 15 (owner-approved):** CNI classification confirmed `non_financial` (record 7); **CNI v3 `9dbc995c-…` locked** (SHA `9b99aa2f…0f33162`, irreversible; `dataset-verify` OK after lock). Active project "CNI — real-data acceptance (FY2025 base)" `05339b10-…`, base scenario `d6935c81-…` rev 1 (approved flat FY2025 drivers, settings). **0 runs.** Also the archived Session 13 project. No rate observations, FX, prices or reference datasets; all providers disabled.
- **`make check` (Session 14, stack up):** check-ignored, lint, typecheck, engine **109**, backend **800**, frontend **115** — pass. `make api-types` leaves no diff. Integrated HTTPS check on the rebuilt stack passed (see `SESSION_LOG.md`).

## Capabilities

| Area | Status |
|---|---|
| SEC pipeline, datasets, conflicts, classification, imports, listings, rates/FX/reference (D42–D58) | Implemented |
| Data API (D59) / actions API (D60) / workflow UI (D61) | Implemented; lock + ingestion **pending P15** |
| **Valuation (D62)** | Projects, append-only assumptions, resolver, FCFF/FCFE runs (constant WACC), gates, content idempotency, reproduction, 24 routes, UI with preview, outputs, trace |
| Valuation Phase 5b | **Not started** (needs owner authorization): scenarios, probabilities, sensitivities, assumption impact, regression beta |

## Outstanding

- **CNI acceptance in progress (Session 15, `SESSION_LOG.md`):** FCFF preview resolves all balance-sheet inputs; checks pass. **Blocked only on 8 owner inputs:** `risk_free_rate`, `mature_market_erp`, `beta_method` (+ beta), `cost_of_debt_method` (+ cost), `target_debt_to_equity`, `marginal_tax_rate`, `terminal_growth`, `ronic`; plus valuation date. FCFE not run (owner decision).
- **P15 open** (lock → admin + analyst; import → admin; plus raw-archive mount for API ingestion). **Also open:** P9, P13, P14, P2–P5.
- Phase 5 limitations: `KNOWN_LIMITATIONS.md` §4q (no stub, three lease treatments, basic shares in market weights, dividends B→V not deducted, base scenario only).

## Next milestone (recommended)

1. **Finish CNI acceptance** with the owner's 8 inputs (never invented; P9 blocks fetches): preview → save → idempotent resubmit → reproduce (same output hash) → UI check → docs. Overrides: `BS.INVENTORY` 734M sourced, `BS.ST_INVESTMENTS`/`BS.PREFERRED_EQUITY`/`BS.NCI` not applicable; FY2025 period `deea5d6e-…`.
2. **Phase 5b** once authorized: design §6/§9.5 (scenarios, sensitivities ≤ 100 cells, assumption impact, regression beta; `val.sensitivity_runs`).

Phase 5b acceptance: scenario CRUD with cycle checks and weights summing to 1; WACC × g, WACC × exit multiple, growth × margin grids stored immutably with refused cells marked; tornado; tests and UI; `make check`; docs and handoff.

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
make check         # expect backend 800, engine 109, frontend 115
make down-dev      # when finished
```

UI `https://127.0.0.1:8444/` (or `npm run dev` → `:5173`); API docs `/api/docs`; CLI `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`; DB `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
