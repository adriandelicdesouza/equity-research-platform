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
- **Why:** An on-demand game server (~3.5 GB) can co-reside. About 0.9 GB margin is left for the OS and page cache.
- **Process:** expected (estimated) and measured memory are recorded separately. Measured at M0 and M5. M5 includes a run with the co-resident game server running.
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

## Implementation decisions during M2 preparation (Session 3)

### D40. Runtime dependencies for ingestion (owner-approved 2026-11-06), with a footprint guard
| Package | Purpose | Installed size |
|---|---|---|
| `httpx` (moved from dev to runtime) | Provider HTTP client: timeouts, mockable transport for tests | ~1.5 MB (with `httpcore`) |
| `procrastinate` 3.10 (+ `psycopg-pool`, `python-dateutil`) | Job queue on PostgreSQL (D2): ingestion, syncs, large sensitivity grids | ~1.4 MB |
| `duckdb` 2.0 | Analytical queries over archived payloads and Postgres (D8) | ~65 MB native library |
| `zstandard` 0.25 | Compression of the raw payload archive | ~23 MB |

- **Total:** about 105 MB more on disk in the app image. That image is shared by `api`, `worker` and `migrate`, so it's stored once.
- **Memory guard:** an import-linter contract forbids importing `duckdb` from request-serving modules (`erp_backend.main/api/auth/security`). DuckDB is loaded only by worker tasks, never resident in the API process.
- **Retries:** implemented directly (bounded exponential backoff, honouring `Retry-After`) instead of adding `tenacity` (Blueprint §2.1).

### D41. Job queue schema frozen in the repository, isolated in schema `jobs`
- **Schema SQL:** procrastinate 3.10.0's schema SQL is stored as `backend/alembic/sql/procrastinate_3_10_0.sql` and applied by migration `0002` with `search_path=jobs`. A procrastinate upgrade is therefore an explicit migration, never implicit.
- **Connections:** all queue connections set `search_path=jobs`. `erp_app` gets DML/EXECUTE there only.
- **Deferral from sync code:** goes through `erp_backend.jobs.defer()`. In 3.10, `App.configure_task` binds to the original app's connector, which bypasses the sync connector.
- **Fetch log:** `source.provider_fetches` is append-only. `erp_app` has INSERT/SELECT only, and a trigger rejects UPDATE/DELETE/TRUNCATE for every role.

### D42. SEC access is impossible without the owner's contact User-Agent
- **The gate:** `erp_backend.ingestion.sec.sec_client()` is the only way to build an SEC client. It validates `ERP_SEC_USER_AGENT`:
  - required;
  - must contain an email address;
  - reserved or example domains and placeholder words are rejected.
- **No default:** none exists, and the setting is never committed (`.env` only).
- **Tests:** they use a `MockTransport` with a clearly labelled validator fixture, so no test can reach SEC.

## Implementation decisions during the SEC adapter (Session 3)

### D43. Reported-fact storage
- **Keys:** `source.reported_facts` uses a `bigint` identity key rather than a UUID, for row size at volume.
- **Uniqueness:** `(company, taxonomy, concept, unit, start, end, accession, value)` with `NULLS NOT DISTINCT`. Exact repeats collapse. Differing values within one filing are both kept, so selection can mark them AMBIGUOUS instead of silently picking one (D11).
- **Source records** (`sec_tickers`, `filings`, `reported_facts`) are append-only:
  - `erp_app` has INSERT/SELECT only;
  - a trigger rejects UPDATE/DELETE/TRUNCATE for every role.
- **Numbers:** values are parsed as `Decimal` from the JSON (never float).

### D44. Interim-coverage definition (D12 made concrete)
- **Window:** the 8 most recent interim period-ends found in 10-Q/6-K (and amendments). Q4 isn't reported on interim forms, so 8 period-ends cover about 2.7 years.
- **A period is complete when it has:**
  - quarterly revenue, operating income and net income (84–98-day durations);
  - cash and total assets (instants);
  - year-to-date operating cash flow.
