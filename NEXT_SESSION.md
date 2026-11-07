# Next Session Handoff

**Updated:** 2026-11-07, end of Session 4. Authoritative entry point (`CLAUDE.md`). Details: `SESSION_LOG.md`, `DECISIONS.md` (D1–D56, pending P1–P14), `KNOWN_LIMITATIONS.md` (§4c–§4k), `docs/operations/`.

## Verified state (2026-11-07)

- **Branch `m2-ingestion`.** HEAD is this handoff commit, on top of `9d540cd`. Working tree clean.
- **11 local commits, none pushed.** `origin/m2-ingestion` = `51cd1fa`, last push 2026-11-06. In order:
  - `0ddb473` publication record;
  - `cccfc04` mapping v1 (D49);
  - `2ecf98f` datasets (D50);
  - `23ff73c` checks/derivations (D51);
  - `0f5ee7f` conflicts (D52);
  - `988f666` classification (D53);
  - `7fb9bf3` market-data boundary (D54);
  - `370255b` handoff;
  - `2e45758` CSV import (D55);
  - `9d540cd` listing registration (D56);
  - this handoff.
- **`main`** = `9ef5594` (M0 + M1). A future merge will conflict on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** The dev DB was last checked at the end of Session 4, and nothing has run since:
  - Alembic **`0012`**;
  - 6 SEC companies, 88,160 facts; 7 listings (from SEC), none with a declared currency;
  - one verified draft dataset per company, none locked;
  - open material conflicts: MSFT 6, RY 5, AAPL 6;
  - 6 classification suggestions, none confirmed;
  - 0 prices, 0 imports, 0 manual registrations.
- **All 7 providers disabled** (`yahoo_chart` and `manual_import` included).
- **Last `make check`** (on `9d540cd`): backend 440, engine 109, frontend 2, all passing.

## Capabilities

| Area | Status |
|---|---|
| SEC ingestion, archive-first provenance (D42–D46) | Implemented; 6 companies ingested live in Session 3 (owner-approved User-Agent) |
| Line-item mapping v1/v2 (D49, D51) | Implemented; tested on real archived SEC facts |
| Versioned datasets: periods, lifecycle, byte-identical rebuilds (D50) | Implemented and tested; drafts exist, **none locked** |
| Integrity checks, derived values (Q4, formulas, LTM, FCF) (D51) | Implemented and tested |
| Conflict detection and audited resolution (D52) | Implemented; open conflicts block locking |
| Financial classification, suggest + confirm (D53) | Implemented; **no confirmations** |
| Market-data provider boundary, split adjustment (D54) | Implemented, tested offline |
| Yahoo chart adapter (D54) | Tested on **synthetic** payloads only; **disabled; live semantics unverified** (P9) |
| Append-only price, corporate-action and listing-snapshot storage (`0010`) | Implemented, tested |
| CSV price and corporate-action import (D55, `0011`) | Implemented, tested on `erp_test`; no real file imported |
| Manual listing registration (D56, `0012`) | Implemented, tested on `erp_test` (RY/XTSE → CSV validation); nothing registered in dev |

## Outstanding limitations (details in KNOWN_LIMITATIONS)

- Yahoo stays disabled and unverified: close basis, adjusted-close method, dividend currency and registered provider symbols (§4i, §4k).
- Market prices, corporate actions and split-adjusted shares **do not feed datasets or valuations**, so the Apple EPS mismatches remain open (§4i).
- **No financial-statement file import** (§4.6).
- `.xlsx` needs an approved dependency (P14). CSV only.
- Other gaps:
  - SEC submissions history is limited to `filings.recent`;
  - extension concepts are absent from companyfacts, so items such as CNQ capex stay missing;
  - no stub periods;
  - classification evidence is balance-sheet only;
  - no correction workflow for imports or registrations;
  - CLI only, with no API or UI for M2 features.
- Owner decisions open:
  - P9: live requests to each provider;
  - P13: imports allowed while `manual_import` is disabled;
  - P14: XLSX;
  - P2–P5: deployment.

## Next milestone: financial-statement import (Blueprint §4.6)

**Implement the working vertical slice. This is not a planning-only task.**

1. **Reconcile the specs first.**
   - §4.6 is a versioned template: rows = standard line-item codes, columns = fiscal periods, plus metadata (company, currency, scale, accounting standard, source document and page). It validates codes, periods, numeric types and scale, runs integrity checks, and creates a `reported` dataset with `provenance_kind = manual_import` linked to the hash-verified file.
   - §4.2 lists `csv_import` / `excel_import` for statements and prices.
   - D16 covers prices only. That part is done (D55).

   Record the result as D57.
2. **Reuse, don't duplicate:**
   - `market/tabular.py` (format boundary; CSV now);
   - `source.imports` + archive + `--expect-sha256` validate-then-ingest (D55);
   - mapping sets / `ref.standard_line_items` for code validation;
   - `standardization/periods.py` for fiscal periods;
   - `checks` / `validation_results`, the dataset lifecycle in `datasets.py` (draft → verify → lock, hashes), conflict detection and resolutions (D52), and audit.
3. **Schema facts to resolve:**
   - `fin.financial_values.provenance_kind` allows only `reported` / `derived` / `analyst` (`0008`), and there is no `import_id`;
   - `financial_datasets.basis` allows `reported` already;
   - datasets require `mapping_version`, `taxonomy`, `facts_as_of` and `input_sha256`. Define their meaning for imports.
4. **Overlap with SEC data:** imported values that overlap SEC data must become §10.3 conflicts, never silent overwrites. Target companies include TSX-only issuers (registered via D56).
5. **Tests:** offline tests on `erp_test` only. No synthetic data in the dev DB.

## Rules

- `m2-ingestion` only. **No push, merge, rebase or history rewrite**, no provider activation or network requests, and no new dependencies without approval.
- Don't lock datasets, confirm classifications or conflicts, register listings, or declare currencies on the owner's behalf.
- Before each commit, check the staged diff against the `FORBIDDEN` patterns in the backup `sanitize.py` (path in local Claude memory, D48).
- Run `make check` at each milestone, update this file and `SESSION_LOG.md`, and stop the stack afterwards.

## Start-up

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -3
git rev-list --count origin/m2-ingestion..HEAD   # expect 11
make up-dev        # background; several minutes; migrations to 0012
make check         # expect backend 440, engine 109, frontend 2
make down-dev      # when finished
```

The CLI runs as `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <cmd>`. Commands:

- **SEC:** `sec-*`.
- **Datasets:** `dataset-build|list|verify|lock|discard|rebuild`.
- **Conflicts:** `conflict-list|resolve`.
- **Classification:** `classify`, `classification-confirm`.
- **Market imports:** `market-import-validate|market-import|market-import-list`.
- **Listings:** `listing-validate|register|list|show`, `listing-set-currency`.

Tooling: `.tools/bin/uv run --all-packages …`. A DB query from the host: `docker exec erp-dev-db-1 psql -U postgres -d erp -Atc "…"`.
