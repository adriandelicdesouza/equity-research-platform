# TECHNICAL BLUEPRINT
# Institutional Equity Research & Valuation Platform

**Status:** Revision 2 — **APPROVED** by the project owner on 2026-11-06 (Session 2). Changes after approval are recorded in `DECISIONS.md`.
**Date:** 2026-11-06
**Authority:** Implements `MASTER_BRIEF.md`. Where this document deviates from the brief, the deviation is marked **[DEVIATION]** with rationale.
**Companion documents:** `DECISIONS.md` (finalized decisions and trade-offs), `KNOWN_LIMITATIONS.md` (data coverage, provider dependencies, methodological limitations, unresolved risks).

**Revision 2 changes** (architecture review, 2026-11-06): FCFE no longer uses the EV bridge (§7.3); memory caps reduced to ~1.6 GB with expected vs measured accounting (§1.2); TSX tickers entered manually, automated discovery optional (§4.3); WACC ERP/CRP separation and beta methodology (§7.5); stub-period modes (§7.4); conditional 6-K interim XBRL (§0.5, §4.4); deterministic first/latest-reported selection (§4.4); IFRS 16 / ASC 842 lease treatment (§7.7); financial-institution classification (§7.6); dilution double-count safeguards (§7.7); material conflict resolution (§10.3); secure-cookie behaviour on LAN/Tailscale (§11.1); mandatory off-server backup (§12.4); mathematically independent reference DCF (§13.1); formal valuation output types (§6.6).

---

## 0. Environment Inspection Findings

All findings below were gathered by read-only inspection on 2026-11-06. Nothing was installed, started, stopped or modified.

### 0.1 Host environment

The platform is self-hosted on a small, shared home server. Only the characteristics that constrain the design are recorded here.

| Item | Finding |
|---|---|
| Hardware class | Small-form-factor desktop; 4-core x86-64 CPU |
| RAM | ~7.6 GiB usable; ~6 GiB typically available; small swap |
| Disk | Single 128 GB SSD, ~64 GB free at inspection |
| OS | Ubuntu 24.04-based Linux |
| Docker | 29.x, Compose v2.40, cgroup v2 (systemd driver) — **container memory/CPU limits are enforceable** |
| Toolchain | Git 2.43, Python 3.12, Node 24; the deploying user can run Docker without sudo |

### 0.2 Co-hosted workloads (must not be disturbed)

The server already runs several unrelated services (containers and system services, including on-demand game servers). Their details are intentionally not recorded in this repository. Design consequences:

- **Memory:** an on-demand game server can use ~3.5 GB RAM when running, leaving ~2.5 GB for everything else. The platform's combined container hard limits are therefore set to **~1.6 GB**, leaving ~0.9 GB of margin for the OS and page cache (§1.2).
- **Ports:** the platform uses its own loopback ports (**127.0.0.1:8090** prod, **127.0.0.1:8091** dev) plus an HTTPS listener (§11.1.1); availability was checked before allocation.
- **Isolation:** the platform creates only its own Docker networks and never prunes or modifies other containers, images or services.
- **Remote access** reuses the existing Cloudflare Tunnel/Access and Tailscale infrastructure; any change to them requires the owner's approval (§12.5).

### 0.3 Existing related code

Earlier personal prototypes on the same machine (a small float-based DCF script and a single-file FastAPI market-data app) were reviewed. **Neither is imported**: they lack exact decimals, calculation traces and provenance, and one includes portfolio features that are out of scope. Only lessons were reused, e.g. SEC concept fallback lists (`SalesRevenueNet` → `RevenueFromContractWithCustomer…`) and Bank of Canada Valet series names.

At the start, the repository contained only `CLAUDE.md`, `MASTER_BRIEF.md`, `.gitignore` (already ignoring `.env*`, `data/`, `backups/`, `documents/`, DB files) and one commit.

### 0.4 Free data provider probe results (live requests, 2026-11-06)

| Provider | Endpoint tested | Result | Usable for |
|---|---|---|---|
| **SEC EDGAR XBRL** | `data.sec.gov/api/xbrl/companyfacts/CIK…` | **200**, AAPL 3.9 MB JSON | Historical statements (US GAAP & IFRS) |
| SEC submissions | `data.sec.gov/submissions/CIK…` | **200** | Profile: tickers, exchange, SIC, fiscal year-end, filings list |
| SEC ticker map | `www.sec.gov/files/company_tickers_exchange.json` | **403 with generic User-Agent; 200 with a contact-style UA** | Ticker → CIK resolution. **Requires an operator-supplied User-Agent with contact info** (configured via `.env`, never committed) |
| **Yahoo Finance chart (unofficial)** | `query1.finance.yahoo.com/v8/finance/chart/RY.TO` | **200**, CAD prices, exchange "TOR" | Daily OHLCV, splits, dividends for a **known** US or TSX symbol. **Ticker search/discovery was not tested and is not assumed.** |
| Stooq CSV | `stooq.com/q/d/l/?s=aapl.us` | **Blocked** — now returns a JavaScript proof-of-work page | **Excluded** |
| **US Treasury** | daily par yield curve CSV | **200** (data through 2026-11-05; 10Y = 4.84%) | US risk-free rate |
| **Bank of Canada Valet** | `FXUSDCAD`, `BD.CDN.10YR.DQ.YLD` | **200** (CA 10Y = 3.77% on 2026-11-03) | CAD risk-free, USD/CAD FX |
| FRED CSV (no key) | `fredgraph.csv?id=DGS10` | 200 (first attempt timed out) | Backup rates/macro |
| Damodaran datasets | `pages.stern.nyu.edu/~adamodar/...` | **200** | ERP, country risk premiums, industry betas, synthetic-rating spreads (reference suggestions) |
| SEDAR+ | `www.sedarplus.ca` | 302 to JS application, no public API | Not automatable |

**SEC XBRL coverage verified on real companies:**

| Company | Taxonomy | Forms | History | Notes |
|---|---|---|---|---|
| Apple (US) | us-gaap, 512 concepts | 10-K/10-Q | FY2007–FY2026 (~19 annual periods) | Revenue concept changes over time: `SalesRevenueNet` (→2017), `Revenues` (2016–18), `RevenueFromContractWithCustomerExcludingAssessedTax` (2017→). **5 of 124 net-income periods carry more than one reported value** → restatements are observable via accession number + filed date. |
| Shopify (CA, SEC 10-K filer) | us-gaap | 40-F → 10-K/10-Q | 2015–2026 | USD and CAD units present |
| Canadian National Railway | us-gaap | 40-F / 6-K | 2009–2025 | CAD |
| Royal Bank of Canada | **ifrs-full** | 40-F / 6-K | FY2018–2026 | CAD; a bank (DCF inappropriate — see §7.6) |
| Canadian Natural Resources | **ifrs-full** | 40-F | 2017–2025 | CAD |

### 0.5 Data coverage conclusions (the most important constraint)

1. **US companies:** strong. SEC XBRL gives ~15+ years of annual and quarterly reported facts, free, official, with filing-level provenance. Rate limit: 10 requests/second; contact User-Agent required.
2. **Canadian companies that file with the SEC** (40-F/10-K interlisted issuers — most large caps: banks, rails, energy, Shopify, etc.): annual statements covered, but IFRS filers only from ~FY2017/18 (when SEC began accepting IFRS XBRL), so **~8 years**, not 15.
   - **Interim (quarterly) data for 40-F filers is conditional.** Their quarterly reports are furnished on Form 6-K, where XBRL is not uniformly present. RY and CNI's companyfacts include 6-K observations, but this cannot be assumed for every 40-F filer. Interim availability is detected per company (§4.4) and never presumed.
3. **TSX-only Canadian companies:** **no free, official, structured fundamentals source exists.** SEDAR+ has no API and filings are PDFs; Canada has no mandatory XBRL. These companies require **Excel/CSV import** in Release 1, and deterministic PDF table extraction later (Release 3).
4. **Prices:** the only free source verified to serve TSX daily prices is Yahoo's unofficial chart endpoint, **for a symbol already known**. It is unofficial, undocumented and may break or change terms. It is therefore one adapter behind an interface, with manual CSV price import as the always-available fallback.
5. **TSX ticker discovery:** no verified free discovery source. TSX tickers and exchanges are **entered manually** in Release 1 (§4.3).
6. **Not available free anywhere reliable:** consensus estimates, forward multiples, precedent transaction databases, credit ratings. These are **analyst inputs** in this platform, never fabricated.
7. **Optional free-key providers** (Financial Modeling Prep, Alpha Vantage, Finnhub, Tiingo): free tiers require account registration and have tight daily caps. Not needed for Release 1. Registering would need your approval (§12).

---

## 1. Overall System Architecture

### 1.1 Logical architecture

```
                     Cloudflare Access (remote auth, edge)
                                  │
                         cloudflared (existing, host)
                                  │   (new ingress rule — needs approval)
                     127.0.0.1:8090 / Tailscale / LAN
                                  │
        ┌─────────────────────────┴─────────────────────────┐
        │  web  (Caddy: static React build + reverse proxy) │
        └─────────────────────────┬─────────────────────────┘
                                  │ /api
        ┌─────────────────────────┴─────────────────────────┐
        │  api  (FastAPI + Pydantic + SQLAlchemy)           │
        │   ├─ auth / RBAC / audit                          │
        │   ├─ company, dataset, project, scenario services │
        │   └─ calculation orchestration ──► erp_engine     │◄── pure Python library,
        └───────┬───────────────────────┬───────────────────┘    no I/O, no DB
                │                       │
        ┌───────┴────────┐     ┌────────┴──────────────────────┐
        │  PostgreSQL 17 │◄────│ worker (same image, different │
        │ (authoritative)│     │ command): ingestion, sync,    │
        │  + job queue   │     │ sensitivities, backups,       │
        └────────────────┘     │ exports. erp_engine + DuckDB  │
                               └────────────┬──────────────────┘
                                            │
                     Filesystem (DATA_ROOT): raw provider payloads,
                     DuckDB files, documents, exports, backups
```

### 1.2 Physical containers (Release 1)

| Container | Image | Purpose | Hard limit (`mem_limit`) | Expected typical | Expected peak | Measured |
|---|---|---|---|---|---|---|
| `web` | `caddy:2-alpine` | Static frontend, reverse proxy, TLS for LAN/Tailscale, security headers | 48 MB | ~15 MB | ~30 MB | pending (M0) |
| `api` | app image (python:3.12-slim) | HTTP API; runs the engine in-process for interactive calcs; **1 uvicorn process** + threadpool | 384 MB | ~130–180 MB | ~300 MB (concurrent previews + 7×7 grid) | pending (M0) |
| `worker` | same app image | Queue consumer + scheduler, **concurrency 1**; ingestion, large grids, exports, `pg_dump`; DuckDB `memory_limit=192MB`, `threads=2` | 576 MB | ~120–200 MB | ~450 MB (companyfacts parse + DuckDB query) | pending (M0) |
| `db` | `postgres:17-alpine` | Authoritative store + job queue; `shared_buffers=128MB`, `work_mem=4MB`, `maintenance_work_mem=64MB`, `max_connections=25` | 576 MB | ~120–220 MB | ~400 MB (index build / vacuum / dump) | pending (M0) |
| **Total** | | | **1,584 MB (~1.55 GB)** | **~0.4–0.6 GB** | **~1.2 GB** (all peaks coinciding — unlikely) | pending |

