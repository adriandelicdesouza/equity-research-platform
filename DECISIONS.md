# Architectural Decisions

**Status:** Revision 2 — **APPROVED** 2026-11-06 (Session 2). Pending decisions (P1–P6) remain open.
**Date:** 2026-11-06
**Detail:** `TECHNICAL_BLUEPRINT.md` (section references below). Limitations: `KNOWN_LIMITATIONS.md`.

Each decision lists the choice, why, the alternative rejected, and the trade-off accepted. "Rev 2" marks decisions added or changed by the 2026-11-06 architecture review.

---

## Platform and infrastructure

### D1. Calculation engine is an in-process pure-Python library (§1.3 A1)
- **Choice:** `erp_engine` package with no I/O, imported by `api` and `worker`.
- **Why:** a DCF run takes milliseconds; a separate HTTP service would cost ~150 MB, add serialization and new ways to fail.
- **Rejected:** a separate engine container.
- **Trade-off:** physically one process. Logical independence is kept: typed inputs → typed outputs + trace, so it can be split out later without changing callers. Minor deviation from the brief's "independent services".

### D2. Four containers, no Redis (§1.2, §1.3 A2)
- **Choice:** `web` (Caddy), `api`, `worker` (same image), `db` (PostgreSQL 17). Jobs run through `procrastinate` on Postgres.
- **Why:** hardware budget; jobs are transactional with application data.
- **Rejected:** Redis + Celery/RQ.
- **Trade-off:** queue throughput is limited by Postgres. More than enough for daily syncs and sensitivity grids.

### D3. Memory hard caps total ~1.6 GB — Rev 2 (§1.2)
- **Choice:** web 48 MB, api 384 MB, worker 576 MB, db 576 MB = 1,584 MB.
- **Why:** Minecraft (`-Xmx3G`) can co-reside. About 0.9 GB margin is left for the OS and page cache.
- **Process:** expected (estimated) and measured memory are recorded separately. Measured at M0 and M5. M5 includes a run with Minecraft running.
- **Trade-off:**
  - api runs one uvicorn process.
  - Postgres runs with `shared_buffers=128MB`.
  - Sensitivity grids larger than 100 cells run as background jobs.
  - Dev and prod don't run at the same time.

### D4. Ports and exposure — Rev 2 (§11.1, §11.1.1)
- **Choice:** `127.0.0.1:8090` (HTTP, loopback) and `<tailscale-ip>:8443` (HTTPS, Caddy internal CA) for prod. Dev uses `127.0.0.1:8091` only. Postgres is never published in prod.
- **Remote access:** a Cloudflare Tunnel hostname plus an Access policy. Needs approval.

### D5. Secure cookies on every path — Rev 2 (§11.1.1)
- **Choice:** always `__Host-` + `Secure; HttpOnly; SameSite=Strict`.
  - Non-localhost access is HTTPS: Cloudflare remotely, Caddy `tls internal` on Tailscale/LAN.
  - Plain HTTP only on `localhost`, which browsers treat as a secure context.
- **Rejected:** an "insecure cookie" mode for LAN.
- **Trade-off:** the Caddy root CA must be installed on each client device. `tailscale serve`/`cert` would avoid this but changes Tailscale config, so it's optional and needs approval.

### D6. Separate dev and prod Compose projects (§12.2)
- **Choice:** `erp-dev` and `erp-prod`, each with its own volumes, database and data root.
- **Why:** dev actions can't touch prod or unrelated containers.

### D7. Off-server backup is a production gate — Rev 2 (§12.4)
- **Choice:**
  - Nightly `pg_dump` plus restic (encrypted) to an off-server destination you choose.
  - Production acceptance requires a successful off-server snapshot and a restore drill from it.
- **Why:** single SSD, no RAID.
- **Trade-off:** needs a destination decision from you, and possibly registration if it's cloud.

---

## Data

### D8. PostgreSQL is authoritative; DuckDB is analytical only (§3.1, §3.6)
- DuckDB never holds a second authoritative copy. It queries archived raw payloads and Postgres (via its extension) for analytics.
- If DuckDB isn't earning its place by Release 3, this will be reported.

### D9. Exact numerics (§3.1, §6.3)
- **Choice:** `NUMERIC` in Postgres, `Decimal` (precision 34, `ROUND_HALF_EVEN`) in the engine.
- **Why:** exact reproducibility and reconciliation.
- **Trade-off:** slower than float, which doesn't matter for single runs. Monte Carlo (R4) uses float64, validated against the Decimal path.

