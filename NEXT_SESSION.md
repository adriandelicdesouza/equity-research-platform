# Next Session Handoff

**Updated:** 2026-11-07, end of Session 6. This file is the entry point (`CLAUDE.md`). Details are in:
- `SESSION_LOG.md`;
- `DECISIONS.md` (D1–D58, pending P1–P14);
- `KNOWN_LIMITATIONS.md` (§4c–§4m);
- `docs/operations/`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** HEAD is the Session 6 commit `feat(m2): rates, FX and Damodaran reference datasets …` on top of `c1d0d91`. The working tree is clean after it.
- **13 local commits, none pushed.** `origin/m2-ingestion` = `51cd1fa` (last push 2026-11-06).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** The dev DB was checked live:
  - Alembic **`0015`**;
  - 19 rate series seeded;
  - 0 rate/FX observations, 0 reference datasets, 0 imports;
  - SEC data unchanged (6 companies, 88,160 facts);
  - open material conflicts AAPL 6, MSFT 6, RY 5.
- **All 7 providers disabled.** No live request has been made to Treasury, the Bank of Canada or Damodaran.
- **`make check`:** backend 609, engine 109, frontend 2, all passing.

## Capabilities

| Area | Status |
|---|---|
| SEC ingestion, mapping, datasets, checks, conflicts, classification (D42–D53) | Implemented; tested on real archived SEC data |
| Yahoo adapter (D54) | Synthetic payloads only; **disabled; unverified** (P9) |
| CSV price/action import (D55); listings (D56); statement import (D57) | Implemented, tested on `erp_test` |
| **Treasury par yields, GoC yields, BoC FX (D58, `0014`)** | Adapters + append-only storage + CLI; synthetic payloads only; **disabled** (P9) |
| **Damodaran ERP/CRP/betas/spreads (D58, `0015`)** | Versioned CSV import (validate → `--expect-sha256` → `--supersede --reason`). Guide: `docs/operations/reference-data-import.md` |

## Outstanding limitations

- **Rates/FX:**
  - no live data, and the provider formats are not re-verified;
  - no scheduler, no FRED fallback, no FX conversion or inversion.
- **Damodaran:** CSV transcription only (XLSX needs P14); industries are not mapped to the platform classification.
- **Nothing in M2 feeds valuations yet.** Rf, ERP, CRP, betas, spreads, prices and imported datasets are stored, not wired.
- **CLI only** (no API or UI for M2).
- **Owner decisions open:**
  - P9: live provider requests;
  - P13: the file-import gate, now also `damodaran`;
  - P14: XLSX;
  - P2–P5: deployment.

## Next milestone (recommended): M3 — API and workflow UI over M2 data

M2's offline scope (Blueprint §14.1) is now built. The rest of M2 depends on owner decisions: live validation of Yahoo, Treasury and BoC after P9, and real imports.

Recommended M3 scope:
1. Read-only FastAPI endpoints with RBAC for companies and listings, datasets, conflicts, imports, rate series/FX and reference datasets.
2. Then the workflow actions (validate/ingest, conflict resolution, dataset lock) behind the existing service functions.
3. Then the UI views.

Record the API design as D59.

Alternative if the owner approves P9: one supervised live request per provider to verify the formats before M3.

## Rules

- `m2-ingestion` only. **No push, merge, rebase or history rewrite.** No provider activation or network requests, and no new dependencies without approval.
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
git rev-list --count origin/m2-ingestion..HEAD   # expect 13
make up-dev        # background; several minutes; migrations to 0015
make check         # expect backend 609, engine 109, frontend 2
make down-dev      # when finished
```

The CLI runs as `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`. Command groups:
- **SEC:** `sec-*`.
- **Datasets and conflicts:** `dataset-*`, `conflict-*`.
- **Classification:** `classify*`.
- **Imports:** `market-import-*`, `statement-import-*`, `reference-import*`, `reference-dataset-*`.
- **Listings:** `listing-*`.
- **Rates and FX:** `rates-*`, `rate-series-list`, `fx-show`.

DB from the host: `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
