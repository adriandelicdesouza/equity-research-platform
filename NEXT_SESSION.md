# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3. History: `SESSION_LOG.md`.

## Verified state

- **Architecture:** Revision 2 APPROVED (decisions up to D42).
- **Git:**
  - `main` @ `057b963` (M0 + M1).
  - Branch **`m2-ingestion`** holds the M2-preparation commit. **Not merged.**
  - No remote; nothing pushed.
- **Done for M2 so far:**
  - runtime dependencies (D40);
  - job queue in schema `jobs` and `worker` service (D41);
  - `source.providers` and append-only `source.provider_fetches`;
  - raw payload archive;
  - provider HTTP client;
  - SEC User-Agent gate (D42).
- **Dev stack `erp-dev`:** `web`, `api`, `db`, `worker`, all healthy, loopback only. DB at Alembic `0003`.
- **No SEC request has ever been made by the application.** `ERP_SEC_USER_AGENT` is not configured.

## BLOCKING decision — needs the owner

**SEC User-Agent contact string.**
- It must be the owner's real organisation or name plus contact email, e.g. the form `Company Name contact@company-domain`. **Do not invent one or use a placeholder.**
- Once given, add it to `.env.dev` as `ERP_SEC_USER_AGENT=...`. Never commit it.
- Then pass it through to the `worker` (and `api`) environment in `infra/compose.yaml` via `${ERP_SEC_USER_AGENT}`. That isn't wired yet, deliberately.

## Other open decisions

- Merge `m2-ingestion` into `main`: after the SEC adapter milestone, or now if preferred.
- Off-server backup destination: before production (M5).
- Minecraft co-residency test: deferred to M5. Do not start `minecraft.service`.

## Exact next task (only after the User-Agent is provided)

**SEC adapter + standardization foundation** (Blueprint §4.2–§4.4, D11, D12):
1. Wire `ERP_SEC_USER_AGENT` into compose. Verify the gate accepts it: `python -m erp_backend.cli` check, no request yet.
2. Migration `0004`: `market.companies`, `market.securities`, `market.company_identifiers`, `source.filings`, `source.reported_facts` (Blueprint §3.3).
3. Adapter tasks on queue `ingestion`:
   - ticker map (`company_tickers_exchange.json`, cached daily);
   - submissions (profile);
   - companyfacts.
   Each goes through `fetch_and_archive`.
4. Fact parser and deterministic first/latest selection with `facts_as_of` (D11). Interim-coverage detection (D12).
5. Fixtures: trimmed real payloads fetched once through the adapter (AAPL, MSFT, SHOP, CNI, CNQ, RY). Tests stay offline using those fixtures.

**Verification:**
- `make check` green.
- Selection permutation tests pass.
- Rebuilding from archived facts is byte-identical.
- First live fetches stay at ≤ 5 req/s and are logged in `source.provider_fetches`.

## Prerequisites at startup

```bash
git switch m2-ingestion && git status --short && git log --oneline -4
make ps-dev || make up-dev
make check
```

Use `make` targets or `.tools/bin/uv run --all-packages …`. Run long jobs (image builds, `make up-dev`) in the background.