- **Expected** figures are estimates from comparable stacks and configuration; they are **not measurements**. The **Measured** column is filled from `docker stats` and cgroup `memory.peak` at M0 (idle + synthetic load) and re-measured at M5 under the acceptance workload. Both columns are kept in `docs/operations/memory.md`; the estimate is never overwritten by the measurement.
- Measured host baseline at inspection (2026-11-06): 1.4 GiB used, 6.2 GiB available, existing containers ≈ 60 MiB combined, systemd Python services ≈ 30–70 MiB each.
- cgroup v2 `memory.max` counts reclaimable page cache; Postgres file cache beyond `shared_buffers` will be reclaimed under pressure rather than causing OOM, but limits are set so the steady state stays well below them.
- **Game-server co-residency test** (M0 and M5): run the acceptance workload while the game server runs and record host available memory, swap activity and per-container `memory.peak`. Starting the game server is starting an existing service, so **I will ask you to start it (or approve me starting and then stopping it)** at that point.
- CPU: `cpus: 1.5` for `api`, `cpus: 1.0` for `worker` and `db`; worker has lower CPU weight (`cpu_shares`) so interactive requests win. All limits come from `.env` so they are tunable without code changes.
- On a memory-limit breach the container is OOM-killed and restarted (`restart: unless-stopped`); jobs are idempotent and retried; the event is logged and surfaced on the Settings → System page.

### 1.3 Key architectural decisions

| # | Decision | Rationale |
|---|---|---|
| A1 | **Calculation engine = separate Python package (`erp_engine`) with zero I/O**, imported by api and worker | Satisfies "dedicated calculation service independent of frontend" as a *module boundary* rather than a network hop. A single DCF run is milliseconds; a separate HTTP service would add a container (~150 MB), serialization and failure modes for no benefit on this hardware. The pure-function interface (typed inputs → typed outputs + trace) means it can be split into its own service later without changes. **[DEVIATION — minor]** from "independently maintainable services" in the physical sense; logical separation preserved. |
| A2 | **No Redis.** Job queue on PostgreSQL via `procrastinate` (uses `SELECT … FOR UPDATE SKIP LOCKED`, supports periodic tasks, retries, locks) | Saves a container and memory; jobs become transactional with application data. Volume of jobs (daily syncs, sensitivity grids) is tiny. |
| A3 | **Document, ingestion and worker responsibilities live in one worker process** in R1–R3 | §5.1 of the brief: separate logical responsibilities, avoid heavyweight containers. Each is a separate Python package/module with its own tests. |
| A4 | **No browser-side financial calculations in R1.** The UI calls a non-persisting `/preview` endpoint (debounced) that runs the authoritative engine | A DCF run is fast enough (<50 ms) to make JS previews unnecessary. Avoids duplicating financial logic in TypeScript (principle 2.4) and eliminates preview/authoritative divergence. Previews are labelled "unsaved". |
| A5 | **Caddy** as gateway (not nginx) | Smaller config, sane security-header defaults, ~15 MB. Built-in internal CA provides HTTPS for the Tailscale/LAN listener (§11.1.1); Cloudflare terminates TLS for remote access. |
| A6 | Monorepo, one app image for api+worker | Reproducible builds, one dependency lock, shared engine version. |

---

## 2. Technology Choices

### 2.1 Backend

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 (matches host; pinned slim image digest) | |
| Dependency management | **uv** with `uv.lock` (installed inside the image and as a dev tool in the project, not system-wide) | Reproducible, fast |
| API | FastAPI, Pydantic v2 | OpenAPI generated automatically |
| ORM / migrations | SQLAlchemy 2.0 (sync, psycopg 3), Alembic | Sync is simpler and adequate; FastAPI runs sync handlers in a threadpool |
| Jobs | procrastinate | §A2 |
| HTTP client | httpx + tenacity (retry/backoff) + per-provider token-bucket limiter | SEC 10 req/s cap enforced client-side (configured at 5 req/s) |
| Analytical | DuckDB (embedded in worker) | §3.6 |
| Excel I/O | openpyxl (values only, never formulas) | CLAUDE.md: no Excel formula engine |
| Logging | structlog → JSON to stdout; Docker `json-file` with per-container rotation (10 MB × 3) | No change to the global Docker daemon config |
| Quality | ruff (lint+format), mypy (`--strict` on `erp_engine`), pytest, hypothesis, pytest-cov | |
| Password hashing | argon2-cffi | |

### 2.2 Frontend

| Concern | Choice | Rationale |
|---|---|---|
| Framework | React 19 + TypeScript (strict) + Vite | Brief mandate |
| Routing | TanStack Router | Typed routes for nested company workspace navigation |
| Server state | TanStack Query | Caching, invalidation after recalculation |
| API types | `openapi-typescript` generated from FastAPI's OpenAPI | Single source of truth for contracts; CI fails if stale |
| Statement grids / editors | **AG Grid Community (MIT)** | Spreadsheet-style keyboard editing, pinned columns, large tables. Used purely for presentation/input — **no formulas**. |
| Charts | **Apache ECharts** | Handles price history (thousands of points), waterfall (equity bridge), heatmaps (sensitivity) |
| Styling | Tailwind CSS with a custom design-token layer + Radix UI primitives | Accessible primitives without a generic SaaS look; light/dark themes from tokens |
| Forms | react-hook-form + zod | |
| Number formatting | Centralized formatter: scale (units/thousands/millions), currency, negatives in parentheses, percent/bp | Consistency across all tables |
| Tests | Vitest + Testing Library (components), Playwright (end-to-end workflow; dev only, browsers not in prod image) | |

---

## 3. Database Architecture and Schema

### 3.1 Principles

1. **PostgreSQL is authoritative.** All financial values that can feed a valuation live there.
2. **Append-only for source data and audit.** Reported facts, provider fetches, valuation snapshots and audit records are never updated in place.
3. **Immutable, versioned financial datasets.** A valuation pins a dataset version; new filings create a new version. This is how restatements never silently alter valuations (brief §7.4, §17.1).
4. **Exact numerics.** Financial amounts are `NUMERIC` (no precision cap) in Postgres and `Decimal` in Python. Never `float` in storage.
5. **Every value carries currency, unit, scale and provenance.**
6. **UUID primary keys** (generated application-side), stable across exports/imports.

### 3.2 Schemas

```
auth      users, roles, user_roles, sessions, api_tokens (future)
ref       exchanges (MIC), currencies, standard_line_items, concept_mappings,
          mapping_versions, reference_datasets (e.g. Damodaran ERP table)
market    companies, securities, company_identifiers, prices, corporate_actions,
          fx_rates, rate_series, rate_observations
source    providers, provider_fetches, filings, reported_facts, imports
fin       fiscal_periods, financial_datasets, financial_values,
          analyst_adjustments, data_conflicts, validation_results
val       valuation_projects, scenarios, assumptions, valuation_runs,
          sensitivity_runs, project_snapshots
audit     audit_events
jobs      (procrastinate-managed)
docs      (Release 3)
```

### 3.3 Core tables (abridged DDL-level description)

**Companies and securities** — a company can have several listings (e.g. Shopify: NYSE `SHOP` and TSX `SHOP`).

| Table | Key columns |
|---|---|
| `market.companies` | id, legal_name, common_name, country (ISO-3166), sector, industry, sic_code, reporting_currency, fiscal_year_end_mmdd, accounting_standard (`US_GAAP`/`IFRS`/`OTHER`), **financial_classification** (§7.6; `suggested` + `confirmed` value with evidence), status, created_by/at |
| `market.securities` | id, company_id, ticker, exchange_mic (`XNYS`, `XNAS`, `XTSE`, `XTSX`), trading_currency, share_class, is_primary, provider_symbols JSONB (`{"yahoo":"RY.TO"}`) |
| `market.company_identifiers` | company_id, scheme (`SEC_CIK`, `LEI`, `ISIN`, `SEDAR_PROFILE`), value, valid_from/to |

**Provenance chain** (§10):

| Table | Key columns |
|---|---|
| `source.providers` | code, name, kind (`official`/`unofficial`/`reference`/`manual`), enabled, priority, rate_limit, markets, categories, config JSONB (no secrets; secrets via env) |
| `source.provider_fetches` | id, provider, url (credentials stripped), params, http_status, retrieved_at, content_sha256, payload_path (zstd file under `DATA_ROOT/raw/`), bytes |
| `source.filings` | id, company_id, accession_no, form, filed_date, period_of_report, fiscal_year, fiscal_period, url, amends_accession |
| `source.reported_facts` | id, company_id, filing_id, fetch_id, taxonomy, concept, unit, value NUMERIC, decimals, period_start, period_end, is_instant, fiscal_year, fiscal_period, dimensions JSONB. **Unique** on (company, taxonomy, concept, unit, period_start, period_end, dimensions, accession). Never updated. |
| `source.imports` | id, kind (`excel`/`csv`/`manual`), original_filename, file_sha256, stored_path, template_version, imported_by/at |

**Standardization and datasets:**

| Table | Key columns |
|---|---|
| `ref.standard_line_items` | code (e.g. `IS.REVENUE`, `BS.CASH`, `CF.CAPEX`), statement, label, parent_code, sign_convention, value_type (`flow`/`stock`/`per_share`/`ratio`/`count`), is_subtotal, subtotal_formula (declarative list of child codes with signs — *data*, not a formula language) |
| `ref.mapping_versions` / `ref.concept_mappings` | version, taxonomy, concept → line_item_code, priority, sign_multiplier, conditions. Source of truth is versioned YAML in the repo, loaded by migration. |
| `fin.fiscal_periods` | id, company_id, fiscal_year, period_type (`FY`,`Q1`–`Q4`,`H1`,`LTM`,`STUB`), start_date, end_date, weeks (52/53) |
| `fin.financial_datasets` | id, company_id, basis (`reported`, `standardized`, `adjusted`), version_no, parent_dataset_id, mapping_version, **selection_policy** (`latest_reported` / `first_reported`), **facts_as_of** (cut-off timestamp: only facts from filings filed ≤ this instant are eligible — makes rebuilds deterministic), **interim_coverage** (`none`/`partial`/`full`), status (`draft`/`locked`), source_summary, created_by/at, locked_at |
| `fin.financial_values` | dataset_id, period_id, line_item_code, value NUMERIC, currency, scale, provenance_kind, source_fact_ids UUID[], import_id, derivation (e.g. `{"rule":"Q4=FY-9M"}`), verified_by/at |
| `fin.analyst_adjustments` | id, dataset_id, period_id, line_item_code, kind (`override`/`delta`/`reclass`), amount, description (required), source_reference (required), created_by/at |
| `fin.data_conflicts` | company, period, line_item, candidate values (provider, value, fetched_at), relative & absolute difference, **is_material**, status (`open`/`resolved`/`overridden`/`auto_resolved_immaterial`), selected candidate, reason (required for `resolved`/`overridden`), resolved_by/at |
| `fin.validation_results` | dataset_id, check_code, period_id, severity, expected, actual, difference, message |
| `fin.capital_instruments` | id, company_id, as_of_date, kind (`option_tranche`, `warrant`, `rsu`, `psu`, `dsu`, `convertible_debt`, `convertible_preferred`, `preferred`, `exchangeable_share`), count, strike/conversion price, face value, currency, source (provenance), **dilution_treatment** (§7.7), **claim_treatment** (§7.7) |
| `fin.share_counts` | company_id, as_of_date, basic_shares_outstanding (point-in-time, e.g. `dei:EntityCommonStockSharesOutstanding` from the cover page), source; **weighted-average counts are stored only as statement line items and never used as the valuation base** |

**Market data:**

| Table | Key columns |
|---|---|
| `market.prices` | security_id, date, provider, open, high, low, close (unadjusted), adj_close, volume, fetch_id. PK (security, date, provider). |
| `market.corporate_actions` | security_id, ex_date, kind (`split`, `reverse_split`, `cash_dividend`, `special_dividend`), ratio_num/ratio_den, amount, currency, provider |
| `market.fx_rates` | base, quote, date, rate, provider |
| `market.rate_series` / `rate_observations` | series code (`UST_10Y`, `GOC_10Y`), currency, tenor; date, value, provider |

**Valuation:**