- **Levels:** `full` = 8/8 complete, `none` = 0, otherwise `partial`.
- **Recorded per period:** the forms used and whether data came from 6-K.
- **Refined after live data:** a period-end counts only if an interim form reports a quarterly revenue or net-income flow ending there. 10-Qs restate the prior fiscal year-end balance sheet, and those comparative instants were wrongly counted as quarters (found on AAPL).
- **Recency bound (found on CNI):** only period-ends within 3 years of the company's latest reported period count. CNI's complete 2008–09 10-Q quarters were masking that its current results aren't in interim XBRL.

### D45. SEC request discipline
- **Who fetches:** the owner's User-Agent is passed only to the `worker` container, the only process that fetches.
- **Rate:** all SEC clients in a process share one rate limiter (5 req/s). Every SEC task holds the procrastinate lock `sec`, so SEC jobs never run concurrently.
- **Logging:** the User-Agent is never logged or stored, since fetch rows record URL and params only.

### D46. Derived company fields can be rebuilt from the archive without network access
- **What it does:** `rebuild_company_from_archive` (CLI `sec-rebuild-from-archive`) re-parses the latest archived submissions payload (hash-verified) and recomputes inferred fields from stored facts. It takes no HTTP client.
- **When to use it:** when parsing or inference rules improve, apply them to existing companies without new SEC requests. Changes are audited with the source fetch id.
- **Rule changes made in M2 and applied this way:**
  - Country falls back to the incorporation code when the business address is absent. SEC code `Z4` = Canada (federal).
  - Reporting currency uses the latest periodic filing, falling back to interim forms for 6-K-only filers such as CNI. Registration forms (F-10) are ignored.

### D49. Standard line-item mapping v1: versioned YAML, frozen once loaded (§3.3, §4.4, §5.2)
- **Files:** `mappings/v1/line_items.yaml` (81 standard line items) plus `us-gaap.yaml` and `ifrs-full.yaml` (ordered concept candidates with a sign multiplier). Loaded by migration `0005` into `ref.mapping_versions`, `ref.standard_line_items` and `ref.concept_mappings`.
- **Immutability:** the migration pins the SHA-256 of `mappings/v1/` and refuses to run if the files changed. The `ref` tables reject UPDATE/DELETE/TRUNCATE, and the application role can only read them. A change means a new `v2/` directory and a new migration.
- **Refinement of the blueprint DDL:** line items are keyed by `(mapping_version, code)`, and each item carries a `period_type` (duration/instant) next to `value_type`, so a version is self-contained.
- **Sign conventions:** `natural` (income, balances), `expense` (costs stored positive), `cash_flow` (inflow +, outflow −). A candidate's `sign: -1` converts a reported positive payment into an outflow (e.g. capex).
- **Resolution (pure, `erp_backend.standardization.resolve`):**
  - It works per line item and per period window, using the D11 selection.
  - The highest-priority concept with a selection in that window wins. The concept, its priority and any lower-priority alternatives are recorded.
  - If the winner is ambiguous, no value is used and nothing falls back silently.
  - Facts must be in the reporting currency (or `<currency>/shares`, or `shares`).
- **Candidate lists** were checked against the stored facts of AAPL, MSFT, SHOP, CNI, CNQ and RY (CLI `sec-standardize-report`, read-only). The blueprint's revenue order was kept: where Shopify reports both concepts, the values are equal.
- **No-NCI fallbacks:** consolidated net income and total equity come last in the candidate lists for "attributable to the parent" items (`NetIncomeLoss`/`StockholdersEquity` in us-gaap, `ProfitLoss`/`Equity` in IFRS). They are flagged as fallbacks (priority > 1) and are only correct for companies without non-controlling interests; the integrity checks (§5.3) are the safeguard.
- **Dependency:** `pyyaml` is declared directly in `erp-backend`. It was already in the lock and the image via `uvicorn[standard]`, so no new package was installed.

### D50. Versioned standardized datasets: periods, lifecycle, Q4, byte-identical rebuilds (§3.3, §3.4, §4.4; D10, D11, D13)
- **Fiscal periods** (`fin.fiscal_periods`, append-only, shared by versions):
  - They come from the company's own reported windows, never from XBRL `fy`/`fp` or a calendar assumption.
  - Fiscal years are the 350–380-day windows. When two annual windows overlap, the one carrying more values wins and the other is recorded as rejected.
  - **Label:** the calendar year in which the fiscal year ends. A 52/53-week year ending in the first 7 days of January belongs to the previous year. `weeks` = 52/53 for 364/371-day years.
  - **Interim:** quarters `Q1`–`Q4` and year-to-date `H1`/`9M`, the last two refining the blueprint's `H1` list. Quarters after the last fiscal year fall into the open next year.
  - Balance-sheet dates attach to the FY, or to Q1–Q3, ending that day.
  - LTM and STUB are not built yet.
