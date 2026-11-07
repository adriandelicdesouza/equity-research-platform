# Next Session Handoff

**Updated:** 2026-11-07, end of Session 9. This file is the entry point (`CLAUDE.md`). Details are in:
- `SESSION_LOG.md`;
- `DECISIONS.md` (D1–D61, pending P1–P15);
- `KNOWN_LIMITATIONS.md` (§4c–§4p);
- `docs/operations/` (the UI guide is `workflow-ui.md`).

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** M3 phase 3 is seven commits on top of `c190e87`. It was pushed fast-forward to `origin/m2-ingestion` at the end of Session 9; see `SESSION_LOG.md` for the pushed HEAD.
- **Uncommitted owner edit:** `CLAUDE.md` gained a "Session Continuity and Context Discipline" section during Session 9. It was deliberately left out of the milestone commits; the owner should commit it, or confirm that it should be committed.
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev stack:** stopped at the end of the session. Dev DB at Alembic **`0016`**; `audit.api_actions` empty; SEC data unchanged (6 companies); open material conflicts AAPL 6, MSFT 6, RY 5. All 7 providers disabled; no live provider request has been made.
- **`make check`:** backend 734, engine 109, frontend 62, all passing.

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

- `m2-ingestion` only. Commit per stage; **push only a completed, validated milestone** (fast-forward to `origin/m2-ingestion`); never force-push, merge, rebase or rewrite history.
- No provider activation or network requests, and no new dependencies without approval.
- Don't do any of the following on the owner's behalf:
  - lock datasets;
  - confirm classifications or conflicts;
  - import files;
  - register listings;
  - declare currencies.

  No synthetic data goes in the dev DB: tests use `erp_test`, and frontend tests mock `fetch`.
- Before each commit, check the staged diff against the `FORBIDDEN` patterns in the backup `sanitize.py` (path in local Claude memory, D48).
- After a backend schema change, run `make api-types`. Before pushing, `git ls-files -o -i --exclude-standard` must list no source files (the `datasets/` data rule once hid UI code).

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git fetch && git rev-list --count origin/m2-ingestion..HEAD   # expect 0
make up-dev        # background; several minutes; builds the UI into the web image
make check         # expect backend 734, engine 109, frontend 62
make down-dev      # when finished
```

UI `https://127.0.0.1:8444/` (or `npm run dev` → `:5173`); API docs `/api/docs`; CLI `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`; DB `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
