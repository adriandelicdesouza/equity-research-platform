# Next Session Handoff

**Updated:** 2026-11-07, Session 4 (two milestones). Authoritative entry point (`CLAUDE.md`). Details: `SESSION_LOG.md` (chronology), `DECISIONS.md` (D1–D56, pending P1–P14), `KNOWN_LIMITATIONS.md` (§4c–§4k).

## State at close

- **Branch `m2-ingestion`**: HEAD is the `feat(m2): manual listing registration …` commit on top of `2e45758` (CSV import). There are **10 local commits, none pushed** (`origin/m2-ingestion` = `51cd1fa`).
- **`main` @ `9ef5594`** (M0 + M1). A future merge will conflict on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** The DB is at Alembic **`0012`**:
  - SEC data: 6 companies, 88,160 facts; one verified draft dataset per company, **none locked**;
  - open material conflicts: MSFT 6, RY 5, AAPL 6; 1 demonstration resolution;
  - 6 classification suggestions, **none confirmed**;
  - **0 price observations, 0 imports, 0 manual registrations; no listing has a declared trading currency.**
- **All providers disabled** (`yahoo_chart`, `manual_import` included).

## Market data: implemented vs verified

| | Status |
|---|---|
| Provider boundary, Yahoo adapter, split methodology (D54) | Implemented, tested offline on **synthetic** payloads; Yahoo semantics unverified |
| **CSV import** of prices and corporate actions (D55) | **Implemented, tested on `erp_test`** (88 tests); CLI validated read-only against the dev DB; no real file imported yet |
| **Manual listing registration** (D56) | **Implemented, tested on `erp_test`** (44 tests, incl. RY/XTSE → CSV validation); read-only checks run against the dev DB; nothing registered |
| `.xlsx` import | Boundary only (`market/tabular.py`); needs a dependency approval (P14) |
| Prices and splits feeding datasets / valuations | Not wired |

**CSV workflow** (format: `docs/operations/market-data-import.md`), all via `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli …`:
1. `listing-set-currency --ticker T --mic XNAS --currency USD --reason …`. This is an analyst declaration, so **leave it to the owner**.
2. `market-import-validate --template market_prices.v1|corporate_actions.v1 --file - --filename f.csv < f.csv`. Read-only; it prints the SHA-256.
3. `market-import … --source "…" --expect-sha256 <sha> [--allow-partial]`, then `market-import-list`.

## Owner decisions open

- **P13:** file imports run with `manual_import` disabled, because `enabled` gates network retrieval only (D55). Confirm or reverse.
- **P14:** spreadsheet dependency for `.xlsx`.
- **P9:** live validation of each new provider (Yahoo first).

**Listing workflow** (`docs/operations/listing-registration.md`): `listing-validate` → `listing-register` (same arguments) → `listing-list` / `listing-show`. For RY on TSX, use `--same-issuer-as RY:XNYS`. **Registering is an owner action:** nothing was registered on the dev DB.

## Next logical milestones (pick with the owner)

1. **Owner actions, no code:**
   - register `RY` / `XTSE` / CAD;
   - declare the currencies of the US listings;
   - import a first real CSV file.

   This would be the first real (non-synthetic) market data.
2. **Live Yahoo validation** (needs P9):
   - enable `yahoo_chart`;
   - fetch AAPL 2013–2021 and RY.TO;
   - settle the close basis with `infer_close_basis`;
   - compare `recompute_adjusted_close` (flag differences > 0.5%);
   - confirm registered provider symbols (§4.3 step 4);
   - then wire split-adjusted shares into a new dataset format.

   Imported CSV prices can cross-check it: the importer warns when sources differ by more than 0.5%.
3. **Statement import (§4.6):** line-item template → `reported` dataset with `manual_import` provenance, reusing `source.imports` and `tabular.py`. This is the fundamentals path for TSX-only issuers.
4. **Remaining M2:** US Treasury, Bank of Canada, Damodaran (each needs approval), then the M2 exit criteria (§14.1), owner review and the merge to `main`.

## Rules (owner instructions)

- Work only on `m2-ingestion`. **No push, merge, rebase or history rewrite** without explicit approval.
- **No network requests to providers and no provider activation** without approval (P9).
- **No synthetic data in the dev DB.** Tests use `erp_test` only. Don't register listings, declare currencies, lock datasets, or confirm classifications or conflicts on the owner's behalf.
- Keep infrastructure details out of tracked files (D48): run the backup `sanitize.py` `FORBIDDEN` patterns before each commit (path in local Claude memory).
- No new dependencies without approval. Don't change Git identity settings.

## Start-up and validation

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -2
make up-dev        # background; several minutes; applies migrations
make check         # must pass: backend 440, engine 109, frontend 2
make down-dev      # when finished
```

Other commands: `dataset-build|list|verify|lock|rebuild`, `conflict-list|resolve`, `classify`, `classification-confirm`, `sec-standardize-report`. Tooling: `.tools/bin/uv run --all-packages …`; after an intended engine change, run `ERP_UPDATE_GOLDEN=1 make test-engine`.