- **Eligible facts:** periodic-report forms (10-K, 20-F, 40-F, 10-Q, 6-K and amendments) filed on or before `facts_as_of`, which defaults to the latest such filing and is always stored. Registration and prospectus forms (F-10, 424B2, …) are excluded.
- **Q4 = FY − 9M:**
  - Only for `flow` items, and only when no Q4 is reported.
  - Both inputs must be OK values of the same concept and sign.
  - The 9M window must start on the fiscal-year start.
  - Never for per-share or count items.
  - Derived values carry `{"rule": "Q4=FY-9M"}`, both input values, both accessions and both source fact ids. Skipped derivations are counted by reason.
- **Lifecycle** (`fin.financial_datasets`):
  - A version is draft, then locked or discarded. Rows are never deleted.
  - Version numbers increase per company and are never reused.
  - One open draft per company. A build whose canonical bytes equal the current version creates nothing.
  - The parent is the latest locked version, and the draft's summary records the diff against it, including whether the inputs or the mapping changed.
- **Locking** requires:
  - a successful verification (stored rows re-serialize to `content_sha256`, and a rebuild from the facts reproduces the same bytes);
  - no ambiguous values (D13).
- **Enforcement:** triggers bind every role, including the owner. Values are never updated, and are inserted or deleted only while their dataset is a draft. Datasets allow only the draft → locked or discarded transition, with nothing but the lifecycle fields changing. All transitions are audited.
- **Canonical form:** sorted JSON with normalized decimals.
  - Header: format, mapping version and SHA-256, taxonomy, currency, policy, cut-off, and `input_sha256` (a fingerprint of the eligible facts).
  - Periods and values, with full provenance.
  - Row ids and timestamps are excluded, so identical inputs give identical bytes.

### D51. Integrity checks and derived values; mapping v2 (§4.4 step 5, §5.3; D13, D50)
- **Checks run on every build** (pure `standardization/validation.py`). Results are stored in `fin.validation_results` with the draft and fingerprinted (`validation_sha256`, covered by `dataset-verify`). They follow the values' lifecycle: never updated, immutable once locked, removed on discard.
- **Statuses:** `passed`, `failed`, `not_evaluable`. An input that is missing, ambiguous or in an unexpected unit makes the check `not_evaluable`; missing data never passes.
- **Each result stores** expected, actual, difference (actual − expected), tolerance, unit, message, and inputs (value, provenance, concept, period, fact ids).
- **Formulas:**
  - **BALANCE:** assets = liabilities + equity.
  - **SUBTOTAL:** every *reported* parent with a mapping `subtotal` equals the signed sum of its children. Derived parents aren't checked, since they would be compared with themselves.
  - **CASH_ROLLFORWARD:** reported net change in cash = CFO + CFI + CFF + FX.
  - **CASH_RECONCILIATION** (warning, FY): net change in cash = BS cash − prior FY-end BS cash. A warning because cash-flow cash often includes restricted cash.
  - **CROSS_NET_INCOME:** IS net income = CF starting net income.
  - **CROSS_DA:** IS D&A = CF D&A, only where both are reported.
  - **SHARES_DILUTED_GE_BASIC:** 1% tolerance.
  - **EPS_BASIC** (error) / **EPS_DILUTED** (warning): EPS × weighted shares = net income to the parent.
  - **COMPLETENESS:** 11 DCF-required items present in each of the latest 5 FYs.