### D10. Immutable, versioned financial datasets pinned by valuations (§3.4)
- New filings or restatements create a new dataset version.
- Projects re-base only when the analyst chooses to.
- Saved runs store fully materialized inputs.

### D11. Deterministic first-reported vs latest-reported selection — Rev 2 (§4.4)
- **Ordering:** a total order on candidate facts by filed date, then original-before-amendment, then accession number.
  - First-reported = the minimum; latest-reported = the maximum.
- **Reproducibility:** each dataset records `selection_policy` and a `facts_as_of` cut-off, so rebuilds are exact.
- **Ambiguity:** conflicting values within one filing are marked ambiguous and never auto-selected.

### D12. Interim (6-K) XBRL treated as conditional — Rev 2 (§0.5, §4.4)
- Interim coverage is detected per company (`none`/`partial`/`full`).
- Features needing interim data warn and degrade; they never assume availability.

### D13. Material data conflicts block dataset locking — Rev 2 (§10.3)
- Materiality thresholds are configurable, with a lower threshold for DCF-critical items.
- A material conflict needs either a resolution or a documented override.
- Immaterial conflicts are auto-resolved by priority and stay visible.

### D14. Provenance on every value; suggestions require approval (§10.1)
- System suggestions (historical averages, Damodaran values) are `suggested`.
- A saved valuation can't use a suggested value until an analyst approves it.

### D15. TSX listings are entered manually — Rev 2 (§4.3)
- **Choice:**
  - Ticker + exchange (MIC) entered by the analyst.
  - Provider symbol derived by a documented rule.
  - The symbol is validated by fetching prices from the verified chart endpoint.
- **Rejected:** assuming Yahoo supports discovery (not verified).
- **Trade-off:** extra manual step for TSX-only names. Automated discovery can be added later as a separate, feature-flagged adapter once verified.

### D16. Yahoo unofficial chart endpoint for prices (§0.4, §4.2)
- **Why:** the only free source verified for TSX daily prices. Stooq now blocks automation.
- **Mitigation:**
  - Adapter boundary.
  - Archived payloads.
  - Manual CSV price import fallback.
  - Our own adjustment recomputation, checked against the provider's.

### D17. SEC ingestion moved into Release 1 (§14.1)
- **Why:** the required first-release flow starts at ticker → historical statements.
- **[DEVIATION]** from the brief's sequencing. Broader providers and documents remain in Release 3.

---

## Calculation engine and methodology

### D18. Typed valuation output kinds — Rev 2 (§6.6)
- **Output kinds:** `ENTERPRISE_VALUE`, `EQUITY_VALUE`, `PER_SHARE_VALUE`. Every methodology declares which one it produces.
- **Conversions:**
  - Only `bridge/` converts EV → equity.
  - Only `dilution/` converts equity → per share.
- **Reconciliation:** weights methods only at one output kind, and records basis (minority vs control).
- **Why:** prevents double-counting claims when DDM, residual income, SOTP, NAV, LBO and financial-institution models arrive.

### D19. FCFE produces equity value directly; no bridge — Rev 2 (§7.3)
- FCFE at the cost of equity gives equity value directly. Cash, non-operating assets and claims are not added or subtracted.
- Their effects must be modelled in the FCFE stream (interest income, modelled distributions, equity-method income).
- The EV-to-equity bridge is exclusive to FCFF and other EV methods.

### D20. Stub periods: two explicit modes; value at balance-sheet date, roll forward — Rev 2 (§7.4)
- **Mode B (actual partial-period):** full-year forecast minus actual YTD, with NWC change from the actual interim balance sheet.
  - Default when interim data exists.
  - Captures seasonality and working-capital timing.
- **Mode A (prorated):** time-proportional fallback, with documented limitations.
- **No interim data:** no stub. The model values at the last fiscal year-end.
- **Roll-forward:** value is computed at the balance-sheet date and compounded at Ke to the valuation date, net of dividends paid.

### D21. ERP/CRP separation and beta methodology — Rev 2 (§7.5)
- **ERP inputs:** mature-market ERP and CRP are separate inputs. The analyst picks one basis: `mature_plus_crp` or `total_country_erp`.
- **CRP:**
  - CRP plus `total_country_erp` is rejected.
  - Exactly one CRP exposure method applies (λ, beta or flat).
  - CRP defaults to 0 for the US and Canada.
  - The risk-free rate must be default-free.
