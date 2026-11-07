# Next Session Handoff

**Updated:** 2026-11-07, Session 10 (Claude Code workflow tooling; no application change). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D61, pending P1–P15), `KNOWN_LIMITATIONS.md` (§4c–§4p), `docs/operations/` (UI guide `workflow-ui.md`).

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** M3 phase 3 (seven commits on `c190e87`) was pushed to `origin/m2-ingestion` in Session 9. Session 10 added one workflow-tooling commit (**unpushed**; push needs owner authorization).
- **Uncommitted, for owner review:** `CLAUDE.md` (owner's continuity section merged with new Definition of Done, testing, formatting and Git conventions; full MASTER_BRIEF read now only for new milestones/scope changes). The root transcript `2026-11-07-055858-…txt` is owner-owned and untracked.
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev stack:** stopped at the end of the session. Dev DB at Alembic **`0016`**; `audit.api_actions` empty; SEC data unchanged (6 companies); open material conflicts AAPL 6, MSFT 6, RY 5. All 7 providers disabled; no live provider request has been made.
- **`make check`:** backend 734, engine 109, frontend 62 (Session 9); Session 10 re-ran all but backend (stack down; no backend change).

## Capabilities

| Area | Status |
|---|---|
| SEC pipeline, datasets, conflicts, classification (D42–D53); file imports and listings (D55–D58) | Implemented |
| Data API (D59) / workflow actions API (D60, `0016`) | 24 GET / 16 audited POST; lock + ingestion **pending P15** |
| **Workflow UI (D61)** | Workspace, statement grid + provenance, conflicts, import preview, dataset controls, action history (`workflow-ui.md`) |
| Rates/FX adapters (D58) | Synthetic payloads only; disabled (P9) |

## Outstanding limitations

- **P15 open:** lock and ingestion are unavailable for every role (proposal: lock → admin + analyst, import → admin; two `ROLE_PERMISSIONS` edits + tests; API ingestion also needs the raw-archive mount).
- **UI gaps (§4p):** no views for classification, listings, prices/actions, rates/FX or reference records; no attribution of others' actions; D28 libraries not installed (approval needed); no browser-driver E2E suite.
- **Nothing in M2 feeds valuations yet.** Owner decisions open: P15, P9, P13, P14, P2–P5.

## Next milestone (recommended)

- **A — M3 phase 4:** remaining views over existing endpoints (classification confirmation, listing registration/currency, prices and corporate actions, reference browser); no new dependency.
- **B — M4:** wire stored inputs (Rf, ERP/CRP, betas, spreads as `suggested`) into valuations from a locked dataset; **needs P15** first.

Ask the owner for P15 before either.

## Rules

- `m2-ingestion` only. Commit per stage; push a completed, validated milestone (fast-forward to `origin/m2-ingestion`) **only with the owner's explicit authorization**; never force-push, merge, rebase or rewrite history.
- No provider activation or network requests, and no new dependencies without approval.
- Never on the owner's behalf: lock datasets, confirm classifications/conflicts, import files, register listings, declare currencies. No synthetic data in the dev DB: tests use `erp_test`, and frontend tests mock `fetch`.
- Before each commit, check the staged diff against the `FORBIDDEN` patterns in the backup `sanitize.py` (path in local Claude memory, D48).
- After a backend schema change, run `make api-types`. `make check` now runs `make check-ignored` (the `datasets/` data rule once hid UI code); use `make fmt` before final verification, then re-read formatted files.
- End each milestone or session with `/handoff` (`.claude/skills/handoff/`).

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git fetch && git rev-list --count origin/m2-ingestion..HEAD   # expect 1 until pushed
make up-dev        # background; several minutes; builds the UI into the web image
make check         # expect backend 734, engine 109, frontend 62
make down-dev      # when finished
```

UI `https://127.0.0.1:8444/` (or `npm run dev` → `:5173`); API docs `/api/docs`; CLI `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`; DB `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