- **Tolerances:**
  - Balance, subtotal, cash and cross-statement checks: max(0.1% of the period's total assets, or of the larger compared value when assets are unavailable; one unit of reporting precision). Precision is inferred from the inputs' trailing zeros, capped at 10⁶.
  - Shares: 1%.
  - EPS: max(1% of net income, $0.005 × shares).
- **Locking is not blocked by failed checks:** that stays the analyst's decision, and the lock's audit event records the validation summary. Approval-for-valuation gating (§5.3, with an override reason) belongs to the valuation workflow.
- **Derivations** (pure `standardization/derivations.py`; dataset format 2):
  - **Quarters from year-to-date:** Q2 = H1 − Q1, Q3 = 9M − H1, Q4 = FY − 9M. This includes quarterly cash flows.
  - **Formulas** from the mapping's subtotals, when the parent isn't reported: gross profit, EBITDA (EBIT + CF D&A), FCF, SG&A, short-term debt, equity totals, net change in cash.
  - **LTM** = previous FY + YTD − prior-year YTD, at each Q1/H1/9M end.
  - Inputs must be valid values of the same concept and sign (formulas: the same period and unit); reported values always win, and windows must align with the dataset's existing periods. Skips are counted by reason.
- **Mapping v2** = v1 + `CF.FCF` (derived only; CFO + capex), loaded and pinned by migration `0007`. v1 stays loaded, and the default `ERP_MAPPING_VERSION` is now `v2`. LTM is a new period type.
- **Older datasets:** a format-1 dataset can still be checked against its stored rows. The rebuild comparison needs the current code's format (`rebuild_matches = None`), and such a dataset has no stored checks, so it can't be locked; build a new version instead.

## Public-release decisions (2026-11-06)

### D47. Git history rewritten for identity and infrastructure privacy (owner-approved)
- **Rewrite 1:** email → `contact@delicsouza.com`.
- **Rewrite 2:** author name (former local username) → `Adrian Delic de Souza`, plus redaction of home-server infrastructure details from all historical versions of the affected docs.
- **Scope:** all 4 branches. Messages, dates, parent structure and every non-documentation file are byte-identical.
- **Backups and tooling:** verified backups of the state before each rewrite are kept outside the repository. Tool: `git-filter-repo` 2.47.0 in `.tools/`.
- **Identity going forward:** the repository-local `user.email` is set; the global Git config was not changed by the agent.
- **Consequence:** every commit hash changed. The mapping is in `SESSION_LOG.md`.

### D48. Infrastructure details stay out of the repository
- **Excluded:** host identity, hardware models, other services, ports, network topology, tunnel/VPN addresses and host-level security observations. These are not recorded in tracked files.
- **What docs keep:** only the design constraints (resource budget, the app's own ports, isolation, remote-access approach).
- **Enforcement:** `sanitize.py`'s `FORBIDDEN` list is the reference check before publication.

## Pending decisions (need your input)

Status as of 2026-11-06 (end of Session 3).

| # | Decision | Status / needed by |
|---|---|---|
| P1 | SEC User-Agent contact string | **Resolved** 2026-11-06: owner-supplied, in git-ignored `.env.dev` only, passed to the worker only (D42, D45) |
| P2 | Off-server backup destination | Open, **deferred**: needed at M5 (production gate) |
| P3 | Cloudflare tunnel hostname + Access policy | Open: M5 (remote access) |
| P4 | Start the co-resident game server for the co-residency test | Open, **deferred to M5** (owner instruction); never start it without approval |
| P5 | Optional: LAN binding; `tailscale serve` instead of internal CA | Open, optional |
| P6 | Optional: registering free-key data providers | Open: Release 3 |
| P7 | **Git remote (backup/sync)**: `origin` = `adriandelicdesouza/equity-research-platform` (public, created by the owner). | **Resolved** 2026-11-06: all four branches published (P12) |
| P8 | **Git author identity** | **Resolved** 2026-11-06 (D47): history rewritten to `Adrian Delic de Souza <contact@delicsouza.com>`; repository-local `user.email` set |
| P9 | Approval for the first live request to each new provider (Yahoo, US Treasury, Bank of Canada, Damodaran) | Open: before each provider's first live request (M2) |
| P10 | **License** | **Resolved** 2026-11-06: MIT (owner's choice), `LICENSE`, copyright Adrian Delic de Souza |
| P11 | **Public vs internal documentation** structure (`CLAUDE.md`, session logs, handoff, decision log) | **Resolved** 2026-11-06: owner keeps the current structure; internal docs are published as is |
| P12 | **Publication**: push of `main`, `m2-ingestion`, `m0-foundation` and `m1-engine` to `origin` | **Resolved** 2026-11-06: owner-approved; pushed without force, `main` is the default branch |
