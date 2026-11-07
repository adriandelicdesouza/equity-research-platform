# Next Session Handoff

**Updated:** 2026-11-07, end of Session 7. This file is the entry point (`CLAUDE.md`). Details are in:
- `SESSION_LOG.md`;
- `DECISIONS.md` (D1–D59, pending P1–P14);
- `KNOWN_LIMITATIONS.md` (§4c–§4n);
- `docs/operations/`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** HEAD is the Session 7 commit `feat(m3): read-only data API …` on top of `4742cba`. The working tree is clean after it.
- **14 local commits, none pushed.** `origin/m2-ingestion` = `51cd1fa` (last push 2026-11-06).
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** Dev DB (checked live): Alembic **`0015`**; 19 rate series; 0 rate/FX observations, reference datasets or imports; SEC data unchanged (6 companies, 88,160 facts); open material conflicts AAPL 6, MSFT 6, RY 5.
- **All 7 providers disabled.** No live request has been made to Treasury, the Bank of Canada or Damodaran.
- **`make check`:** backend 644, engine 109, frontend 2, all passing.
- **No migration in Session 7.** The dev DB was unchanged by the API smoke test (row counts identical).

## Capabilities

| Area | Status |
|---|---|
| SEC ingestion, mapping, datasets, checks, conflicts, classification (D42–D53) | Implemented; tested on real archived SEC data |
| Yahoo adapter (D54) | Synthetic payloads only; **disabled; unverified** (P9) |
| CSV price/action import (D55); listings (D56); statement import (D57) | Implemented, tested on `erp_test` |
| **Treasury par yields, GoC yields, BoC FX (D58, `0014`)** | Adapters + append-only storage + CLI; synthetic payloads only; **disabled** (P9) |
| **Read-only data API (D59)** | 24 GET routes under `/api` (`company:read`); READ ONLY transactions + ORM guards. Guide: `docs/operations/data-api.md` |
| **Damodaran ERP/CRP/betas/spreads (D58, `0015`)** | Versioned CSV import (validate → `--expect-sha256` → `--supersede --reason`). Guide: `docs/operations/reference-data-import.md` |

## Outstanding limitations

- **Rates/FX:** no live data (formats not re-verified); no scheduler, FRED fallback or FX conversion.
- **Damodaran:** CSV transcription only (XLSX needs P14); industries are not mapped to the platform classification.
- **Nothing in M2 feeds valuations yet.** Rf, ERP, CRP, betas, spreads, prices and imported datasets are stored, not wired.
- **API is read-only; no workflow actions or UI yet.** Cookie sessions only (no API tokens); no dedicated read-only DB role (§4n).
- **Owner decisions open:** P9 (live provider requests), P13 (file-import gate, incl. `damodaran`), P14 (XLSX), P2–P5 (deployment).

## Next milestone (recommended): M3 phase 2 — workflow actions API

Expose the existing service functions as authenticated, CSRF-protected, audited POST endpoints, with no new business logic:
- dataset build, rebuild, verify (async job), lock and discard;
- conflict resolution;
- classification confirmation;
- import validate (read-only preview) → ingest with `expect_sha256`;
- listing registration and currency declaration.

Each action keeps the CLI's refusals and reason requirements. Uploads must be size-limited and must not expose archive paths.

Permissions to decide (record as D60):
- `company:write` for analysts;
- `data:manage` for imports;
- whether `admin` alone may lock datasets.

Then **M3 phase 3:** the workflow UI (company → financials → conflicts → DCF → trace), generating its types from OpenAPI (D28).

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
git rev-list --count origin/m2-ingestion..HEAD   # expect 14
make up-dev        # background; several minutes; migrations to 0015
make check         # expect backend 644, engine 109, frontend 2
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