- **Regression beta:**
  - Weekly 2y or monthly 5y; simple total returns; same-currency broad index.
  - Diagnostics shown; optional Blume adjustment.
  - Unlevered at the window's average D/E before relevering.
- **Relevering:** Hamada by default, Harris–Pringle selectable.

### D22. Lease treatment covers IFRS 16 and ASC 842 — Rev 2 (§7.7.1)
- **Choice:** one explicit `lease_treatment` (`as_debt` / `as_operating`) applied consistently to FCFF, the bridge, WACC weights and EBITDA multiples.
- **Validation:** missing inputs, ROU additions counted twice, and lease-basis mismatches in multiples are all errors.

### D23. Dilution via an instrument register with double-count safeguards — Rev 2 (§7.7.2)
- **Base:** point-in-time basic shares. Weighted-average diluted shares are never the base.
- **Each instrument:** appears once, with one treatment. Convertibles are either a claim or if-converted, never both.
- **SBC:** expensing and future-dilution modelling are mutually exclusive.
- **TSM:** solved exactly.
- **Sanity check:** a warning when implied diluted shares differ from reported diluted shares by more than 5%.

### D24. Financial-business classification — Rev 2 (§7.6.1)
- **Classes:** `bank`, `insurer`, `specialty_lender`, `reit`, `asset_manager_broker`, `investment_holding`, `non_financial`.
- **How:** suggested from SIC plus XBRL evidence, confirmed by the analyst.
- **Policy (data-driven):**
  - Banks, insurers, specialty lenders and investment holdings: FCFF blocked.
  - REITs and asset managers/brokers: permitted with targeted warnings.

### D25. No browser-side financial calculations (§1.3 A4)
- Previews call the authoritative engine through a non-persisting endpoint. No financial logic is duplicated in TypeScript.

### D26. Mathematically independent reference valuation — Rev 2 (§13.1.1)
- Written from the methodology spec, not the engine.
- Uses different formulations: backward recursion, the economic-profit identity, APV, and closed forms.
- Uses different arithmetic (`Fraction`, `mpmath`).
- Can't import `erp_engine` (enforced by import-linter).
- Hand-worked benchmarks are never regenerated from engine output.

### D27. Gordon terminal value discount timing (§7.4) — AMENDED 2026-11-06
- **Current rule (owner-approved, methodology review Q1):** Gordon TV timing **follows the cash-flow convention**. Under mid-period discounting it's discounted at `t_N − ½`; under end-of-period, at `t_N`.
  - End-of-period stays available as a recorded conservative override, shown on outputs.
  - Exit-multiple TV is always at the end of the final period.
- **Why amended:** under mid-period discounting the perpetuity's flows also arrive mid-period, so end-of-period TV timing was internally inconsistent. It understated EV by 3.9% in methodology benchmark B2.
- **Superseded rule (Rev 2):** end-of-period default with a mid-period variant.
- **Detail:** `docs/methodology/dcf.md` §6.2.
---

## Frontend

### D28. React 19 + TypeScript + Vite; TanStack Router/Query; AG Grid Community; ECharts; Tailwind + Radix (§2.2)
- API types are generated from OpenAPI, the single contract source.
- AG Grid is used for presentation and input only. No formulas.

---

## Implementation decisions during M0 (Session 2)

These refine the approved architecture. None changes an approved methodology decision.

### D29. Runtime data and secrets live inside the repository directory (git-ignored)
- **Choice:** `DATA_ROOT` defaults to `<repo>/data/<env>/`, and secrets to `<repo>/secrets/<env>/`. Blueprint §12.2 proposed `~/erp-data/<env>/`.
- **Why:** `CLAUDE.md` requires working exclusively within this repository. The path is still configurable (`ERP_DATA_ROOT`) for a later move to external storage.
- **Safeguards:** both directories are git-ignored and excluded from the Docker build context (`.dockerignore`). `secrets/` is mode 0700.

### D30. Caddy `default_sni` for IP-address HTTPS
- **Finding:** clients send no TLS SNI when connecting to a bare IP. The internal-CA listener then failed the handshake for `https://127.0.0.1:8444`. The planned prod Tailscale-IP listener would have failed the same way.
- **Choice:** set `default_sni` to the IP (`ERP_TLS_DEFAULT_SNI`). Verified over HTTPS by IP and by hostname.

