# Next Session Handoff

**Updated:** 2026-11-07, end of Session 8. This file is the entry point (`CLAUDE.md`). Details are in:
- `SESSION_LOG.md`;
- `DECISIONS.md` (D1–D60, pending P1–P15);
- `KNOWN_LIMITATIONS.md` (§4c–§4o);
- `docs/operations/`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** HEAD is the Session 8 stage-6 commit (`test/docs(m3): workflow route sweep …`); M3 phase 2 is six commits on top of `d179831`.
- **Pushed:** the completed M3 phase 2 milestone was pushed to `origin/m2-ingestion` (fast-forward) at the end of Session 8; local and remote are equal (see `SESSION_LOG.md`).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** Dev DB (checked live): Alembic **`0016`** (`audit.api_actions` empty); 19 rate series; 0 rate/FX observations, reference datasets or imports; SEC data unchanged (6 companies, 88,160 facts); open material conflicts AAPL 6, MSFT 6, RY 5.
- **All 7 providers disabled.** No live request has been made to Treasury, the Bank of Canada or Damodaran.
- **`make check`:** backend 732, engine 109, frontend 2, all passing.

## Capabilities

| Area | Status |
|---|---|
| SEC ingestion, mapping, datasets, checks, conflicts, classification (D42–D53) | Implemented; tested on real archived SEC data |
| Yahoo adapter (D54) | Synthetic payloads only; **disabled; unverified** (P9) |
| CSV price/action import (D55); listings (D56); statement import (D57) | Implemented, tested on `erp_test` |
| **Treasury par yields, GoC yields, BoC FX (D58, `0014`)** | Adapters + append-only storage + CLI; synthetic payloads only; **disabled** (P9) |
| **Workflow actions API (D60, `0016`)** | 16 audited, CSRF-protected POST actions + action log/idempotency; lock and ingestion **pending P15** (403 for every role). Guide: `docs/operations/workflow-api.md` |
| **Read-only data API (D59)** | 24 GET routes under `/api` (`company:read`); READ ONLY transactions + ORM guards. Guide: `docs/operations/data-api.md` |
| **Damodaran ERP/CRP/betas/spreads (D58, `0015`)** | Versioned CSV import (validate → `--expect-sha256` → `--supersede --reason`). Guide: `docs/operations/reference-data-import.md` |

## Outstanding limitations

- **Rates/FX:** no live data (formats not re-verified); no scheduler, FRED fallback or FX conversion.
- **Damodaran:** CSV transcription only (XLSX needs P14); industries are not mapped to the platform classification.
- **Nothing in M2 feeds valuations yet.** Rf, ERP, CRP, betas, spreads, prices and imported datasets are stored, not wired.
- **API:** cookie sessions only; lock/import await P15 (and API ingestion needs the raw-archive mount); actions are synchronous; no UI yet (§4n, §4o).
- **Owner decisions open:** **P15** (who may lock datasets / ingest files; proposal in the workflow guide), P9 (live provider requests), P13 (file-import gate), P14 (XLSX), P2–P5 (deployment).

## Next milestone (recommended): M3 phase 3 — workflow UI

React/TypeScript views (D28; types generated from `/api/openapi.json`) over the read API and the workflow actions:
- company search → workspace (listings, classification, datasets);
- financial statements grid with provenance drill-down;
- conflicts with candidate comparison and the resolution form → rebuild;
- import preview → confirm (ingest stays disabled until P15);
- action history.

Before or alongside it: the owner's P15 decision (two `ROLE_PERMISSIONS` edits + tests; the API raw-archive mount for ingestion). Record the UI design as D61. Commit per stage; push the completed milestone.

## Rules

- `m2-ingestion` only. Commit per stage; **push only a completed, validated milestone** (fast-forward to `origin/m2-ingestion`); never force-push, merge, rebase or rewrite history. No provider activation or network requests, and no new dependencies without approval.
- Don't do any of the following on the owner's behalf:
  - lock datasets;
  - confirm classifications or conflicts;
  - import files;
  - register listings;
  - declare currencies.

  No synthetic data goes in the dev DB: tests use `erp_test`.
- Before each commit, check the staged diff against the `FORBIDDEN` patterns in the backup `sanitize.py` (path in local Claude memory, D48).
- Run `make check` at each milestone, update this file and `SESSION_LOG.md`, and stop the stack afterwards.

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git rev-list --count origin/m2-ingestion..HEAD   # expect 0 after the Session 8 push
make up-dev        # background; several minutes; migrations to 0016
make check         # expect backend 732, engine 109, frontend 2
make down-dev      # when finished
```

The CLI runs as `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`. Command groups:
- **SEC:** `sec-*`.
- **Datasets and conflicts:** `dataset-*`, `conflict-*`.
- **Classification:** `classify*`.
- **Imports:** `market-import-*`, `statement-import-*`, `reference-import*`, `reference-dataset-*`.
- **Listings:** `listing-*`.
- **Rates and FX:** `rates-*`, `rate-series-list`, `fx-show`.

API: `https://127.0.0.1:8444/api/docs` (dev; login required). DB from the host: `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