| Table | Key columns |
|---|---|
| `val.valuation_projects` | id, company_id, name, thesis_text, status, owner, dataset_id (pinned), created/updated |
| `val.scenarios` | id, project_id, name, kind (`base`/`bull`/`bear`/`custom`), parent_scenario_id (inheritance), probability NUMERIC, sort_order |
| `val.assumptions` | id, scenario_id, key (e.g. `revenue_growth`), period_index (nullable = all periods), value NUMERIC/JSONB, unit, provenance_kind (`analyst`, `suggested`, `provider`), source_ref, **status (`suggested`/`approved`)**, approved_by/at, note |
| `val.valuation_runs` | id, project_id, scenario_id, run_kind (`preview` not stored; `saved`), dataset_id, **input_document JSONB (fully resolved canonical inputs)**, input_sha256, engine_version, module_versions JSONB, settings JSONB, **methodology, primary_output_kind** (§6.6), outputs JSONB (typed `ValuationOutput` records), trace_path (zstd JSON), validation JSONB, output_sha256, created_by/at. **Immutable.** |
| `val.sensitivity_runs` | id, valuation_run_id, config JSONB (axes, ranges, output metric), results JSONB, engine_version, created_by/at |

**Audit:**

`audit.audit_events` — id, occurred_at, user_id, entity_type, entity_id, action, before JSONB, after JSONB, reason, request_id, valuation_impact JSONB (when computable). The application DB role has `INSERT`/`SELECT` only on this table; a trigger rejects `UPDATE`/`DELETE`. Migrations run with a separate owner role.

### 3.4 Why immutable datasets (the most expensive decision to change later)

When a company files a restatement or a new 10-K:
1. New raw facts are appended to `reported_facts`.
2. A new *draft* `standardized` dataset version is built (parent = previous version), and a diff against the previous version is produced (changed values, new periods).
3. Existing projects stay pinned to their dataset. The UI shows "newer data available (3 restated values)"; the analyst explicitly re-bases.
4. Saved `valuation_runs` store the fully resolved input document, so reproduction never queries "latest" data.

### 3.5 Storage estimate

Raw SEC companyfacts ≈ 4 MB JSON per company (~0.5 MB zstd). 500 companies ≈ 250 MB raw archive. `reported_facts` for 500 companies ≈ 10–20 M rows worst case if all concepts are stored → **only facts for mapped concepts plus dimensionless facts are loaded** into Postgres (full raw kept on disk). Prices: 20 years × 252 days ≈ 5 k rows/security. Expected total for a few hundred companies: **< 5 GB**. Disk guard: worker refuses bulk ingestion when free space < 10 GB and the UI shows usage.

### 3.6 Role of DuckDB

DuckDB is **not** a second copy of authoritative data. Uses:
- Querying raw archived provider payloads (JSON/Parquet) during mapping development and coverage analysis.
- Analytical workloads over Postgres data via DuckDB's `postgres` extension (bundled at image build — no runtime download) — e.g. beta regressions, peer-universe statistics (R4), Monte Carlo result sets (R4) written as Parquet.
In Release 1 its use is minimal (coverage analysis, beta regression). If it is not earning its place by Release 3 I will say so.

---

## 4. Financial Data Ingestion and Normalization

### 4.1 Adapter interface

```python
class ProviderAdapter(Protocol):
    code: str
    capabilities: ProviderCapabilities  # markets, categories, rate limit, kind
    def resolve(self, query: str) -> list[ListingCandidate]: ...
    def fetch_profile(self, ids: CompanyIds) -> RawPayload: ...
    def fetch_fundamentals(self, ids: CompanyIds) -> RawPayload: ...
    def fetch_prices(self, symbol: str, start: date, end: date) -> RawPayload: ...
    def parse_*(self, payload: RawPayload) -> list[Normalized…]: ...
```

Fetching and parsing are separated: every fetch is archived (`provider_fetches` + file with SHA-256) **before** parsing, so parsing can be re-run on historical payloads when mappings improve.

### 4.2 Release 1 adapters

| Adapter | Data | Markets |
|---|---|---|
| `sec_edgar` | ticker→CIK map, submissions (profile), companyfacts (fundamentals) | US + SEC-registered Canadian |
| `yahoo_chart` (unofficial) | daily OHLCV, adjusted close, splits, dividends | US, TSX, TSXV |
| `us_treasury` | daily par yield curve | USD risk-free |
| `bank_of_canada` | GoC benchmark yields, USD/CAD and other FX | CAD risk-free, FX |
| `fred` (fallback) | rate series | USD |
| `damodaran` | ERP, CRP, industry betas, rating→spread table (annual reference datasets) | reference |
| `excel_import` / `csv_import` | statements and prices from a documented template | any |
| `manual` | UI entry | any |

### 4.3 Company resolution (Ticker → Company)

1. Search local `securities` first (name, ticker, exchange).
2. Else SEC `company_tickers_exchange.json` (cached daily) → creates company + security + `SEC_CIK` identifier, enriched from `submissions` (legal name, SIC, fiscal year-end, exchanges, former names). This covers US listings and SEC-registered Canadian issuers (whose SEC entry lists the US ticker; the TSX listing is added as a second security).
3. **Manual listing entry (always available, the Release 1 path for TSX/TSXV):** the analyst enters ticker, exchange (MIC: `XTSE`, `XTSX`, `XNYS`, `XNAS`, …), trading currency and company profile fields (legal name, country, fiscal year-end, accounting standard, reporting currency). Provider symbols are **derived deterministically** from ticker + MIC by a documented per-provider rule (e.g. Yahoo: `XTSE` → `<TICKER>.TO`, `XTSX` → `<TICKER>.V`, share classes `RCI.B` → `RCI-B.TO`), and can be overridden by the analyst.
4. **Symbol validation, not discovery:** after manual entry, the price adapter fetches a short price history for the derived symbol and shows the returned currency, exchange name and last close for the analyst to confirm. A mismatch (e.g. returned currency ≠ entered currency) blocks confirmation. This uses only the chart endpoint verified in §0.4.
5. **Automated TSX discovery is optional and disabled by default.** Candidate sources (Yahoo search endpoint, TMX listed-issuer files) have **not been verified**; each would be added as a separate `discovery` adapter only after it is tested and documented, behind a feature flag. Fundamentals for TSX-only companies come from Excel/CSV import and the UI says so.
6. Accounting standard inferred from taxonomy (`ifrs-full` → IFRS) and flagged as *inferred* until confirmed.

### 4.4 SEC standardization pipeline

1. **Select facts:** non-dimensional facts, from 10-K/10-K/A/20-F/20-F/A/40-F/40-F/A (annual) and 10-Q/10-Q/A (quarterly). **6-K facts are used only where present and only after passing the interim checks below** — their existence is conditional, not assumed. Annual duration test: 350–380 days (handles 52/53-week years); quarterly 84–98 days; year-to-date durations recognized explicitly; instant facts for balance sheet.
   - **Interim coverage detection** per company: for each of the last 8 fiscal quarters, record whether a complete interim set (revenue, operating income, net income, cash, total assets, CFO YTD) exists and from which form. Result stored as `interim_coverage` (`none`/`partial`/`full`) on the dataset and shown in the UI. Features requiring interim data (LTM metrics, partial-period stubs, §7.4) check this flag and degrade with an explicit warning rather than silently.
   - 6-K-sourced facts carry `form = 6-K` in provenance; because 6-K exhibits are furnished, not filed, and may be unaudited press-release data, they are labelled as such.