### D31. Three database roles
- **Roles:** `postgres` (bootstrap/tests only), `erp_owner` (migrations; owns DB, not superuser), `erp_app` (runtime least privilege).
- **Audit log:** append-only twice over. `erp_app` has only INSERT/SELECT, and triggers reject UPDATE/DELETE/TRUNCATE for every role, including the owner. Tested.

### D32. Worker container deferred to the first milestone that needs it
- **Choice:** M0 runs `web`, `api`, `db` and a one-shot `migrate`.
- **When:** the `worker` service (procrastinate queue) is added in M2, when ingestion jobs exist. No placeholder container runs before then.
- **Budget:** unchanged (576 MB for the worker).

### D33. `MALLOC_ARENA_MAX=2` for the API container
- **Finding:** 8 concurrent clients with logins pushed the API to 294 MiB peak, and 279 MiB stayed resident after the load ended. That was 77% of its 384 MiB cap.
- **Cause:** glibc's per-thread malloc arenas in the request threadpool keep freed Argon2 buffers (19 MiB per verification).
- **Choice:** set `MALLOC_ARENA_MAX=2`. Measured effect: `docs/operations/memory.md`.

### D34. Login rate limit keys
- **Keys:** `(client IP, username)` and `username` alone.
- **Why:** requests arriving through cloudflared share one source address. Per-account limiting still works when IPs are indistinguishable.
- **Scope:** in-memory, valid because the API runs one process.

### D35. Default terminal FCF form: value driver (owner-approved 2026-11-06)
- **Choice:** the Gordon TV defaults to the value-driver form, `NOPAT_{N+1} × (1 − g/RONIC) / (WACC − g)`. For FCFE it uses `ROE_new`.
- **RONIC:** a required analyst assumption, suggested `= WACC` (growth neither creates nor destroys value) until approved.
- **Simple growth:** stays selectable per run.
- **Why:** keeps terminal reinvestment consistent with growth (Blueprint §7.6). The simple form carries forward the final year's reinvestment rate, which is usually inconsistent with `g`.
- **Detail:** `docs/methodology/dcf.md` §6.1, benchmark B1-VD.

## Implementation decisions during M1 (Session 3)

### D36. Engine inputs are frozen dataclasses; the engine has no third-party dependencies
- **Choice:** `erp_engine.inputs` uses frozen dataclasses with `Decimal` fields, not Pydantic.
- **Why:** keeps the authoritative calculation package dependency-free and independent of the API layer.
- **Validation split:** the backend (Pydantic) validates requests and maps them to these types.

### D37. WACC is a separate engine API; DCF consumes explicit discount rates
- **Choice:** `erp_engine.wacc` computes Ke and WACC with every §7 rule. `run_valuation` takes the resulting per-period rates (`discount_rates`, `cost_of_equity`).
- **Why:** the WACC build-up and the valuation are inspected separately, and scenarios override either.
- **Integration:** wiring the two together in one request is backend work (M3).

### D38. Student-t quantile without SciPy
- **Choice:** the 95% CI of regression beta uses the Cornish–Fisher expansion of the t quantile.
- **Accuracy:** error below 2×10⁻⁵ for 46 or more degrees of freedom. The minimum sample sizes guarantee that range.
- **Why:** avoids adding SciPy (about 40 MB) for one constant.

### D39. FCFE with an interim balance sheet (stub) deferred
- **What's deferred:** methodology §5.3 states the Mode B rule for FCFE. Implementing it needs fiscal-year-start debt and cash balances, which the R1 financing inputs don't carry.
- **Current behaviour:** the engine refuses FCFE with a stub (`fcfe_stub_not_supported`) rather than approximating.
- **FCFF:** stubs are fully supported.
- **Plan:** revisit with the R2 debt schedule.

## Pending decisions (need your input)

| # | Decision | Needed by |
|---|---|---|
| P1 | SEC User-Agent contact string (stored in `.env`) | M2 |
| P2 | Off-server backup destination | M5 (production gate) |
| P3 | Cloudflare tunnel hostname + Access policy | M5 (remote access) |
| P4 | Start Minecraft for the co-residency test (you, or approve me) | M0 / M5 |
| P5 | Optional: LAN binding; `tailscale serve` instead of internal CA | M0 |
| P6 | Optional: registering free-key data providers | Release 3 |
