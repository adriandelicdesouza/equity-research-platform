# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3. History: `SESSION_LOG.md`.

## Verified state

- **Architecture:** Revision 2 APPROVED (decisions up to D46).
- **Git:**
  - `main` @ `057b963` (M0 + M1).
  - Branch **`m2-ingestion`** holds the M2 foundation and **SEC adapter** commits. **Keep unmerged until M2 is complete and ready for review** (owner instruction).
  - No remote; nothing pushed.
- **SEC:** the owner's User-Agent is configured in git-ignored `.env.dev` and reaches the `worker` only.
  - **13 live SEC requests** were made (Session 3), all successful and at ≤ 5 req/s.
  - Data loaded for AAPL, MSFT, SHOP, CNI, CNQ, RY (88,160 facts).
- **Dev stack `erp-dev`:** `web`, `api`, `db`, `worker`, all healthy. DB at Alembic `0004`.

## Owner decisions open

- Merge of `m2-ingestion`: at M2 completion (review requested then).
- Off-server backup destination: before production (M5). Deferred.
- Minecraft co-residency test: deferred to M5. Do not start `minecraft.service`.

## Exact next task: rest of M2 (Blueprint §4.4–§4.6, §5, §14.1 M2)

1. **Standardization mapping v1:** versioned YAML in `mappings/` (concept → standard line item, priority, sign) for `us-gaap` and `ifrs-full`, plus the `ref` tables loaded by migration. Concept fallback is chosen per period.
2. **Financial datasets:** `fin.fiscal_periods`, `fin.financial_datasets` (`selection_policy`, `facts_as_of`, `interim_coverage`), `fin.financial_values` built from the D11 selection, with derived values flagged (gross profit, Q4 = FY − 9M, EBITDA).
3. **Integrity checks** (Blueprint §5.3) and **conflict materiality / lock blocking** (§10.3, D13).
4. **Financial-business classifier** (D24): SIC plus XBRL evidence. RY must classify as `bank`.
5. **Remaining providers:**
   - Yahoo chart prices, with manual TSX listing entry and symbol validation (D15);
   - Treasury and Bank of Canada rates and FX;
   - Damodaran reference datasets;
   - Excel/CSV import template.
   **Ask before the first live request to each new provider.**

**M2 exit criteria (Blueprint §14.1):**
- The five companies load with ≥ 8–15 years and passing balance checks.
- RY is classified `bank`, so DCF is blocked.
- A TSX-only company is entered manually, its symbol validated, and its financials imported via template.
- Rebuilding a dataset from archived facts is byte-identical.

## Prerequisites at startup

```bash
git switch m2-ingestion && git status --short && git log --oneline -5
make ps-dev || make up-dev
make check
```

- Use `make` targets or `.tools/bin/uv run --all-packages …`. Run image builds and `make up-dev` in the background.
- SEC CLI, which runs where the User-Agent exists (the worker):
  - `docker exec erp-dev-worker-1 python -m erp_backend.cli sec-check-config`
  - `docker exec erp-dev-api-1 python -m erp_backend.cli sec-ingest --ticker XYZ` (enqueue only)
  - `docker exec erp-dev-worker-1 python -m erp_backend.cli sec-rebuild-from-archive` (no network)
