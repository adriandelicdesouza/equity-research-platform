# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3. Authoritative entry point (`CLAUDE.md`). Details: `SESSION_LOG.md` (chronology), `DECISIONS.md` (D1–D54), `KNOWN_LIMITATIONS.md` (§4c–§4i).

## State at close (verified 2026-11-06)

- **Branch `m2-ingestion` @ `7fb9bf3`**, working tree clean. **7 local commits, none pushed** (`origin/m2-ingestion` = `51cd1fa`):
  - `0ddb473` docs: publication record
  - `cccfc04` mapping v1 (D49)
  - `2ecf98f` versioned datasets (D50)
  - `23ff73c` integrity checks + derivations, mapping v2 (D51)
  - `0f5ee7f` data conflicts (D52)
  - `988f666` financial classification (D53)
  - `7fb9bf3` market-data boundary + offline Yahoo adapter (D54)
- **`main` @ `9ef5594`** (M0 + M1) is the GitHub default branch. A future merge will conflict on `README.md`: keep the `m2-ingestion` version.
- **Dev stack stopped.** The DB (`data/dev/`) is at Alembic **`0010`**:
  - 6 SEC companies, 88,160 facts;
  - one verified format-3 draft per company (MSFT v4, others v3; 13 earlier versions discarded); **none locked**;
  - open material conflicts: MSFT 6, RY 5, AAPL 6;
  - 1 demonstration resolution (MSFT FY2016 equity);
  - 6 classification suggestions (RY → bank, others → non_financial), **none confirmed**;
  - 0 price observations.
- **Providers in `source.providers`: all disabled, including `yahoo_chart`.** SEC uses its own User-Agent gate (`.env.dev`, worker only; never print or commit it).

## Market-data milestone (D54): what is and isn't established

| | Status |
|---|---|
| Provider-neutral types and protocol, `market/{model,adjust,store}.py` | Implemented, tested offline |
| Yahoo adapter: symbol rule, request builder, gated client, parser | Implemented, tested on **synthetic** payloads only |
| Close basis, `adjclose` method, dividend currency for Yahoo | **Unverified.** Stored as `unverified` / `assumed_quote_currency` |
| Split methodology (basis = filing date; Π factors over basis < ex ≤ reference) | Implemented; Apple EPS regression on fixture values only |
| Split-adjusted shares in dataset builds and EPS checks | **Not wired**; Apple EPS mismatches remain open |
| Migration `0010` (append-only price observations, corporate actions, listings) | Applied in dev; tables empty |

**Live Yahoo validation needs owner approval (P9) first.** Then: enable `yahoo_chart`, fetch AAPL 2013–2021 and RY.TO (optionally SHOP / SHOP.TO), settle the close basis with `infer_close_basis`, compare `recompute_adjusted_close` (0.5% flag), and only then wire split-adjusted shares into a new dataset format.

## Next milestone: Excel/CSV import (Blueprint §4.6)

**Read first:** Blueprint §4.6, §4.2 (`excel_import` / `csv_import`: "statements and prices from a documented template"), §3.3 (`source.imports`), §10.1 (provenance), D16 (manual CSV price import as the Yahoo fallback), D54, and `erp_backend/market/`. Note that §4.6 specifies mainly a **statement** template (line-item codes × fiscal periods, plus a metadata sheet). Settle the scope for market data (prices and corporate actions) against it before designing anything.

**Facts already checked:**
- `source.imports` does not exist yet (the blueprint defines it).
- A `manual_import` provider row exists, disabled.
- No spreadsheet library is in `uv.lock`. CSV works with the standard library; **`.xlsx` needs a new dependency, which needs owner approval**.

**Scope for a vertical slice:**
- documented, versioned template(s) and column mapping (`templates/`);
- listing identification (security / ticker + MIC);
- dates and time zones, currency, and **explicit close basis** (raw / split-adjusted / adjusted close, never substituted);
- corporate actions where the template supports them;
- row-level validation and rejection reasons; ambiguous rows rejected;
- the original file stored hash-verified, with provenance on every row;
- duplicate detection and idempotency (reuse the `0010` unique indexes);
- a **preview / validate step before any permanent write**;
- persistence through `market/store.py` (a `MarketDataProvider`-style parser);
- offline unit tests and DB integration tests (`erp_test` only; no synthetic data in the dev DB).

## Rules (owner instructions)

- Work only on `m2-ingestion`. **No push, merge, rebase or history rewrite** without explicit approval.
- **No network requests to any data provider and no provider activation** without explicit approval (P9).
- Keep infrastructure details out of tracked files (D48): before any commit, run the `FORBIDDEN` patterns from the backup `sanitize.py` (path in local Claude memory).
- Don't lock datasets or confirm classifications or conflicts on the owner's behalf.
- Don't change Git identity settings. No new dependencies without approval.
- `make check` at each milestone; update `SESSION_LOG.md` / `NEXT_SESSION.md`; stop the dev stack afterwards.

## Start-up and validation

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -1   # expect 7fb9bf3 (or the handoff commit)
make up-dev        # background job; several minutes; applies migrations
make check         # must pass: backend 308, engine 109, frontend 2
make down-dev      # when finished
```

## Useful commands (all `docker exec erp-dev-worker-1 python -m erp_backend.cli …`)

- **Datasets:** `dataset-build --ticker T`, `dataset-list`, `dataset-verify --id`, `dataset-lock --id --reason`, `dataset-rebuild --ticker --reason`.
- **Conflicts:** `conflict-list --id [--all]`, `conflict-resolve --id --key (--select F | --value V --source S | --override | --revoke) --reason`.
- **Classification:** `classify --ticker T`, `classification-confirm --ticker --class --reason`.
- **SEC:** `sec-standardize-report --ticker T`, `sec-rebuild-from-archive`.
- **Tooling:** `.tools/bin/uv run --all-packages …` (plain `uv run` drops the workspace packages). After an intended engine calculation change, run `ERP_UPDATE_GOLDEN=1 make test-engine`.

## Remaining M2 after the import

Live Yahoo validation, US Treasury, Bank of Canada, Damodaran (each needs approval first), then the M2 exit criteria (Blueprint §14.1), owner review and the merge to `main`.
