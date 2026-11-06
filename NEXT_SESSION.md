# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3. History: `SESSION_LOG.md`.

## Verified state

- **Architecture:** Revision 2 APPROVED (D27 amended; D35–D39 added).
- **Methodology:** `docs/methodology/dcf.md` APPROVED.
- **Git:**
  - `main` @ `0c42321` (M0).
  - `m1-engine` holds the methodology, reference and **M1 engine** commits. **Not merged.**
  - No remote; nothing pushed.
- **Engine `erp_engine`:** M1 scope complete and validated. That covers core, typed outputs, forecast with leases, FCFF/FCFE, stub A/B, roll-forward, terminal values, bridge, dilution, WACC, classification gate and golden master.
  - It matches all five benchmarks and the independent reference.
  - It isn't exposed through the API or UI yet.
- **Dev stack `erp-dev`:** running, loopback only.

## Decisions needing the owner

1. **Merge `m1-engine` into `main`?** M1 exit criteria are met (Blueprint §14.1). Merge requires approval.
2. **Start M2 (data ingestion)?** It needs the **SEC User-Agent contact string**, stored in `.env` only, plus approval of new runtime dependencies:
   - `httpx` (in the lock as a dev dependency; would become a backend runtime dependency);
   - `procrastinate` (job queue, D2);
   - `duckdb` (D8);
   - `zstandard` (raw payload archive).
3. **Off-server backup destination:** before production (M5).
4. **Minecraft co-residency test:** deferred to M5. Do not start `minecraft.service`.

## Exact next task (after decision 2)

**M2, Data ingestion** (Blueprint §4, §14.1). Start with:
1. Add the `worker` service (D32) and the procrastinate schema migration.
2. Archive raw provider payloads (SHA-256, zstd) and build the `source.*` tables.
3. SEC adapter: ticker map, submissions, companyfacts. Rate limit 5 req/s. User-Agent from `.env`.
4. Deterministic first/latest fact selection with `facts_as_of` (§4.4, D11), against trimmed real-payload fixtures.

**Verification:**
- AAPL, MSFT, SHOP, CNI and CNQ load with passing balance checks.
- RY is classified `bank`.
- Rebuilding a dataset from archived facts is byte-identical.

## Prerequisites at startup

```bash
git switch m1-engine && git status --short && git log --oneline -5
make ps-dev || make up-dev
make check
```

Use `make` targets or `.tools/bin/uv run --all-packages …`. Run long jobs in the background. Golden outputs: after any deliberate calculation change, bump `erp_engine/version.py`, then run `ERP_UPDATE_GOLDEN=1 make test-engine`.