2. **Fiscal period assignment** from the period end date and the company's fiscal year-end — *not* from the XBRL `fy` field, which reflects the filing's fiscal year and is unreliable for prior-period comparatives.
3. **Concept mapping:** per standard line item, an ordered list of candidate concepts per taxonomy (versioned YAML). First present concept wins for a given period; which one won is recorded. Example `IS.REVENUE` (us-gaap): `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues` → `SalesRevenueNet` → `RevenueFromContractWithCustomerIncludingAssessedTax`.
4. **Value choice per period — deterministic selection rule.**
   - **Fact key:** (company, taxonomy, concept, unit, period_start, period_end, dimensions). The candidate set for a key is every `reported_fact` with that key from a filing with `filed_at ≤ facts_as_of` (the dataset's cut-off).
   - **Total order on candidates** (applied identically every time; ties are impossible because accession numbers are unique):
     1. `filed_date` (ascending)
     2. form rank: original before amendment (`10-K` < `10-K/A`, `40-F` < `40-F/A`, …) for the same filed date
     3. `accession_no` (lexicographic ascending)
   - **First-reported** = the minimum of this order. **Latest-reported** = the maximum.
   - **Within-filing duplicates:** if one filing reports the same key twice with different values, the values are compared after rounding to the coarser `decimals`; if they still differ, the key is marked `ambiguous`, neither value is used automatically, and a material conflict (§10.3) is opened.
   - **Restatement detection:** a key whose first- and latest-reported values differ by more than the rounding implied by `decimals` is a restatement; it is listed with both values, both accessions and the difference.
   - **Concept fallback per period:** for each line item and period, the highest-priority mapped concept that has at least one candidate under the cut-off wins; the selected concept and policy are recorded on the value. Fallback is evaluated per period, never mixed within one value.
   - The dataset's `selection_policy` (default `latest_reported`) and `facts_as_of` are stored, so rebuilding the dataset from the archived facts reproduces it exactly. A `first_reported` (point-in-time) dataset can be built on request, e.g. to reconstruct what was known at a past date.
5. **Derivations** (marked `derived`, never confused with reported): gross profit = revenue − COGS when not reported; Q4 = FY − 9M YTD; EBITDA = EBIT + D&A; free cash flow; net debt.
6. **Sign normalization** to the line-item's convention (e.g. `PaymentsToAcquirePropertyPlantAndEquipment` positive → `CF.CAPEX` negative).
7. **Integrity checks** (§5.3) run; failures stored as `validation_results` and shown in the UI.
8. **Coverage report:** which line items are populated per period, which are missing, which are derived.

Mappings are tested against real archived payloads committed as **small, trimmed test fixtures** (public SEC data; a few companies: AAPL, MSFT, SHOP, CNI, CNQ, a 52/53-week filer, a company with a restatement).

### 4.5 Market data handling

- Store the provider's unadjusted close and adjusted close separately; store corporate actions separately.
- Recompute adjusted series ourselves from actions and compare with the provider's adjusted close; mismatches > 0.5% are flagged.
- Valuation "current price" is a specific dated close stored in the run's input document — never "live".
- Daily schedule (worker, off-hours, staggered): prices after market close (~18:30 ET), rates/FX (~17:00 ET), SEC submissions check for tracked companies (new filings → draft dataset version, notification in UI). All idempotent.

### 4.6 Excel/CSV import

A versioned template (statements in rows = standard line-item codes, columns = fiscal periods; metadata sheet: company, currency, scale, accounting standard, source document & page). Importer validates codes, periods, numeric types, scale, and runs integrity checks before creating a `reported` dataset with `provenance_kind = manual_import` linked to the stored original file (hash-verified).

---

## 5. Historical Financial Statements

### 5.1 Three layers, always distinguishable (brief §8.5)

| Layer | Content | Editable? |
|---|---|---|
| Reported | Facts as filed, with concept name, filing, accession, filed date | Never |
| Standardized | Mapped to the standard line-item taxonomy, with derivations | Only by creating a new version (re-mapping, manual fill of missing items with source) |
| Adjusted | Standardized + analyst adjustments (each with description, source, user, timestamp) | Via adjustments only |

### 5.2 Standard line-item taxonomy (Release 1 scope)

Sufficient for an industrial/commercial company DCF:

- **IS:** revenue, COGS, gross profit, SG&A, R&D, other opex, D&A (where separately disclosed), EBITDA (derived), EBIT/operating income, interest expense, interest income, other non-operating, pre-tax income, income tax, NCI share, net income to common, basic & diluted EPS, basic & diluted weighted shares, SBC.
- **BS:** cash & equivalents, short-term investments, receivables, inventory, other current assets, PP&E net, ROU assets, goodwill, intangibles, other non-current assets, total assets; payables, accrued liabilities, short-term debt & current LTD, current lease liabilities, other current liabilities, long-term debt, non-current lease liabilities, deferred tax liabilities, other non-current liabilities, total liabilities; preferred equity, common equity, NCI, total equity; shares outstanding.
- **CF:** CFO, D&A, SBC, change in working capital, capex, acquisitions, CFI, debt issued/repaid, buybacks, dividends, CFF, FX effect, net change in cash.
- **Leases (both standards, §7.7):** operating lease cost (ASC 842), operating lease liabilities current/non-current (ASC 842), finance lease liabilities current/non-current (ASC 842), lease liabilities current/non-current (IFRS 16), ROU asset depreciation, interest on lease liabilities, lease principal payments (CFF), variable/short-term lease expense where disclosed.
- **Point-in-time share data:** basic shares outstanding at cover-page date (separate from weighted averages, §7.7).

Codes are **stable identifiers**; Release 2 extends (never renames) them.

### 5.3 Integrity checks (Release 1)

| Check | Rule | Tolerance |
|---|---|---|
| Balance | Assets = Liabilities + Equity | max(0.1% of assets, 1 unit of reporting scale) |
| Subtotals | Each subtotal = signed sum of children (where all children present) | same |
| Cash roll-forward | CFO + CFI + CFF + FX = Δcash (vs BS cash or CF-statement cash incl. restricted) | same |
| Cross-statement | NI(IS) = NI starting line (CF); D&A IS vs CF where both reported | same |
| Shares | Diluted ≥ basic; EPS × shares ≈ NI | 1% |
| Completeness | Items required by DCF present for N historical years | — |

Failures never block viewing; they block **approval** of a dataset for valuation unless the analyst records an override reason.

---

## 6. Valuation Engine Architecture (`erp_engine`)

### 6.1 Package layout

```
engine/src/erp_engine/
  core/
    numbers.py       Decimal context (prec=34, ROUND_HALF_EVEN), quantization rules
    units.py         Currency, Scale, Unit (money / ratio / count / per_share / multiple)
    periods.py       FiscalPeriod, PeriodIndex, year fractions, stub handling
    series.py        PeriodSeries[Decimal] with unit+currency, alignment checks
    trace.py         TraceRecorder, TraceNode (step id, label, inputs, output, rule)
    validation.py    Check, Severity, ValidationReport
    registry.py      Module registry, dependency DAG (graphlib), cycle detection
    errors.py        Structured, typed errors (no bare exceptions crossing API)
    version.py       ENGINE_VERSION + per-module MODULE_VERSION
  statements/        line-item catalog, historical analysis (growth, margins, ratios)
  forecast/          drivers → revenue, opex/EBIT, D&A, capex, NWC, tax schedules
  wacc/              CAPM, ERP/CRP composition, beta (regression, adjusted, bottom-up), cost of debt, weights
  dcf/
    fcff.py, fcfe.py, discounting.py, stub.py, terminal.py, leases.py
  bridge/            EV → equity (the only EV→equity conversion in the engine)
  dilution/          instrument register, TSM/if-converted, per-share conversion (the only equity → per-share conversion)
  outputs.py         ValuationOutput types (§6.6)
  scenarios/         inheritance resolution, probability weighting
  sensitivity/       1-D, 2-D grids, tornado
  api.py             run_valuation(inputs: ValuationInputs) -> ValuationResult
```

### 6.2 Execution contract (brief §16.3)

Each module implements:

```python
class Module(Protocol):
    name: str
    version: str
    requires: frozenset[Key]
    provides: frozenset[Key]
    def run(self, ctx: CalcContext) -> None      # reads requires, writes provides + trace
    def validate(self, ctx: CalcContext) -> list[Check]
```

`run_valuation` resolves the DAG, executes modules in topological order, runs output validation, and returns `ValuationResult(outputs, trace, validation, engine_version, module_versions, input_hash, output_hash)`.

- **Deterministic:** no clock, no randomness (except R4 Monte Carlo with explicit seed), no I/O, no global state. Inputs are a fully resolved Pydantic document; identical inputs + versions → byte-identical canonical output (asserted in tests).
- **No formula language:** calculations are Python functions. "Dependency resolution" operates on named financial quantities between modules, not on cells.
- **Circularity:** the DAG forbids cycles. Genuine financial circularity (interest ↔ average debt ↔ cash sweep, R2) is solved inside one module by an explicit fixed-point iteration with recorded tolerance and iteration count in the trace. Release 1 avoids it by computing interest on opening balances (documented convention).

### 6.3 Numerics — decision and trade-off

- **Decimal everywhere in the authoritative path.** Reason: exact reproducibility across platforms, exact reconciliation of bridges and statement sums, and no float rounding surprises in audit diffs. Cost: ~20–50× slower than float, irrelevant at single-run scale (a 10-year DCF ≈ a few hundred Decimal operations).
- Non-integer powers (mid-year discounting `(1+r)^(t-0.5)`, stub fractions) use `Decimal.exp`/`ln` at precision 34 — deterministic.
- **Monte Carlo (R4)** uses a vectorized NumPy float64 path; it is validated against the Decimal path (same inputs → agreement within 1e-9 relative) and labelled as such.
- Presentation rounding happens only in the UI/export layer; stored outputs are unrounded.

### 6.4 Trace

Every important output is a `TraceNode`: `id` (e.g. `dcf.fcff[2029]`), label, value, unit, period, `rule` (human-readable description of the calculation, e.g. "NOPAT − Capex + D&A − ΔNWC"), input node ids, module + version. The trace is a DAG serialized to JSON (zstd) with each saved run. The UI's "inspect" panel walks it from any number back to assumptions and source facts (through provenance ids carried on historical inputs).

### 6.5 Versioning

`ENGINE_VERSION` (semver) plus per-module versions recorded in every run. Golden-master regression tests store expected outputs **together with the module version**; a change in outputs without a version bump fails CI. A saved run can be re-executed and compared: same version → must match exactly; different version → shows the diff.

### 6.6 Valuation output types

Every methodology declares the **kind of value it produces**. Conversions between kinds happen only in two shared modules, so no methodology implements its own bridge or share count.

```python
class OutputKind(Enum):
    ENTERPRISE_VALUE = "enterprise_value"   # value of operating assets to all capital providers
    EQUITY_VALUE     = "equity_value"       # value to common shareholders (aggregate)
    PER_SHARE_VALUE  = "per_share_value"    # value per diluted common share

@dataclass(frozen=True)
class ValuationOutput:
    kind: OutputKind
    value: Decimal
    currency: str
    valuation_date: date
    methodology: str            # e.g. "dcf_fcff"
    basis: str                  # e.g. "minority, marketable" | "control" (precedents)
    trace_id: str
```

```
EV ──(bridge/: claims & non-operating assets)──► Equity ──(dilution/: diluted shares)──► Per share
```

Rules:
1. A methodology module declares `produces: OutputKind` in its registry entry. The orchestrator, not the methodology, applies the downstream conversions.
2. **Only `bridge/` converts EV → Equity**, and only for methodologies that produce `ENTERPRISE_VALUE`. Feeding an equity-producing method into the bridge is a type error caught by the orchestrator (and by tests).
3. **Only `dilution/` converts Equity → Per share.** Methods that natively produce a per-share value (e.g. a per-share DDM) are not converted back up, except to display an aggregate equity value computed as per-share × the same diluted share count, labelled as derived.
4. Valuation reconciliation (R4) compares and weights methods **only at the same output kind** — by default at `PER_SHARE_VALUE`, after each has passed through the shared conversions. Mixing kinds is rejected.
5. Each output carries `basis` so control-basis values (precedent transactions, LBO) are not averaged with minority-basis values without an explicit, recorded analyst choice.

| Methodology | Produces | Notes |
|---|---|---|
| DCF — FCFF | `ENTERPRISE_VALUE` | Bridge → equity → per share |
| DCF — FCFE | `EQUITY_VALUE` | **No bridge** (§7.3) → per share |
| DDM (aggregate dividends) | `EQUITY_VALUE` | R4 |
| DDM (per-share dividends) | `PER_SHARE_VALUE` | R4 |
| Residual income / excess return (incl. banks, insurers) | `EQUITY_VALUE` | R4 |
| Trading comps — EV multiples | `ENTERPRISE_VALUE` | R4 |
| Trading comps — P/E, P/B, P/S | `EQUITY_VALUE` | R4 |
| Precedent transactions | `ENTERPRISE_VALUE` (basis = control) | R4 |
| SOTP | `ENTERPRISE_VALUE` per operating segment; segments declared `EQUITY_VALUE` (e.g. a financial subsidiary, listed stake) are added **after** the bridge, never inside it | R4 |
| Asset-based / NAV | `EQUITY_VALUE` (assets net of all liabilities) | R4 |
| LBO | `ENTERPRISE_VALUE` (entry EV consistent with target IRR) — returns metrics are separate non-value outputs | R4 |
| Reverse DCF | none — produces implied assumptions | R4 |

---

## 7. Integrated DCF Methodology (Release 1)

### 7.1 Forecast drivers

Per forecast year, per scenario (value can be a single value applied to all years, a linear fade from year *a* to *b*, or explicit per-year values — all stored as explicit per-year values after resolution):

| Driver | Default suggestion (status = `suggested` until approved) |
|---|---|
| Revenue growth % | Historical 3/5-year CAGR shown as context; **no automatic default becomes an input without approval** |
| EBIT margin % (or EBITDA margin % + D&A % revenue) | Historical average shown |
| D&A % revenue | Historical |
| Capex % revenue | Historical |
| NWC % revenue (or days: DSO/DIO/DPO) | Historical |
| Cash tax rate % | Historical effective rate; statutory shown |
| SBC treatment | Treated as cash expense (default) or added back with dilution — explicit switch, documented |
| Horizon | 1–20 years (default 10) |

Forecast horizon start = first fiscal year after the last historical year in the pinned dataset.

### 7.2 FCFF

```
EBIT_t        = Revenue_t × EBIT margin_t
Taxes_t       = EBIT_t × tax rate_t        (no tax credit on negative EBIT unless NOL option on — R2)
NOPAT_t       = EBIT_t − Taxes_t
FCFF_t        = NOPAT_t + D&A_t − Capex_t − ΔNWC_t
ΔNWC_t        = NWC_t − NWC_{t−1}, NWC_0 from last historical balance sheet
```

### 7.3 FCFE

```
InterestExp_t  = Kd_pre-tax × Debt_{t−1}               (opening-balance convention, R1)
InterestInc_t  = r_cash × Cash_{t−1}                    (opening-balance convention, R1)
NI_t           = (EBIT_t − InterestExp_t + InterestInc_t) × (1 − tax rate_t)
NetBorrowing_t = Debt_t − Debt_{t−1}                    (debt path: constant, target D/E, or explicit)
FCFE_t         = NI_t + D&A_t − Capex_t − ΔNWC_t + NetBorrowing_t
TV_equity      = FCFE_{N+1} / (Ke − g)                  (debt grows at g in the terminal period)
Equity value   = Σ PV(FCFE_t at Ke) + PV(TV_equity at Ke)
```

**FCFE discounted at the cost of equity produces equity value directly** (`produces = EQUITY_VALUE`, §6.6). It does **not** pass through the EV-to-equity bridge: cash, non-operating assets, debt and other claims are **not** added or subtracted afterwards, because their economic effect is already in the FCFE stream (interest income on cash, interest expense and net borrowing). Adding cash on top would double-count it.

Consequences, made explicit in the methodology doc and UI:
- Cash held by the company contributes to FCFE value only through modelled interest income and any modelled cash release. If the analyst believes the company holds genuinely excess cash that will be distributed, it must be modelled as a forecast distribution or drawdown in the cash path — not as a post-hoc add-on.
- Non-operating assets (e.g. equity-method investments) contribute only via their modelled income (equity-method income / dividends received) in NI.
- Preferred dividends and NCI share of income are deducted in NI to common in FCFE; preferred and NCI are therefore not subtracted again.
- Per-share conversion still goes through `dilution/` (§7.7) — dilution is not a bridge item.
- The **EV-to-equity bridge is exclusive to FCFF** (and other `ENTERPRISE_VALUE` methods).

FCFF and FCFE values are shown side by side with an explicit reconciliation table; they are **not** expected to be equal unless the capital structure, cash and interest assumptions are mutually consistent, and the UI says why they differ.

### 7.4 Discounting conventions

**Dates.**
- **Balance-sheet date `B`:** the date of the balance sheet whose net debt, cash, NWC and share data feed the model (latest fiscal year-end, or latest interim quarter-end when interim data is used).
- **Valuation date `V`:** explicit input (default: date of the market price used). `V ≥ B` is required.
- Cash flows are forecast from `B` forward. **The model values the company at `B` and rolls the result forward to `V`** (below). This keeps the cash flows, the balance sheet used in the bridge and the discounting window on the same start date, so cash generated between `B` and `V` is neither lost nor double-counted.

**Stub period** (`B` is not a fiscal year-end, i.e. an interim balance sheet is used). Two explicitly different modes; the mode is a recorded setting and shown on every output:

| | **Mode A — Prorated annual** | **Mode B — Actual partial-period** |
|---|---|---|
| Stub cash flow | `FCF_stub = f × FCF_FY1`, `f = days(B → FYE₁) / days(FY₁)` | `FCF_stub = FCF_FY1(forecast full year) − FCF_YTD(actual, FY start → B)` for each flow component (EBIT, taxes, D&A, capex) |
| ΔNWC in stub | `f × ΔNWC_FY1` | `NWC_FYE1(forecast) − NWC_B(actual interim balance sheet)` |
| Seasonality | **Ignored** — assumes cash flow accrues evenly through the year | **Captured** — the remaining-year flow is what the annual forecast implies after actual year-to-date results |
| Working-capital timing | **Ignored** — seasonal NWC build/release at `B` is not reflected | **Captured** — starts from actual NWC at `B` |
| Data needed | Annual forecast only | Interim YTD income/cash-flow statement and interim balance sheet at `B` (`interim_coverage` ≠ `none`, §4.4) |
| Default | Fallback only | **Default whenever interim data exists** |

- **Mode A limitations (documented in UI and methodology doc):** misstates value for seasonal businesses (retail, agriculture, tax preparation, heating utilities); if `B` is an interim date at a seasonal NWC peak or trough, prorating double-counts or omits the seasonal swing; it treats a full-year forecast as linear in time; and the stub's tax and capex timing is assumed uniform. When Mode A is used with an interim `B`, a warning is raised; when Mode A is used because interim data is missing, the warning says so.
- **Avoiding the inconsistency entirely:** if no interim data exists, the default is **no stub** — `B` = last fiscal year-end, full forecast years from there, then roll forward to `V`. Mode A is used only if the analyst explicitly selects an interim `B` they entered manually.
- **Mode B consistency checks:** YTD actuals must not exceed the full-year forecast in a way that implies negative remaining revenue (warning); the forecast FY1 must be the same fiscal year as the YTD actuals; interim and annual data must share currency and scale.
- Discount time for the stub: `t_stub = days(B → FYE₁)/365.25` (end-of-period), or `t_stub/2` (mid-period). Subsequent full years: `t = t_stub + k − 1` (−0.5 mid-period). Without a stub, `t = k` (−0.5 mid-period). Day-count convention (Actual/365.25) is recorded.

**Roll-forward from `B` to `V`.**
- FCFF: equity value at `B` is compounded to `V` at the cost of equity: `Equity_V = Equity_B × (1 + Ke)^{(V−B)/365.25} − dividends with ex-date in (B, V]` (per-share dividends × shares). Rolling at Ke rather than WACC is used because the roll-forward is applied after the bridge, at the equity level.
- FCFE: same formula applied to the FCFE equity value.
- `V − B > 183 days` raises a warning (stale balance sheet); the gap and roll-forward factor are in the trace.

**Discount factors and terminal value.**
- Discount factor: `DF_t = 1 / Π (1 + WACC_i)^{Δt_i}` — supports a **per-period WACC vector** (changing capital structure); constant WACC is the special case.
- Terminal value discounting: Gordon TV follows the cash-flow convention (mid-period TV at the final mid-period time when mid-period discounting is on; end-of-period otherwise), with end-of-period as a recorded conservative override; exit-multiple TV always at the end of the final period. *(D27 as amended 2026-11-06; see `docs/methodology/dcf.md` §6.2.)*

### 7.5 WACC

```
Ke   = Rf + βL × MatureERP + CRP_term + SpecificPremium
βL   = βU × [1 + (1 − t) × D/E]                    (Hamada, debt beta = 0; default)
Kd   = Rf + credit spread  |  yield on debt  |  synthetic rating (interest coverage → spread)
WACC = E/V × Ke + D/V × Kd × (1 − t) + P/V × Kp     (V = D + E + P, market values)
```

#### 7.5.1 Risk-free rate
- In the **currency of the cash flows**: USD → UST 10Y (Treasury.gov); CAD → GoC 10Y (Bank of Canada). Mismatched currency between Rf and cash flows is a validation error.
- Rf must be a **default-free** rate. For USD and CAD sovereigns this holds. If a future market uses a local-currency sovereign yield containing default risk, the sovereign default spread must be subtracted from it; a validation rule refuses a CRP when the Rf source is flagged `includes_default_spread` without that adjustment (prevents counting country risk in both Rf and CRP).

#### 7.5.2 Equity risk premium — mature-market vs country-specific
The two are **separate, typed inputs** and never a single "ERP" number:

| Input | Meaning | Typical source |
|---|---|---|
| `mature_market_erp` | ERP for a mature market with no country default risk | Damodaran implied ERP (S&P 500) with edition date; or analyst |
| `country_risk_premium` (CRP) | Additional premium for exposure to riskier countries | Damodaran CRP table (edition date); or analyst |
| `total_country_erp` | Mature ERP + CRP for one country, as published by some sources | Damodaran "total ERP" column |

**Double-counting safeguards (enforced by validation, not convention):**
1. The analyst selects **one ERP basis**: `mature_plus_crp` (default) or `total_country_erp`. With `total_country_erp`, any non-zero CRP input is a **validation error** — it is already embedded.
2. CRP is entered **once**, in exactly one place, via one exposure method (recorded):
   - `lambda` (default when CRP ≠ 0): `CRP_term = λ × CRP`, λ = company's exposure (e.g. share of revenue from that country relative to the average company there; analyst-entered with source).
   - `beta`: `CRP_term = βL × CRP` (country risk scales with beta).
   - `flat`: `CRP_term = CRP` (every company equally exposed).
   Multiple methods cannot be active simultaneously.
3. CRP exposure is based on **operating exposure** (where revenue/assets are), not domicile; a revenue-weighted CRP across countries is supported (weights must sum to 100%).
4. For the US and Canada (Aaa sovereigns), CRP defaults to **0**; a non-zero value requires a reason.
5. If the cost of debt uses a spread that already reflects country risk, the UI shows it so the analyst does not add a separate country adjustment to Kd.
6. The `SpecificPremium` (size, company-specific) is optional, defaults to 0, requires a written rationale, and is displayed separately in the WACC build-up so it is never mistaken for CRP.

#### 7.5.3 Beta — methodology and regression assumptions
Three methods; the chosen one and all parameters are stored with the run.

**(a) Regression beta**
- **Model:** OLS `R_stock,t = α + β × R_index,t + ε_t`.
- **Returns:** simple (not log) **total returns** computed from adjusted closes (dividends and splits included). Raw returns, not excess returns — with weekly/monthly horizons and Rf variance small relative to equity variance, the difference is negligible; the choice is recorded.
- **Frequency/window:** weekly over 2 years (default, ≥ 80 observations required) or monthly over 5 years (≥ 48 observations required). Daily is not offered (non-synchronous trading bias for less liquid TSX names).
- **Sampling:** last trading day of each week/month; both series aligned on common dates; missing days are not filled.
- **Index:** a broad market index in the **same currency** as the stock's trading currency — S&P 500 for US listings, S&P/TSX Composite for TSX listings. **Limitation:** Yahoo's `^GSPC` and `^GSPTSE` are price indices (no dividends); total-return index availability on the free source is unverified. Using a price index lowers index returns by roughly the dividend yield each period, which affects α, not β materially; this is documented and an imported total-return series can replace it.
- **Diagnostics shown:** β, standard error, 95% CI, R², observation count, window dates, and a warning when SE > 0.25 or R² < 0.10 (beta statistically weak — consider bottom-up).
- **Adjustment:** optional **Blume adjustment** `β_adj = 0.67 × β_raw + 0.33 × 1.0` (shrinks toward the market average, reflecting observed mean reversion). Raw and adjusted are both displayed; which one is used is recorded. Vasicek shrinkage may be added later.
- **Leverage consistency:** a regression beta reflects the company's **average capital structure during the window**. To use it at a different target structure it is first unlevered at the window's average market D/E, then relevered at the target D/E — not relevered directly.

**(b) Bottom-up beta**
- Industry **unlevered** beta from Damodaran (dataset region and edition recorded; Damodaran publishes US, Europe, Japan, Emerging Markets, Global etc., **not Canada-specific** — the region choice is the analyst's, with US or Global suggested for Canadian companies). Cash-corrected unlevered beta is preferred where published.
- Relevered at the company's target market D/E with the marginal tax rate.

**(c) Analyst-entered beta** with required source/rationale.

**Relevering formula:** Hamada (`debt beta = 0`, constant D/E) is the default; Harris–Pringle (`βL = βU × (1 + D/E)`, debt beta = 0, no tax adjustment) is selectable. The formula used is recorded and is the same for unlevering and relevering within one run.

#### 7.5.4 Cost of debt and weights
- Kd options: Rf + spread (analyst, with source), yield to maturity on traded debt (analyst-entered), or synthetic rating from interest coverage using Damodaran's coverage→spread table (edition recorded; large-cap vs small-cap table selectable). After-tax at the marginal rate.
- **Weights:** market equity = price at `V` × diluted shares (§7.7); debt = book value (labelled as proxy for market value) or analyst-entered market value; or a target structure. If leases are treated as debt (§7.7), lease liabilities are included in D and the lease rate in Kd, consistently.
- Every WACC input carries provenance: provider value (with date and source), reference-dataset value (Damodaran table + edition date), or analyst assumption. Damodaran values enter as `suggested` and require approval (§10.1).

### 7.6 Terminal value and coherence checks

- **Perpetuity growth:** `TV = FCFF_{N+1} / (WACC − g)` where `FCFF_{N+1}` is either `FCFF_N × (1+g)` or the value-driver form `NOPAT_{N+1} × (1 − g / RONIC)` (selectable; value-driver recommended because it keeps reinvestment consistent with growth). *Default: value-driver form with RONIC as a required analyst assumption (D35).*
- **Exit multiple:** `TV = multiple × EBITDA_N` (or EBIT, Revenue).
- Both always computed when inputs exist; each shows the **implied** counterpart (implied exit multiple from Gordon; implied perpetual growth from the exit multiple).

| Check | Severity |
|---|---|
| g ≥ WACC | Error (calculation refused) |
| g > Rf (proxy for long-run nominal growth) | Warning |
| g < 0 | Info (permitted; tested) |
| TV > 85% of EV | Warning |
| Terminal FCFF ≤ 0 | Warning (TV negative / meaningless) |
| Implied RONIC < WACC with g > 0 (value-destroying growth) | Warning |
| Terminal reinvestment rate inconsistent with g/RONIC | Warning |
| Negative equity value | Displayed, not hidden |
| Company's financial classification is not `non_financial` | Per-class policy below |

#### 7.6.1 Financial-business classification

A single "financial institution" flag is too coarse: a bank, an insurer, a REIT and an insurance broker need different treatment. Classification is **suggested** from evidence and **confirmed by the analyst**; both are stored with the evidence used.

| Class | Typical SIC (from SEC submissions) | XBRL evidence (us-gaap / ifrs-full) | DCF policy in R1 |
|---|---|---|---|
| `bank` (incl. savings institutions, bank holding cos.) | 6021, 6022, 6029, 6035, 6036 | `Deposits`; interest income/expense dominant; IFRS `DepositsFromCustomers`; loan loss provisions | **FCFF blocked.** Debt is raw material, not financing; WACC and EV are not meaningful. FCFE also blocked in R1 (requires regulatory-capital-driven reinvestment model). Excess-return / DDM in R4. Override not permitted in R1. |
| `insurer` (life, P&C, health, reinsurance, title) | 6311, 6321, 6324, 6331, 6351, 6361, 6399 | `PremiumsEarnedNet`, `PolicyholderBenefitsAndClaimsIncurredNet`; IFRS 17 `InsuranceRevenue`, `InsuranceServiceExpenses` | **FCFF blocked**, same reasoning (float, reserves and capital requirements). R4 models. |
| `specialty_lender` (consumer/commercial finance, mortgage lenders, BDCs) | 6141, 6153, 6159, 6162, 6172, 6199 (review) | Finance receivables dominant; funding debt | **FCFF blocked** (lending spread business, like banks). |
| `reit` / real-estate investment entity | 6798; 6500s require review | `RealEstateInvestmentPropertyNet`; IFRS `InvestmentProperty` at fair value | **FCFF permitted with warning:** NAV and FFO/AFFO-based valuation (R4) are typically more appropriate; investment-property fair-value gains must be excluded from operating cash flows (validation). |
| `asset_manager_broker` (asset managers, brokers/dealers, exchanges, insurance *brokers*, investment advice) | 6211, 6282, 6411, 6200s | Fee revenue dominant; client funds/segregated assets on BS | **FCFF permitted with warning:** client cash, segregated funds and regulatory capital must be excluded from the bridge's cash (validation prompts). Broker-dealers with large balance sheets are flagged for review. |
| `investment_holding` (closed-end funds, holding companies, SPACs) | 6770, 6799 | Investments at fair value dominant | **FCFF blocked**; NAV (R4). |
| `non_financial` | everything else | — | Standard |

- The SIC lists above are a starting point and will be checked against the SEC's published SIC code list during M2.
- SIC is evidence, not truth (e.g. SIC 6411 is insurance *agents/brokers*, not risk-bearing insurers; 6500s include operating real-estate companies). Where SIC and XBRL evidence disagree, the suggestion is `needs_review` and DCF actions wait for analyst confirmation.
- IFRS filers (e.g. RY) frequently lack a reliable SIC → XBRL concept evidence is the main signal; the classifier lists which concepts triggered it.
- Policy is data-driven (`ref.classification_policies`), so R4 can enable proper methods without changing the DCF code.

### 7.7 Equity bridge (FCFF only), leases and dilution

```
Enterprise value at B (Σ PV FCFF + PV TV)                    ← FCFF only (§6.6)
+ Cash & equivalents + short-term investments (excl. restricted / client cash)
+ Non-operating assets (equity-method investments, analyst-entered)
− Total debt (ST + LT)                       (excl. convertibles treated as equity — below)
− Lease liabilities                          (only per the lease treatment — §7.7.1)
− Preferred equity                           (excl. convertible preferred treated as equity)
− Non-controlling interests
− Other claims (analyst: after-tax pension deficit, litigation, etc.)
= Equity value at B → rolled forward to V (§7.4)
÷ Diluted shares (dilution/ — §7.7.2)
= Implied share price → vs current price → upside/downside
```

The bridge applies **only** to methodologies producing `ENTERPRISE_VALUE`. FCFE uses no bridge (§7.3).

#### 7.7.1 Lease accounting — IFRS 16 and US GAAP ASC 842

| | IFRS 16 (most Canadian filers) | ASC 842 — operating lease | ASC 842 — finance lease |
|---|---|---|---|
| Balance sheet | ROU asset + lease liability | ROU asset + operating lease liability | ROU asset + finance lease liability |
| Income statement | ROU depreciation (in D&A) + interest (in finance cost) — **no lease cost in EBITDA** | Single straight-line lease cost **in operating expenses (inside EBITDA)** | Amortization (in D&A) + interest — like IFRS 16 |
| Cash flow statement | Principal → **financing**; interest → operating or financing (policy choice, disclosed) | All payments → **operating** | Principal → financing; interest → operating |
| Reported EBITDA | Excludes leases (higher) | Includes lease cost (lower) | Excludes |

The engine requires one explicit setting, `lease_treatment`, and adjusts FCFF, the bridge, WACC and any EBITDA multiple consistently:

| `lease_treatment` | FCFF | Bridge | WACC | EBITDA for exit multiple |
|---|---|---|---|---|
| **`as_debt`** (default for IFRS 16 reporters) | Before all lease payments. ASC 842 operating lease cost is **added back** to EBIT(DA) and replaced by ROU depreciation (lease-adjusted EBIT); IFRS 16 needs no adjustment | Subtract **all** lease liabilities (IFRS 16, ASC 842 operating and finance) | Lease liabilities in D; lease rate in Kd | EBITDA excluding lease cost (IFRS 16-like) — multiple must be on the same basis |
| **`as_operating`** (default for ASC 842 reporters with only operating leases) | After operating lease payments. IFRS 16: ROU depreciation in EBIT is replaced by the full lease cash cost (principal + interest), so FCFF is after all lease payments. ASC 842: operating lease cost already in EBIT; finance leases stay debt-like | IFRS 16: subtract **no** lease liabilities. ASC 842: subtract **finance lease** liabilities only | Leases excluded from D | EBITDA after lease cost (pre-IFRS 16 basis) |

Validation rules:
- Required inputs for the chosen treatment must be present (e.g. ASC 842 `OperatingLeaseCost` for `as_debt` on a US filer; IFRS 16 lease principal payments for `as_operating` on an IFRS filer). Missing inputs block the run with a precise message; there is no silent default.
- ROU asset additions are never in capex under `as_debt` (they are financed by the lease liability) and are included as lease cost under `as_operating` — the engine prevents counting them in both.
- Exit multiples and (R4) comparables carry a `lease_basis` tag; using an EBITDA multiple on a different lease basis than the subject's EBITDA is a validation error. This matters directly for US (ASC 842) vs Canadian (IFRS 16) peer sets.
- Data caveat: XBRL concepts for lease cost and lease cash flows are disclosed inconsistently; missing items are surfaced for manual entry with source.

#### 7.7.2 Diluted shares and safeguards against double-counting

**Base:** basic shares outstanding at a **point in time** (cover-page `dei:EntityCommonStockSharesOutstanding` or latest balance-sheet date, from `fin.share_counts`), adjusted for buybacks/issuance between that date and `V` if entered. **The diluted weighted-average share count from the income statement is never used as the base** — it already contains treasury-stock dilution at the period's average price and is a period average, not a point-in-time count.

**Instrument register:** every dilutive instrument (`fin.capital_instruments`, project-overridable) appears **exactly once** and has exactly one treatment:

| Instrument | Allowed treatments (choose one) |
|---|---|
| Options / warrants | TSM at the implied price (default); excluded (reason required) |
| RSUs / PSUs / DSUs | Add shares (unvested included or excluded — explicit); excluded because future SBC is expensed in FCFF (see below) |
| Convertible debt | **Either** `claim` (subtracted at fair/face value in the bridge, not in diluted shares) **or** `if_converted` (shares added, removed from bridge debt, interest added back in FCFE) — the engine picks the higher-value-to-holder treatment by default and records it; analyst can override |
| Convertible preferred | Same mutual exclusivity as convertible debt |
| Exchangeable shares (common in Canadian structures) | Included in basic **or** NCI/claim — never both; source disclosure required |

Engine-enforced safeguards:
1. **Single appearance:** an instrument id may appear in either the bridge claims or the share count, never both (validation error).
2. **SBC consistency:** if SBC is treated as a cash operating expense in FCFF (default), *future* grants are already charged; outstanding instruments are still added. If SBC is added back to FCFF, the analyst must model future dilution explicitly (annual share-count growth); doing neither or both is flagged.
3. **TSM circularity solved exactly:** TSM depends on the implied price → closed form `P = (E + Σ n_i K_i) / (S + Σ n_i)` over the in-the-money tranche set, iterated to a fixed point (≤ number of tranches). Recorded in the trace.
4. **Sanity reconciliation:** implied diluted shares are compared with the latest reported diluted weighted-average count; a difference > 5% (configurable) raises a warning with both figures and the instrument breakdown — a common symptom of a double-count or a missing instrument.
5. **Buyback/issuance dates:** share-count adjustments after the base date must be dated and sourced, and cannot overlap the base date.

**Implied multiples:** EV/Revenue, EV/EBITDA (on the run's lease basis), EV/EBIT, P/E on LTM and NTM bases.

### 7.8 DCF outputs (brief §10.7)

Typed outputs per §6.6 (FCFF: EV → equity → per share; FCFE: equity → per share), implied price, current price (with date and source), upside/downside, TV and TV share of EV, forecast cash-flow table with discount factors and PVs, WACC build-up, terminal growth/multiple, implied multiples, validation panel, trace inspector.

---

## 8. Scenario and Sensitivity Analysis

### 8.1 Scenarios (Release 1)

- Each project starts with Base; Bull/Bear/custom scenarios can inherit from any scenario.
- A scenario stores **overrides only**. Resolution walks the parent chain (cycle-checked) and produces a fully materialized assumption set.
- Saved runs store the materialized inputs, so later edits to a parent never change a saved result.
- Probability weights (must sum to 100% across included scenarios; validated) → probability-weighted value per share and range.
- Scenario comparison view: assumption diff table + output comparison.
- Nested combinations and Monte Carlo: Release 4.

### 8.2 Sensitivity

- **One-variable** (tornado): each key driver flexed ± configurable amount; full engine re-run per point.
- **Two-variable grids:** WACC × g, WACC × exit multiple, revenue growth shift × EBIT margin shift, margin × reinvestment (capex % revenue). Default 7×7, up to 15×15.
- Every cell is a **full authoritative engine run**, not an approximation. 49 runs ≈ well under a second; grids > 100 cells run as background jobs with progress.
- Grid cells that violate hard checks (g ≥ WACC) show "n/a" with the reason, never a number.
- Results are stored with the saved run and exported.

---

## 9. Comparable Company Analysis (Release 4 design, constrained by Release 1 schema)

- Peer set per project (manual selection from companies in the database; no screener, per brief §6.1).
- Metrics computed from each peer's **pinned standardized dataset** (same line-item taxonomy, so normalization is shared, not duplicated): LTM = latest FY + current YTD − prior YTD (from quarterly facts).
- Market values at a single as-of date; peers converted to the subject's currency using `fx_rates` on that date.
- Calendarization to the subject's fiscal year-end by time-weighting adjacent fiscal years.
- Multiples: EV/Revenue, EV/EBITDA, EV/EBIT, P/E, P/B, P/S, FCF yield. Negative or tiny denominators → "NM", excluded and listed.
- Statistics: min, Q1, median, mean, Q3, max, with optional analyst exclusions (recorded with reason).
- Applied to the subject's metrics → implied price range, feeding valuation reconciliation.
- **Limitation:** no free consensus estimates → forward multiples require analyst-entered peer forecasts (marked as such). Peers must be SEC filers or imported.

---

## 10. Data Provenance and Source Auditing

### 10.1 Provenance kinds (shown as badges in every table)

| Kind | Meaning |
|---|---|
| `reported` | Directly from a filing (concept, accession, filed date, fetch hash) |
| `derived` | Calculated from reported values by a documented rule |
| `manual_import` | From an imported file (file hash, sheet, cell range, source doc/page from metadata) |
| `analyst_adjusted` | Adjustment with description, source and user |
| `analyst_assumption` | Forward-looking input entered/approved by an analyst |
| `suggested` | System-computed suggestion (e.g. historical average, Damodaran beta) **not yet approved** — cannot be used in a saved valuation until approved |
| `provider_market` | Price/rate/FX with provider, date, fetch id |

### 10.2 Chain

`valuation_run.input_document` → each input carries `{value, provenance_kind, ref}` → `financial_values` → `reported_facts` → `filings` (accession, SEC URL) → `provider_fetches` (URL, timestamp, SHA-256, archived payload). Clicking any number in the UI follows this chain.

### 10.3 Conflicts

When two providers (or provider vs import, or two facts within one filing) supply different values for the same item and period, all candidates are stored and a `data_conflicts` record is opened. Nothing is silently overwritten.

**Materiality** (thresholds configurable, defaults below, recorded on the conflict):
- Material if `|a − b| > max(0.5% × |larger value|, 0.05% × the period's total assets or revenue)`; any sign disagreement is material; any conflict on a line item that feeds the DCF directly (revenue, EBIT, D&A, capex, NWC items, cash, debt, lease liabilities, share counts) at a lower 0.1% threshold.
- Immaterial conflicts are auto-resolved by provider priority, marked `auto_resolved_immaterial`, and remain visible in the conflicts list.

**Material conflicts require one of:**
1. **Resolution** — analyst selects a candidate (or enters a value with source), with a reason; or
2. **Documented override** — analyst proceeds without resolving, recording a reason; the override is shown on every valuation that uses the dataset.

**Enforcement:** a standardized dataset with open material conflicts **cannot be locked**, and a saved valuation cannot be created from an unlocked dataset. Draft previews still work, with a prominent "unresolved material conflicts" indicator. Resolutions and overrides are audit events.

### 10.4 Audit

All mutations go through service functions that write `audit_events` in the same transaction (before/after, user, reason). Assumption changes on a project compute and record the valuation impact (price before/after) when the project has a current run. Audit records are immutable at the database level (§3.3).

---

## 11. Authentication and Security

### 11.1 Layers

1. **Network:** containers on a private Docker network; only `web` publishes ports: **127.0.0.1:8090** (HTTP, loopback only) and **`<tailscale-ip>`:8443** (HTTPS; LAN IP optional, off by default) — §11.1.1. PostgreSQL publishes **no** host port (dev profile may bind 127.0.0.1:5433 for tooling).
2. **Remote access:** a new hostname on the existing Cloudflare Tunnel → `http://localhost:8090`, protected by a Cloudflare Access application (email allow-list). **This requires your approval** and is deferred until Release 1 is deployable.
3. **Application auth (always on, independent of Cloudflare):** local accounts, Argon2id password hashes, server-side sessions in Postgres, idle + absolute expiry, login rate limiting, CSRF protection (Origin allow-list check + token on unsafe methods). Optional: verify Cloudflare's `Cf-Access-Jwt-Assertion` JWT (signature against the team's public keys, audience tag) as defence in depth and to bind the Access identity to the app user.
4. **Local fallback:** when Cloudflare or the internet is unavailable the app works over LAN/Tailscale with application login only (§11.1.1).

#### 11.1.1 Secure cookies on every access path

Browsers do not send `Secure` cookies over plain HTTP to a LAN or Tailscale IP. Rather than weakening the cookie for those paths, **every non-localhost access path is HTTPS**:

| Access path | Origin seen by browser | Transport | Cookie |
|---|---|---|---|
| Remote via Cloudflare | `https://<app-hostname>` | TLS terminated by Cloudflare; `cloudflared` → `http://127.0.0.1:8090` | `__Host-session`; `Secure; HttpOnly; SameSite=Strict; Path=/` |
| Tailscale | `https://<tailscale-ip-or-name>:8443` | **Caddy HTTPS listener** bound to the Tailscale IP, certificate from Caddy's internal CA (`tls internal`) | same |
| LAN (optional, off by default) | `https://<lan-ip>:8443` | same Caddy listener, bound to LAN IP if enabled | same |
| On the server / SSH tunnel | `http://localhost:8090` | plain HTTP on loopback only | same — browsers treat `http://localhost` as a secure context and accept `Secure` cookies there (Chromium, Firefox); Safari's behaviour varies by version, so the documented SSH-tunnel path uses a Chromium/Firefox browser or the HTTPS listener |

- **No configuration ever issues a non-`Secure` session cookie.** There is no "insecure mode" flag in prod; dev uses `localhost` only.
- The Caddy internal CA root certificate must be installed once on each client device that uses the Tailscale/LAN path (documented procedure). This needs no change to Tailscale itself.
- **Alternative needing your approval:** `tailscale serve` / `tailscale cert` provides a publicly trusted certificate for the machine's `*.ts.net` name with no CA installation, but it modifies Tailscale configuration, so it is not the default.
- Caddy sets `X-Forwarded-Proto`; the API trusts forwarded headers **only from the Caddy container** and builds its CSRF Origin allow-list from the configured origins (Cloudflare hostname, Tailscale origin, optional LAN origin, localhost).
- Sessions are per-origin (host-only `__Host-` cookies); logging in through one path does not authenticate another.

### 11.2 Authorization

Roles: `admin`, `analyst`, `reviewer` mapped to permission strings (`company:write`, `valuation:write`, `provider:configure`, `user:manage`, …). Enforced by FastAPI dependencies on every route; a test enumerates all routes and asserts each declares a permission. Reviewers can inspect runs and traces but not mutate.

### 11.3 Secrets

- `.env` (git-ignored, already) per environment; committed `.env.example` with placeholders only.
- DB passwords via Docker secrets files under `secrets/` (to be added to `.gitignore`).
- SEC User-Agent contact string, Cloudflare Access audience tag, future provider API keys: env only.
- Provider URLs are stored with credentials stripped; logs pass through a redaction processor.
- Pre-commit hook: `gitleaks` (or equivalent) secret scan.

### 11.4 Other controls

Security headers at Caddy (CSP with no inline scripts, frame-ancestors none, nosniff, referrer policy), upload size limits, Pydantic validation on all inputs, parameterized SQL only (SQLAlchemy), dependency vulnerability scan (`pip-audit`, `npm audit`) in the check pipeline, containers run as non-root with read-only root filesystems where practical.

---

## 12. Deployment and Infrastructure

### 12.1 Repository layout

```
equity-research-platform/
  engine/            erp_engine package (pure calculation) + tests
  backend/           FastAPI app, services, providers, workers, alembic + tests
  frontend/          React app + tests
  mappings/          versioned XBRL → standard line-item YAML
  templates/         Excel/CSV import templates
  infra/
    compose.yaml     base services
    compose.dev.yaml, compose.prod.yaml   overrides (profiles)
    Caddyfile, postgres.conf
    scripts/         backup.sh, restore.sh, healthcheck, disk-check
  docs/              architecture, database, methodology, API, deployment, backup, ADRs
  Makefile           make check / test / up-dev / up-prod / backup / restore
```

### 12.2 Environments

| | Dev | Prod |
|---|---|---|
| Compose project | `erp-dev` | `erp-prod` |
| Port | 127.0.0.1:8091 (+ Vite 127.0.0.1:5173), loopback only | 127.0.0.1:8090 (HTTP) + `<tailscale-ip>`:8443 (HTTPS) |
| Data root | `<repo>/data/dev/` (git-ignored; was `~/erp-data/dev/`, changed by D29) | `<repo>/data/prod/` (D29) |
| Database | separate instance & volume | separate instance & volume |
| Code | bind-mounted, hot reload | built images, tagged by git SHA |

Dev and prod run as separate Compose projects, so `docker compose down` in one cannot affect the other or any unrelated container. Only one environment needs to run at a time to conserve RAM.

`DATA_ROOT` layout (configurable, movable to external storage/NAS later):

```
postgres/  duckdb/  raw/  imports/  documents/  reports/  exports/  backups/  logs/
```

### 12.3 Operational settings

- `restart: unless-stopped`; health checks on every service (`pg_isready`, `/api/health` checking DB + queue, Caddy).
- Postgres tuned for the hardware and its 576 MB cap: `shared_buffers=128MB`, `effective_cache_size=384MB`, `work_mem=4MB`, `maintenance_work_mem=64MB`, `max_connections=25` (api pool 8 + worker pool 4 + queue 3 + admin headroom).
- Uvicorn: 1 process with threadpool (configurable); worker concurrency 1; DuckDB `memory_limit=192MB`, `threads=2`; daily sync jobs at off-hours with jitter.
- Only one environment (dev or prod) runs at a time by default; running both doubles the footprint (~3.2 GB of caps) and is not supported with the game server running.
- Disk guard job: warn at 80% filesystem use, refuse bulk ingestion below 10 GB free; status shown in Settings.
- Resource monitoring page: container memory/CPU from cgroup stats, DB size, data-root size.

### 12.4 Backups and recovery

- Nightly `pg_dump -Fc` (worker job) → `backups/`, retention 7 daily / 4 weekly / 6 monthly; checksum recorded.
- `raw/`, `imports/`, `documents/` are append-only → incremental copy.
- `restore.sh` restores into a scratch database and runs verification (row counts, re-execute a sample of saved runs and compare output hashes). A restore test is part of each release's acceptance.
- **Off-server backup is mandatory before production acceptance.** The server has a single SSD and no RAID; on-disk backups alone do not survive disk failure, theft or filesystem corruption.
  - Tool: **restic** (encrypted, deduplicated, verifiable snapshots; supports SFTP, local/USB, S3-compatible and rclone back-ends). Repository password in `.env`/secret file, **never** committed; a copy must be stored by you outside the server.
  - Content: nightly `pg_dump`, `raw/`, `imports/`, `documents/`, configuration (excluding secrets), and a manifest of image tags.
  - Candidate destinations (your choice — §12.5): another of your machines over Tailscale via SFTP (no cost, no new service exposure); an external drive that is **rotated off-site** (a permanently attached USB drive does not count as off-server); or a cloud object store (free tiers exist but require account registration → approval).
  - Acceptance gate (M5): at least one successful off-server snapshot, `restic check` passing, and a **restore drill from the off-server copy** into a scratch environment with saved-run hash verification.

### 12.5 Things I will need your approval for (later, not now)

1. Adding a tunnel hostname + Cloudflare Access application.
2. Registering free-tier API keys with any third-party provider.
3. Binding the app to the LAN interface (vs localhost + Tailscale).
4. **Off-server backup destination** (required before production acceptance) and, if cloud, account registration.
5. Starting the game server (or having you start it) for the co-residency memory test.
6. Optional: `tailscale serve`/`cert` instead of Caddy's internal CA.

---

## 13. Testing and Validation

### 13.1 Calculation engine (highest bar)

| Layer | Examples |
|---|---|
| Unit | Every function in `core`, `forecast`, `wacc`, `dcf` |
| **Analytical identities** (property-based, Hypothesis) | Constant FCF, g=0 → EV = FCF/WACC (perpetuity limit); growing perpetuity closed form; annuity PV formula; mid-year PV = end-year PV × (1+WACC)^0.5 for constant WACC; scaling all cash flows by k scales EV by k; EV strictly decreasing in WACC for positive FCF; TSM closed form vs brute-force search; bridge items sum exactly |
| **Mathematically independent reference** | See §13.1.1 |
| **Hand-worked benchmarks** | Fully worked examples with every intermediate number written out in the test file and the methodology doc (computed independently, not from the engine) |
| Edge cases (brief §10.8) | Negative FCF; negative g; g → WACC; high leverage (D/E 5×); negative NI (FCFE, no tax shield); per-period WACC; 52/53-week and non-December fiscal years; stub periods; mid- vs end-year; extreme WACC/g grids |
| Reconciliation | Bridge sums, scenario probability sums, sensitivity center cell = base run exactly |
| **Policy guards** (each has a failing-input test) | FCFE output never passes through `bridge/`; mixing output kinds in reconciliation rejected; CRP with `total_country_erp` rejected; two CRP exposure methods rejected; lease treatment missing inputs / ROU in capex twice / EBITDA lease-basis mismatch rejected; instrument in both bridge and share count rejected; weighted-average shares as base rejected; SBC double/zero treatment flagged; stub Mode A on interim `B` warns; FI classes blocked per policy |
| Determinism | Same inputs → identical output hash, across processes |
| Golden master | Stored outputs per module version; drift without version bump fails |

#### 13.1.1 Independent reference valuation

A reference that re-uses the engine's formulas in a different file only checks typing, not mathematics. The reference is independent in **specification, formulation, arithmetic and code**:

1. **Specification:** written from `docs/methodology/dcf.md` (the conventions document), not from engine code. The engine author does not write the reference test oracles for the same feature in the same change; where that is impossible (single developer), the reference is written first, from the spec, before the engine function exists.
2. **Different formulation** — each check reaches the same value by a different mathematical route:
   - **Backward recursion** instead of forward discount-factor sums: `V_N = TV`, `V_{t−1} = (V_t + CF_t) / (1 + r_t)^{Δt_t}`; EV = `V_0`. Handles per-period WACC and stubs without ever forming a cumulative discount factor.
   - **Economic-profit (EVA) identity:** `EV = Invested capital_B + Σ PV(NOPAT_t − WACC × IC_{t−1}) + PV(continuing economic profit)` must equal the FCFF DCF when FCFF = NOPAT − ΔIC (the standard McKinsey equivalence). This tests the reinvestment, NWC and terminal logic, not just discounting.
   - **APV cross-check** (constant-D/E case): unlevered value + PV(tax shields) vs FCFF-at-WACC, within the known tolerance of the constant-leverage assumption.
   - **Closed forms:** growing annuity + growing perpetuity formulas for constant-growth inputs; TSM fixed point verified by brute-force search over the price grid.
3. **Different arithmetic:** `fractions.Fraction` (exact rationals) for integer-exponent cases and **`mpmath` at 50 digits** for fractional exponents (mid-period, stubs) — not `Decimal`, not the engine's numeric context.
4. **Different code:** `tests/reference/` may not import `erp_engine` (enforced by an `import-linter` contract); it takes its inputs from plain JSON fixtures, not engine types.
5. **Agreement criteria:** engine vs reference within `1e-12` relative for exact-arithmetic cases and `1e-10` for transcendental cases, over Hypothesis-generated input sets that include the §10.8 edge cases.
6. **Hand-worked benchmarks:** at least three fully worked examples (end-year; mid-year with stub Mode B; FCFE with leverage change) with every intermediate number written in the methodology document, computed by hand/independent calculation and checked into fixtures. These are never regenerated from engine output.

### 13.2 Data pipeline

- Parsers tested on archived real SEC payload fixtures (trimmed): concept fallbacks, 52/53-week years, restatement detection, IFRS filer, CAD units, Q4 derivation, 40-F filer with and without 6-K interim XBRL.
- First/latest-reported selection: permutation tests (shuffling fact order never changes the result), same-day original + amendment, within-filing duplicates → ambiguous, `facts_as_of` cut-off reproduces an earlier dataset byte-for-byte.
- Conflict materiality thresholds and lock-blocking behaviour.
- Integrity checks must pass on the standardized fixture companies (e.g. AAPL assets = liabilities + equity for every year).
- Providers mocked at the HTTP layer (respx) — tests never hit the network; a separate opt-in `@live` suite for manual provider health checks.

### 13.3 Backend and frontend

- API: pytest with a real Postgres (testcontainers or the dev DB), permission matrix test, audit-event assertions, migration up/down test.
- Frontend: Vitest component tests (grids, formatters, provenance badges, validation indicators); Playwright E2E for the full Release 1 workflow.
- One command (`make check`) runs lint, types, all tests, and the OpenAPI type-sync check. No CI service is assumed; a git pre-push hook runs it.

---

## 14. Phased Implementation Roadmap

### 14.1 Release 1 — Ticker to sensitivity (DCF)

**[DEVIATION — sequencing]** The brief places provider adapters and standardization in Release 3, but your required first release starts at *Ticker → Company profile → Historical statements*. I will therefore build the **SEC EDGAR adapter, standardization pipeline and price/rate adapters in Release 1**, on the full provider-agnostic framework, while broader provider coverage, documents and quarterly modelling remain in Release 3.

| Milestone | Content | Exit criteria |
|---|---|---|
| **M0 Foundation** (short) | Repo layout, uv/npm locks, compose dev/prod, Postgres + Alembic baseline, Caddy (loopback HTTP + Tailscale HTTPS with internal CA), health checks, auth (admin user, `__Host-` secure sessions, RBAC skeleton), audit table, `make check`, **memory measurement script** | `erp-dev` stack up within the 1.6 GB caps; **idle and synthetic-load memory measured and recorded next to estimates**; login works on loopback and Tailscale HTTPS; tests green |
| **M1 Engine core + DCF** | Methodology doc first (conventions, stub modes, lease treatments, ERP/CRP, beta, dilution, output types); `core`, **`outputs` (typed output kinds)**, forecast drivers, FCFF and **FCFE (no bridge)**, discounting (end/mid, per-period WACC), **stub Modes A/B + roll-forward**, Gordon + exit TV, `bridge/`, **`dilution/` with instrument register and safeguards**, **lease treatments (IFRS 16 / ASC 842)**, WACC with **ERP/CRP separation** and beta methods; **independent reference (§13.1.1) built alongside** | Full engine test suite incl. policy guards and reference agreement green; ≥ 3 hand-worked benchmarks pass |
| **M2 Data ingestion** | SEC adapter (ticker map, profile, companyfacts), raw archive, **deterministic first/latest selection with `facts_as_of`**, standardization mapping v1 (us-gaap + ifrs-full incl. lease and share-count items), **interim coverage detection (conditional 6-K)**, integrity checks, **conflict materiality + lock blocking**, dataset versions, **financial-business classifier**, Yahoo price adapter (symbol validation only), **manual TSX listing entry**, Treasury/BoC rates & FX, Damodaran reference datasets, Excel/CSV import template | AAPL, MSFT, SHOP, CNI, CNQ load end to end with ≥ 8–15 years and passing balance checks; RY classified `bank` and DCF blocked; a TSX-only company entered manually, symbol validated, financials imported via template |
| **M3 Workflow UI** | Search / manual listing entry → company profile → financials (reported/standardized/adjusted, provenance badges, validation and conflicts panels, adjustments) → project → assumption panel (historical context, suggestion/approval, WACC build-up with ERP/CRP, beta diagnostics, lease and dilution settings) → DCF output → trace inspector | Playwright E2E: ticker to implied price, for a US and a TSX-only company |
| **M4 Scenarios, sensitivity, persistence** | Base/bull/bear/custom with inheritance & probabilities, 1-D/2-D sensitivity, saved immutable runs, audit history UI, Excel export (values + provenance + engine version + output kinds) | Saved run re-executes to identical hash; export opens cleanly |
| **M5 Release hardening** | Prod deployment, **off-server restic backup + restore drill from the off-server copy**, **memory re-measurement under acceptance workload with the game server running**, docs, `KNOWN_LIMITATIONS.md` updated | All brief §24.6 criteria met, plus the off-server backup and game-server co-residency gates |

Roadmap adjustments in Revision 2: FCFE moved from M4 to M1 (it shares the engine core and output-type work); manual TSX entry, classification, conflict handling and deterministic selection added to M2; memory measurement added to M0 and M5; off-server backup made an M5 gate. M1 grows the most — it is the highest-value milestone and is not shortened.

**Release 1 acceptance criteria:**
1. Entering `AAPL` (or an interlisted Canadian ticker) creates a company with profile and ≥ 8 years of standardized annual statements, every value traceable to a filing accession.
2. A TSX-only company can be entered manually (ticker + exchange), its price symbol validated, and valued via Excel import.
3. The analyst approves assumptions; no `suggested` value enters a saved valuation.
4. FCFF and FCFE DCF produce EV, equity value, implied price, upside vs a dated market price, TV share, implied multiples.
5. WACC × g and WACC × exit-multiple grids; growth × margin grid; tornado.
6. Bull/base/bear + custom scenarios with probability-weighted value.
7. Saved valuations are immutable, reproducible (identical output hash) and survive a restore **from the off-server backup**.
8. Every number on the DCF page opens a trace to its inputs and sources.
9. All engine benchmark, reference-agreement, property, edge-case and policy-guard tests pass.
10. Production stack stays within its memory caps with the game server running; measured peaks recorded alongside estimates.
11. No saved valuation exists on a dataset with unresolved, un-overridden material conflicts.

### 14.2 Release 2 — Advanced modelling
Integrated three-statement model (BS/CF forecast, cash & revolver with explicit circularity solver), full schedules (§8.2 of brief), leases, SBC, debt tranches, convertibles, NCI, acquisitions, restructuring, segment models aggregating to consolidated, quarterly and stub forecasting. The R1 DCF consumes these schedules through the same module interfaces — no duplicated calculations.

### 14.3 Release 3 — Research infrastructure
Research library (upload, versioning, deterministic text/PDF extraction with page references, search via Postgres full-text), source-to-model links with page numbers, research notes and history, watchlist/status, quarterly statement history, additional provider adapters (optional free-key providers if approved), TSX listings directory, global market identifiers.

### 14.4 Release 4 — Complete application
Trading comps, precedent transactions (manual entry), SOTP, asset-based/liquidation, DDM, residual income, LBO, NAV, bank/insurer models, reverse DCF (bounded root-finding with inspectable solver), Monte Carlo (seeded, correlated via Cholesky), valuation reconciliation, investment conclusions, PDF research reports and IC materials.

### 14.5 Final hardening
Security review, performance and memory profiling, DB optimization, backup/restore drills, operational monitoring, user documentation.

---

## 15. Decisions That Would Be Expensive to Change Later

| Decision | Why it is locked in now |
|---|---|
| Decimal/NUMERIC for all authoritative values | Changing later invalidates stored hashes and reproduction |
| Immutable dataset versions pinned by projects | Cannot be retrofitted for valuations saved without it |
| Fully materialized input document stored per saved run | Reproduction must not depend on mutable rows |
| Stable standard line-item codes | Referenced by mappings, datasets, imports, exports |
| Fiscal periods as explicit start/end dates (not calendar-year ints) | 52/53-week years, stubs, non-December FYEs |
| Currency + scale on every value | Global coverage and FX consistency |
| Company ≠ security | Interlisted (NYSE/TSX) and multi-class shares |
| Provenance on every value; `suggested` vs `approved` | Brief principles 2.1–2.3 |
| Multi-user + user attribution from day one | Audit history cannot be back-filled |
| Versioned trace format | Old traces must stay readable |
| Typed valuation output kinds with single conversion modules | Every R4 methodology plugs into them; retrofitting would touch every method |
| Deterministic fact selection with `facts_as_of` | Datasets built without it cannot be rebuilt exactly |
| Instrument register for dilution | Share counts saved without per-instrument treatment cannot be audited for double counting |

---

## 16. Decisions and Trade-offs

The finalized architectural decisions, their rationale and the trade-offs accepted are recorded in **`DECISIONS.md`**. That file is the place to update when a decision changes.

---

## 17. Known Limitations

Data coverage, provider dependencies, methodological limitations and unresolved risks are recorded in **`KNOWN_LIMITATIONS.md`**, which is maintained through every release (brief §24.5).
