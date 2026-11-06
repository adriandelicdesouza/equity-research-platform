# Memory: Estimates vs Measurements

The estimates come from Blueprint §1.2. They are kept as written and never overwritten by measurements; measurements are appended below with date, conditions and method.

**Method:** `infra/scripts/measure-memory.sh` reads cgroup v2 `memory.current`, `memory.peak` (since container start) and `memory.max` for each container. cgroup memory includes reclaimable page cache, so the database figure overstates its unavoidable usage. Load comes from `infra/scripts/loadgen.py`.

## Estimates (Blueprint §1.2, approved)

| Container | Hard limit | Expected typical | Expected peak |
|---|---|---|---|
| web | 48 MB | ~15 MB | ~30 MB |
| api | 384 MB | ~130–180 MB | ~300 MB |
| worker | 576 MB | ~120–200 MB | ~450 MB |
| db | 576 MB | ~120–220 MB | ~400 MB |
| **Total** | **1,584 MB** | **~0.4–0.6 GB** | **~1.2 GB** |

## Measurements

### M0 — 2026-11-06, dev stack (`erp-dev`), Minecraft not running

Conditions:
- Stack: `web`, `api`, `db`. No worker exists yet (D32).
- Load: 8 concurrent clients for 60 s. Each logs in (one Argon2id verification, 19 MiB) every 20 requests and otherwise alternates `/api/auth/me` and `/api/health`. That's about 12,000 requests per run.

| Container | Limit | Idle | Load peak (default malloc) | Retained after load (default) | Load peak (`MALLOC_ARENA_MAX=2`) | Retained after load (arena cap) |
|---|---|---|---|---|---|---|
| web | 48 MiB | 10.8 | 20.3 | 16.8 | 18.6 | 15.6 |
| api | 384 MiB | 65.8 | **294.1** | **279.2** | **218.5** | **198.5** |
| db | 576 MiB | 107.6 | 128.0 | 116.6 | 128.0 | 60.4 |
| **Total** | 1,008 MiB (M0 services) | 184.2 | 442.4 | 413 | 365.1 | 274 |

Throughput at 60 s: 552 logins / 10,940 other requests (default) vs 591 / 11,706 (arena cap).

Findings:
1. **API peak in line with the estimate (~300 MB), but retained memory wasn't.**
   - With default glibc malloc, the request threadpool held on to freed Argon2 buffers: 279 MiB stayed resident after load, 73% of the cap.
   - With `MALLOC_ARENA_MAX=2` (D33): peak 218 MiB (57% of cap), retained 199 MiB, slightly *higher* throughput. Adopted.
2. **DB well under its estimate** at this stage: no financial data yet. Its 128 MiB peak equals `shared_buffers`.
3. **Web far under its cap** at about 19 MiB peak.
4. **Host:** MemAvailable stayed at or above 5.8 GiB throughout. Host swap shows 9 MiB used after the second run. Our containers can't swap (`memswap_limit` = `mem_limit`), so that comes from another process.

### Still to measure

- **Minecraft co-residency:** deferred to M5 by the owner (2026-11-06).
- **Worker (M2)**, and the **db with real financial datasets (M2)**.
- **M5 acceptance workload** on the prod stack.
