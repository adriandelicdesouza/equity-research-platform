# Next Session Handoff

**Updated:** 2026-11-07, end of Session 5. Authoritative entry point (`CLAUDE.md`). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D57, pending P1–P14), `KNOWN_LIMITATIONS.md` (§4c–§4l), `docs/operations/`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** HEAD is the Session 5 commit `feat(m2): financial-statement import …` on top of `e8cb956`. Working tree clean after it.
- **12 local commits, none pushed.** `origin/m2-ingestion` = `51cd1fa` (last push 2026-11-06). Session 5 added one commit.
- **`main`** = `9ef5594` (M0 + M1). A future merge conflicts on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** The dev DB was checked live this session:
  - Alembic **`0013`**;
  - 6 SEC companies, 88,160 facts; 7 listings, none with a currency;
  - 6 SEC drafts, none locked, all re-verified byte-identically after the refactor;
  - open material conflicts AAPL 6, MSFT 6, RY 5;
  - **0 statement imports**, 0 prices, 0 market imports, 0 registrations.
- **All 7 providers disabled.**
- **`make check`:** backend 499, engine 109, frontend 2, all passing.

## Capabilities

| Area | Status |
|---|---|
| SEC ingestion, mapping v1/v2, datasets, checks, conflicts, classification (D42–D53) | Implemented; tested on real archived SEC data |
| Market-data boundary, Yahoo adapter (D54) | Synthetic payloads only; **disabled; unverified** (P9) |
| CSV price / corporate-action import (D55); manual listings (D56) | Implemented, tested on `erp_test` |
| **Financial-statement CSV import (D57, `0013`)** | Implemented, tested on `erp_test`: `statement-import-validate` → `statement-import --expect-sha256` → draft `reported` dataset (`manual_import` provenance, cross-import/SEC conflicts, D51 checks, verify/lock). Dev: read-only smoke test only. Guide: `docs/operations/financial-statement-import.md` |

## Outstanding limitations

- **Statement import** (§4l): CSV only (P14); no segments, metrics, stubs or FY-end changes; corrections by re-import plus conflict resolution; SEC datasets are not compared with imports.
- **Market data:** Yahoo disabled and unverified; prices, actions and datasets don't feed valuations yet.
- **Other:** `filings.recent` only; extension concepts missing; CLI only (no API/UI for M2).
- **Owner decisions open:** P9 (live provider requests), P13 (file imports with `manual_import` disabled, now covering statements), P14 (XLSX), P2–P5 (deployment).

## Next milestone (recommended): finish M2 offline — rates, FX and reference data

Blueprint §14.1 M2 still lists:

1. **Treasury / Bank of Canada rates and FX:**
   - adapters plus `market.rate_series` / `rate_observations` / `fx_rates`, following the D54 pattern (archive-first, parse separately);
   - seeded **disabled**, tested on synthetic payloads only;
   - first live request needs P9.
2. **Damodaran reference datasets** (ERP, CRP, betas, spreads): versioned `ref.reference_datasets` loaded from an archived file via the D55/D57 validate → `--expect-sha256` pattern.
3. Then **M3** (API + workflow UI over datasets, conflicts and imports).

Reuse `market/tabular.py`, `source.imports`, the archive, `audit.record` and the provider gate. Record the design as D58.

## Rules

- `m2-ingestion` only. **No push, merge, rebase or history rewrite**, no provider activation or network requests, and no new dependencies without approval.
- Don't lock datasets, confirm classifications or conflicts, import statements, register listings, or declare currencies on the owner's behalf. No synthetic data in the dev DB: tests use `erp_test`.
- Before each commit, check the staged diff against the `FORBIDDEN` patterns in the backup `sanitize.py` (path in local Claude memory, D48).
- Run `make check` at each milestone, update this file and `SESSION_LOG.md`, and stop the stack afterwards.

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git rev-list --count origin/m2-ingestion..HEAD   # expect 12
make up-dev        # background; several minutes; migrations to 0013
make check         # expect backend 499, engine 109, frontend 2
make down-dev      # when finished
```

The CLI runs as `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`. Command groups:

- **SEC:** `sec-*`.
- **Datasets:** `dataset-build|list|verify|lock|discard|rebuild` (`--basis standardized|reported`).
- **Conflicts:** `conflict-list|resolve`.
- **Classification:** `classify`, `classification-confirm`.
- **Imports:** `market-import-*`, `statement-import-*`.
- **Listings:** `listing-*`, `listing-set-currency`.

Tooling: `.tools/bin/uv run --all-packages …`. A DB query from the host: `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
