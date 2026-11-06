# Session Log

## Session 1 — 2026-11-06 (UTC ~05:10–05:31)

**Type:** Architecture and technical planning only.
**Architecture status at end of session:** **PROPOSED — NOT APPROVED.** No implementation has been authorized.
**Implementation performed:** None. No dependencies installed, no containers or services started, stopped or modified, no infrastructure changes, no commits.

### 1. Work performed

1. Read `CLAUDE.md` and `MASTER_BRIEF.md` in full.
2. Read-only environment inspection (results in `TECHNICAL_BLUEPRINT.md` §0):
   - **Host:** 4-core x86-64, 7.6 GiB RAM, 64 GB free on a 128 GB SSD, Ubuntu-based Linux.
   - **Tooling:** Docker 29.1.3 / Compose 2.40.3, Python 3.12.3, Node 24.18.
   - **Existing services:** inventoried (details kept outside this repository). No changes made.
   - **Game servers:** on-demand game servers are installed but were stopped (one uses up to ~3.5 GB when running).
   - **Existing code:** earlier personal prototypes reviewed. Their code won't be reused; their lessons will.
3. Live probes of free data providers (read-only GET requests):
   - **Working:** SEC `data.sec.gov`.
   - **SEC ticker file:** 403 without a contact User-Agent.
   - **Yahoo chart:** works for a known symbol, including TSX.
   - **Stooq:** now blocked by a JavaScript challenge.
   - **Rates and reference data:** Treasury, Bank of Canada, FRED and Damodaran reachable.
   - **SEC XBRL coverage:** verified for AAPL, SHOP, CNI, RY and CNQ.
   - Probe files are in the session scratchpad only, not in the repository.
4. Wrote `TECHNICAL_BLUEPRINT.md` (Revision 1) and presented the architecture and trade-offs.
5. Received an architecture review. Revised the blueprint (Revision 2) and created `DECISIONS.md` and `KNOWN_LIMITATIONS.md`.

### 2. Revision 2 — the 14 corrections

**Mandatory corrections**

| # | Correction | Where |
|---|---|---|
| 1 | **FCFE:** equity value comes directly from FCFE discounted at Ke. Cash and non-operating assets are no longer added, and the EV→equity bridge is used only by FCFF. Interest income on cash is added to FCFE net income. | Blueprint §7.3, §7.7; D19 |
| 2 | **Memory:** hard caps reduced to 1,584 MB total (web 48 / api 384 / worker 576 / db 576 MB). Expected-typical, expected-peak and measured columns are kept separate (measured = pending). A game-server co-residency test is required at M0 and M5. | §1.2, §12.3; D3 |
| 3 | **TSX discovery:** ticker and exchange are entered manually, and the provider symbol is derived by a documented rule. The Yahoo chart endpoint is used only to *validate* the symbol. Automated discovery is optional and off until verified. | §0.4, §0.5, §4.3; D15 |
| 4 | **WACC/CRP:** mature-market ERP and CRP are separate inputs. A basis switch and validation rules prevent counting country risk twice. The beta regression and adjustment method is documented. | §7.5; D21 |
| 5 | **Stub periods:** Mode A (prorated) and Mode B (actual partial period) are distinguished. Mode B, the default, captures seasonality and working-capital timing. Mode A's limitations are documented. Valuation is done at the balance-sheet date and rolled forward to the valuation date. | §7.4; D20 |

**Additional refinements**

| # | Refinement | Where |
|---|---|---|
| 6 | 6-K interim XBRL is treated as conditional, with per-company interim-coverage detection | §0.5, §4.4; D12 |
| 7 | Deterministic first-reported vs latest-reported selection (total ordering, `facts_as_of` cut-off, ambiguous duplicates) | §4.4; D11 |
| 8 | Lease treatment covers IFRS 16 and ASC 842, applied consistently across FCFF, bridge, WACC and multiples | §7.7.1; D22 |
| 9 | Financial-business classification: bank / insurer / specialty lender / REIT / asset manager-broker / investment holding / non-financial, each with its own policy | §7.6.1; D24 |
| 10 | Dilution instrument register with double-count safeguards; weighted-average shares are never the base | §7.7.2; D23 |
| 11 | Material data conflicts block dataset locking until resolved or overridden with a recorded reason | §10.3; D13 |
| 12 | Secure cookies on every access path: HTTPS on Tailscale/LAN through Caddy's internal CA, plain HTTP on localhost only | §11.1.1; D5 |
| 13 | An off-server backup (restic) and a restore drill from it are required before production acceptance | §12.4; D7 |
| 14 | The reference DCF is mathematically independent of the engine (different formulas, `Fraction`/`mpmath` arithmetic, import-linter rule) | §13.1.1; D26 |

### 3. Additional requirement — valuation output types

The engine has three output kinds: `ENTERPRISE_VALUE`, `EQUITY_VALUE` and `PER_SHARE_VALUE`. Every methodology declares which one it produces.

- **Conversions:** only `bridge/` converts EV→Equity, and only `dilution/` converts Equity→Per share.
- **Reconciliation:** methods are compared only at the same output kind, with the basis recorded (minority or control).
- **Mapping:** a methodology table covers DDM, residual income, comps, precedents, SOTP, NAV, LBO and reverse DCF.
- **Where:** Blueprint §6.6; D18.

### 4. Roadmap changes (Blueprint §14.1)

- **M0:** memory measurement added, plus Tailscale HTTPS and `__Host-` secure sessions.
- **M1:** FCFE moved here from M4. Also added: typed output kinds, stub modes A/B with roll-forward, lease treatments, the dilution register, ERP/CRP separation, beta methods, and the independent reference valuation. The methodology document is written first.
- **M2:** added deterministic fact selection, interim-coverage detection, conflict materiality and lock blocking, the financial-business classifier, manual TSX listing entry with symbol validation, and Damodaran reference datasets.
- **M3:** the end-to-end test now covers both a US company and a TSX-only company.
- **M5:** gates added for an off-server backup with a restore drill from it, and memory re-measurement with the game server running.
- **Release 1 acceptance criteria:** updated (items 2, 7, 9, 10 and the new item 11).

### 5. Other changes in Revision 2

- The default discount timing for the Gordon terminal value changed from mid-period to end-of-period (D27).
- SIC codes I couldn't verify were removed. The SIC lists are still to be checked against the SEC list in M2.
- Prod ports are now `127.0.0.1:8090` (HTTP) plus `<tailscale-ip>:8443` (HTTPS). Both were free at inspection.

### 6. Repository state at end of session

| Item | Value |
|---|---|
| Branch | `main` |
| Latest commit | `2b37134b93bbf43a1609eec1edecf323a71b18de` — "chore: establish project specification and instructions" (2026-11-06 05:07:18 +0000) |
| Staged | nothing |
| Untracked (not committed, not staged) | `TECHNICAL_BLUEPRINT.md`, `DECISIONS.md`, `KNOWN_LIMITATIONS.md`, `SESSION_LOG.md`, `NEXT_SESSION.md` |
| Modified tracked files | none |

SHA-256 of the architecture documents at handoff:

```
749e8fe3faca40d50f92cce787d167099f72649532c9d0221917c6d30ec0b58a  TECHNICAL_BLUEPRINT.md
4af901a3f3333825b06002dcd332d595e538e777ceca9246b85e495c7a659b0c  DECISIONS.md
cb326c3c76f2c69b536c9aaa48ffc2977189c4a1afc8c5fbed366d3c2d072f9a  KNOWN_LIMITATIONS.md
```

## Session 2 — 2026-11-06 (UTC ~12:25–)

- Handoff verified: branch `main`, HEAD `2b37134`, the three document hashes matched. Only difference: `CLAUDE.md` modified by the owner (new "Token Efficiency and Session Continuity" section).
- **Owner decisions:**
  - (a) **Architecture Revision 2 APPROVED**, and M0 authorized.
  - (b) SEC User-Agent deferred to M2.
  - (c) Commit all five docs plus `CLAUDE.md` to `main`.
- Status lines in the three architecture documents changed to APPROVED. Docs checkpoint committed. M0 work is on branch `m0-foundation`.
- **M0 Foundation implemented** on `m0-foundation`:
  - Workspace: uv workspace with `engine` and `backend`, resolved with `exclude-newer`; uv pinned in `.tools/`.
  - Backend: FastAPI app with Argon2id auth, `__Host-` server-side sessions, CSRF token plus Origin check, rate limiting, RBAC (3 roles, 12 permissions), append-only audit log, structured errors, request IDs.
  - Database: Alembic baseline `0001`; 3-role DB model (D31).
  - Frontend: React login/home shell.
  - Infra: Compose `erp-dev` (`web`/`api`/`db`/`migrate`); Caddy with HTTP on loopback and internal-CA HTTPS; Makefile; memory and load scripts; docs.
- **Checks:** `make check` green (ruff, eslint, mypy strict, tsc, 29 pytest, 2 vitest). Smoke and HTTPS end-to-end flows pass.
- **Fixes found during M0:**
  - IP-address HTTPS needed Caddy `default_sni` (D30).
  - API retained 279 MiB after load; `MALLOC_ARENA_MAX=2` brought it to 199 MiB (D33).
  - The empty editable install and `uv run` re-sync behaviour now need `--all-packages`.
- **Decisions added:** D29–D34. D29 deviates from Blueprint §12.2: data and secrets live in the git-ignored repo dirs (CLAUDE.md "work within repository").
- **Memory:** idle 184 MiB, load peak 365 MiB (sum of per-container peaks). Details in `docs/operations/memory.md`.
- **Still open in M0:**
  - Game-server co-residency test (permission pending).
  - Tailscale-IP listener, verified at prod deploy (M5). Same mechanism tested on loopback.
- **Incident:** one foreground command was killed (exit 137) during the restart. Not OOM: the host had about 6 GB free and the API showed `OOMKilled=false`. It left `web` in "Created"; resolved by re-running `make up-dev`. Long runs now go to background jobs.
- **End of Session 2 (handoff, ~12:50 UTC).**
  - **Git:**
    - `m0-foundation` @ `8b3606d` (M0) and `main` @ `4dcb413` (docs). Not merged.
    - **No Git remote is configured**, so nothing has been pushed.
    - This log entry and the `NEXT_SESSION.md` rewrite are uncommitted (no commit authorized during handoff).
  - **Files in `8b3606d`:**
    - New: `Makefile`, `README.md`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `engine/**`, `backend/**`, `frontend/**` (incl. `package-lock.json`), `infra/**` (compose ×3, Dockerfiles, Caddyfile, postgres init, env examples, scripts), `docs/DEVELOPMENT.md`, `docs/operations/memory.md`.
    - Modified: `.gitignore` (adds `secrets/`, `.tools/`), `DECISIONS.md` (D29–D34), `KNOWN_LIMITATIONS.md` (§4a), `TECHNICAL_BLUEPRINT.md` (data-root row only, per D29), `SESSION_LOG.md`, `NEXT_SESSION.md`.
    - Deleted: none.
  - **Untracked/ignored runtime state:** `.venv/`, `.tools/`, `frontend/node_modules/`, `.env.dev`, `secrets/dev/`, `data/dev/`.
  - **Running:** `erp-dev` containers (`web`/`api`/`db`), loopback only, left running. Game server inactive.
  - **Consistency check:**
    - Status lines in the three architecture docs agree: APPROVED 2026-11-06.
    - D29 is reflected in Blueprint §12.2.
    - Minor gap, not edited: the Blueprint §12.2 dev port row lists 8091 (+5173) but not the dev HTTPS port `127.0.0.1:8444` or DB `127.0.0.1:5433`. Both are documented in `docs/DEVELOPMENT.md` and D30, and both are loopback-only. No architectural change.
  - **Owner question asked and still unanswered:** permission for the game-server co-residency test.
- **Owner decision (end of Session 2):** Game-server co-residency test **deferred to M5**. Handoff files committed. The merge of `m0-foundation` into `main` is not yet confirmed.
- **Merge:** `m0-foundation` → `main` with `--no-ff` (`4153e69`), owner-approved. `make check` green on `main`. Not pushed (no remote).
- **M1 started** on branch `m1-engine`. Drafted `docs/methodology/dcf.md` (uncommitted, **DRAFT awaiting owner approval**). It covers every R1 DCF convention with Blueprint/decision traceability, plus 4 hand-worked benchmarks:
  - B1: FCFF end-year, price 16.088182.
  - B2: mid-period, stub Mode B, roll-forward, price 16.381688.
  - B3: FCFE with leverage change, no bridge, price 14.683347.
  - B4: TSM fixed point, P 19.5454545455.
  - Benchmarks were computed with exact rationals and 50-digit decimals as a calculator; no engine code exists.
- **Open owner questions:**
  - Q1: Gordon TV timing under mid-period (D27). Default lowers EV 3.9% in B2; recommend following the cash-flow convention.
  - Q2: default terminal FCF form (recommend value-driver with RONIC as a required assumption).
- No implementation or installation performed after the draft; stopped for approval.

## Session 3 — 2026-11-06

- **Startup verification:** on branch `m1-engine`. `m0-foundation` fully merged into `main` (`4153e69`, identical trees). Uncommitted: methodology draft plus handoff updates, as recorded.
- **Owner decision:** methodology draft **approved with recommendations**.
  - Q1: Gordon TV timing follows the cash-flow convention. **D27 amended.**
  - Q2: value-driver terminal form is the default, with RONIC a required assumption suggested = WACC. **New D35.**
- **Changes:**
  - `docs/methodology/dcf.md` marked APPROVED; §6.1/§6.2 updated; §15 converted to resolutions.
  - B2 recomputed under amended D27: EV_B 1,997.414891, price 17.171458. The old values are kept as the "conservative override" row.
  - New benchmark B1-VD (value-driver default): price 15.564694.
  - B1/B2/B3 now state explicitly that they select the simple form. Recomputed with the same exact-arithmetic calculator method; no engine code.
  - `DECISIONS.md`: D27 amended (superseded rule kept), D35 added.
  - `TECHNICAL_BLUEPRINT.md`: two lines annotated (§7.4 TV timing, §7.6 default form) with pointers to D27/D35.
- **Commit** on `m1-engine` (see `git log`). Not merged; not pushed (no remote).
- **Next:** independent reference implementation. Needs owner approval to add dev dependencies `mpmath` and `import-linter`.
- **Session 3, continued: independent reference valuation (owner-approved).**
  - **Dev dependencies:** `mpmath` 1.4.1 and `import-linter` 2.15, added via uv under the existing `exclude-newer` lock (`pyproject.toml`, `uv.lock`). Dev-only; no system changes.
  - **`engine/tests/reference/`** (package `reference`):
    - `arithmetic` (Fraction / mpmath-50);
    - `timeline` (§5.1 times; D27-amended TV timing);
    - `discounting` (**backward recursion** over dated cash events, with per-period rate compounding);
    - `forecast` (§2–§4, stub Modes A/B);
    - `terminal` (simple and value-driver Gordon);
    - `economic_profit` (**EP identity**: telescoping form for any TV, plus the McKinsey value-driver continuing value);
    - `tsm` (brute-force subset search);
    - `valuation` (FCFF with bridge and roll-forward; FCFE without bridge).
  - **Fixtures:** `engine/tests/fixtures/benchmarks/{B1,B1-VD,B2,B3,B4}.json`. Expected values come from the methodology calculator (forward discount-factor sums), not from the reference. Each fixture also carries the document's rounded values.
  - **Tests (26 engine tests):**
    - Benchmarks: exact equality for B1, B1-VD, B3, B4; relative 1e-10 for B2.
    - Document/fixture rounding consistency.
    - EP identity for several IC₀ values, plus a Hypothesis test over random forecasts.
    - Closed-form property tests: perpetuity, growing perpetuity, mid-period = end × (1+r)^½, cumulative per-period rates.
    - TSM self-consistency.
    - FCFE no-bridge guard.
    - AST independence scan.
  - **Sensitivity checks:**
    - Mutations were caught: B2 price off by 1e-6 relative; B1 EV off by 1e-12; superseded D27 TV timing.
    - A deliberate `import erp_engine` in the reference broke the import-linter contract and failed the AST test. Reverted.
  - **Config:**
    - `pyproject.toml`: `[tool.importlinter]` with 2 contracts; ruff `allowed-confusables` for −, ×, – in formula docstrings.
    - `Makefile`: `lint` runs `lint-imports`.
    - `docs/DEVELOPMENT.md`: reference section added.
  - **Result:** `make check` green (lint + 2 contracts kept, mypy strict, 26 engine + 28 backend pytest, 2 vitest).
  - Committed on `m1-engine`. Not merged; not pushed (no remote). The dev stack was already running and was not restarted.
- **Session 3, continued: M1 engine implemented (owner-approved).** No new dependencies; the engine stays dependency-free (D36).
  - **`erp_engine/core/`:**
    - `numbers`: Decimal precision 34, `ROUND_HALF_EVEN`, traps, `@calc` context isolation, `dpow`.
    - `errors`, `validation` (Severity, `blocks_save`), `trace` (TraceNode DAG).
    - `registry`: graphlib order; cycle, missing-dependency, duplicate-provider and contract checks.
    - `timeline`: Actual/365.25; cumulative per-period discount factors.
  - **Valuation modules:**
    - `inputs` (frozen dataclasses) and `outputs` (OutputKind, ValuationOutput, `require_kind`).
    - `forecast`, with lease treatments for IFRS 16/ASC 842 and SBC checks.
    - `dcf/flows`: FCFF, FCFE, stub Modes A/B.
    - `dcf/terminal`: value driver (default), simple, exit multiple; D27-amended timing and override; implied counterparts; coherence checks.
    - `dcf/discounting`.
    - `bridge`: EV only; lease deductions; instrument double-count guard.
    - `rollforward`.
    - `dilution`: register, TSM fixed point, convertibles claim/if-converted/auto, RSUs, guards.
    - `classification` gate.
    - `wacc`: Ke with ERP/CRP guards; regression beta with SE/R²/CI; Blume; unlever/relever; synthetic spread; WACC weights incl. leases.
    - `api.run_valuation`: registry pipeline, input/output hashes, versions.
  - **Tests (109 engine tests, 98% line coverage of `erp_engine`):**
    - Benchmarks B1, B1-VD, B3, B4 within 1e-12 relative; B2 within 1e-10.
    - Hypothesis engine-vs-reference: FCFF (both conventions, both Gordon forms), stub Mode B mid-period, FCFE.
    - Core units.
    - 30+ policy-guard tests (each with a failing input).
    - Unit tests: leases, bridge, TSM, convertibles, roll-forward, per-period rates.
    - WACC tests.
    - Golden master: output change without a version bump fails, verified by deliberate perturbation, then reverted.
  - **Fixed during the work:**
    - Four test-side failures: expectations were computed at 28 digits against the engine's 34. They're now computed in the engine context; no tolerances loosened.
    - One scripted-edit syntax slip in `dilution.py`.
  - **Decisions D36–D39 added.** D39: FCFE with a stub is refused rather than approximated.
  - **`KNOWN_LIMITATIONS.md` §4b added.**
  - **`make check` green:** lint, 2 import contracts kept, mypy strict on 46 files, 109 engine, 28 backend, 2 frontend tests.
  - Committed on `m1-engine`. Not merged; not pushed (no remote).
- **Merge (owner-approved):** `m1-engine` → `main` with `--no-ff` (`e3c17bc`). `main` tree identical to the branch; `make check` green; clean. Not pushed (no remote).
- **M2 preparation** on branch `m2-ingestion`:
  - **Dependencies (owner-approved, D40):** `procrastinate` 3.10.0, `duckdb` 2.0.0, `zstandard` 0.25.0 added to the backend runtime; `httpx` moved from dev to runtime. Locked under `exclude-newer`. About +105 MB installed; an import-linter contract keeps DuckDB out of request-serving modules.
  - **Migrations:**
    - `0002`: procrastinate schema frozen in-repo (`backend/alembic/sql/procrastinate_3_10_0.sql`), applied in schema `jobs` with app-role grants (D41).
    - `0003`: `source.providers` (7 providers seeded, all disabled) and `source.provider_fetches`, append-only by trigger and grants.
  - **Code:**
    - `erp_backend/ingestion/archive.py`: content-addressed zstd archive; atomic, read-only files; hash-verified reads.
    - `ingestion/http.py`: `ProviderClient` with required User-Agent, rate limiter, bounded retries honouring `Retry-After`, URL redaction; `fetch_and_archive` archives before parsing and logs the fetch.
    - `ingestion/sec.py`: SEC gate (D42). No client can exist without a valid owner-supplied `ERP_SEC_USER_AGENT`; placeholders are rejected.
    - `jobs.py` (queue app, `sync_app`, `defer`), `tasks.py` (`system.ping`), `worker.py` (with `--check`), CLI `enqueue-ping`, `models/source.py`, config `raw_root` / `sec_user_agent`.
  - **Infra:**
    - Compose `worker` service: concurrency 1, 576 MiB cap, `cpu_shares` 512, host-user write to `data/<env>/raw`, read-only root filesystem, health check.
    - Make targets `enqueue-ping-dev` and `data-dev` (raw directory).
  - **Fixed during the work:**
    - In procrastinate 3.10, `configure_task` ignores `with_connector`, so a `defer()` helper binds the job manager explicitly.
    - Exceptions renamed to `…Error` (lint).
  - **Verification:**
    - `make check` green: lint, 4 import contracts, mypy strict (54 files), 109 engine + 57 backend + 2 frontend tests.
    - Real enqueue → worker → `succeeded` on the test DB and in the running dev stack.
    - Dev DB at Alembic `0003`.
    - Memory idle: worker 36 MiB, API unchanged (`docs/operations/memory.md`).
  - **No SEC request was made.** `ERP_SEC_USER_AGENT` is not configured anywhere. Every HTTP test uses `MockTransport`.
  - Docs: DECISIONS D40–D42; `docs/DEVELOPMENT.md` (jobs and archive section). Committed on `m2-ingestion`, not merged.
- **SEC adapter (owner-approved; owner supplied the SEC User-Agent).**
  - **Configuration:** the User-Agent is stored only in the git-ignored `.env.dev` and passed only to the `worker` container (D45). Verified that `.env*` are ignored and untracked, and that the contact string appears in no committable file and no worker log. The env examples carry an empty `ERP_SEC_USER_AGENT=` key.
  - **Migration `0004`:**
    - `market.companies`, `market.securities`, `market.company_identifiers`.
    - `source.sec_tickers`, `source.filings`, `source.reported_facts` (bigint identity; `NULLS NOT DISTINCT` key including value). Append-only by trigger and grants (D43).
  - **Code:**
    - `ingestion/sec_parse.py`: ticker map, submissions and companyfacts parsers. Decimal values, strict errors.
    - `ingestion/selection.py`: D11 total order (filed date → original before amendment → accession); `facts_as_of` cut-off; ambiguity and restatement detection.
    - `ingestion/coverage.py`: D12/D44 interim coverage.
    - `ingestion/sec_ingest.py`:
      - ticker-map refresh (cached 1 day) and CIK resolution;
      - submissions → profile, listings, filings;
      - companyfacts → facts, standard/currency/coverage inference (audited);
      - archive rebuild (D46).
    - Process-wide 5 req/s limiter. Tasks `sec.refresh_ticker_map` and `sec.ingest_company` on queue `ingestion` with lock `sec`.
    - CLI `sec-check-config`, `sec-ingest`, `sec-rebuild-from-archive`.
    - `fetch_and_archive` now stamps the actual retrieval time.
  - **Live SEC requests made: 13** (1 ticker map + submissions and companyfacts for AAPL, MSFT, SHOP, CNI, CNQ, RY).
    - All HTTP 200, 0 errors; minimum spacing 0.245 s (≤ 5 req/s).
    - 15 MB raw → 914 kB archived; 13/13 payloads re-hash correctly.
    - Result: 6 companies, 7 securities, 8,799 filings, 88,160 facts; DB 53 MB.
  - **Defects found by live data and fixed:**
    1. 10-Q fiscal year-end comparatives were counted as interim quarters (AAPL/MSFT showed `partial`).
    2. Stale 2008–09 quarters masked CNI's current gap; added a 3-year recency bound.
    3. RY/CNQ/SHOP have no business address; country now falls back to the incorporation code (incl. `Z4` = Canada).
    4. CNI files financials on 6-K; currency inference now falls back to interim periodic forms and ignores registration forms (F-10).
    - Each has a regression test. They were applied to existing data with `sec-rebuild-from-archive` (no network), so the SEC request count stayed at 13.
  - **Live results:**

    | Company | Coverage | Country | Currency | Standard |
    |---|---|---|---|---|
    | AAPL | full | US | USD | US GAAP |
    | MSFT | full | US | USD | US GAAP |
    | SHOP | partial | CA | USD | US GAAP |
    | CNI | none | CA | CAD | US GAAP |
    | CNQ | none | CA | CAD | IFRS |
    | RY | none | CA | CAD | IFRS (bank template limitation) |

    - Selection on live data: order-independent for all 6; 0 ambiguous keys.
    - Restatements were sampled and confirmed genuine (e.g. AAPL FY2008–09 assets after revenue-recognition adoption; EPS after splits).
  - **Tests:** 111 backend tests (54 SEC-specific, mocked HTTP only). `make check` green: lint, 4 import contracts, mypy strict (59 files), 109 engine + 111 backend + 2 frontend.
  - Memory after ingestion: worker peak 194.6 MiB in the archive rebuild (cap 576). Committed on `m2-ingestion`. Not merged, not pushed (no remote).

### Session 3 closing summary — 2026-11-06 (UTC ~14:10)

- **Completed today (Sessions 2–3):**
  - **M0 Foundation:** merged to `main`.
  - **M1 valuation engine:** approved DCF methodology, independent reference, engine. Merged to `main` (`e3c17bc`).
  - **M2 part 1 on `m2-ingestion`:**
    - ingestion foundation (`c651008`);
    - **SEC adapter complete** (`92e31ed`).
- **Decisions recorded:** D29–D46. The pending-decision table is updated with P7 (Git remote), P8 (author identity) and P9 (per-provider first live request).
- **SEC requests:** 13 live requests total today, all successful, ≤ 5 req/s. None since the adapter verification; the later fixes were applied from the archive.
- **Git remote:** none configured, so nothing has been pushed anywhere.
  - `gh` is authenticated as `adriandelicdesouza`.
  - No repository named `equity-research-platform` exists on that account.
  - Other, unrelated repositories on the account were **not** used.
  - Every commit's author metadata contains the owner's Gmail (global git config). Publishing requires the owner's explicit approval with that disclosure (P8).
- **Shutdown:**
  - The `erp-dev` stack was stopped with `make down-dev`; data persists in `data/dev/`.
  - No background jobs are left running. Other server containers were untouched.
- **Uncommitted at close:** `SESSION_LOG.md`, `NEXT_SESSION.md`, `DECISIONS.md` and `KNOWN_LIMITATIONS.md`, holding this closing documentation only. Commit is pending owner instruction.

### Session 3, continued — history rewrites and public-release remediation (2026-11-06)

All owner-approved, nothing pushed. Backups are outside the repository: `20261106T141410Z` (before rewrite 1) and `20261106T142726Z` (before rewrite 2). Both were verified by restoring.

- **Rewrite 1 (email):** author and committer email changed from the owner's former Gmail address to `contact@delicsouza.com` on all 4 branches. Done with `git-filter-repo` 2.47.0, installed in `.tools/` only.
- **Rewrite 2 (public-release remediation):**
  - Author and committer name changed from the former local username to `Adrian Delic de Souza`.
  - Home-server infrastructure details redacted from every historical version of 5 docs: `TECHNICAL_BLUEPRINT.md` §0.1–0.3, `KNOWN_LIMITATIONS.md` §6, `MASTER_BRIEF.md` §5, `SESSION_LOG.md` (inspection summary), and one `NEXT_SESSION.md` line. The removed details were hostname, hardware models, the exposed-service and tunnel inventory, the web-shell note, the Tailscale IP, listening ports, network names, and paths to other personal projects.
  - Each was replaced with a generic description that keeps the design constraints (memory budget, loopback ports, isolation, remote-access approach). The exact rules are in `sanitize.py` in the second backup.
- **Verified after rewrite 2:**
  - 11 commits: emails, dates, messages and parent structure unchanged.
  - Every other file is byte-identical in every commit. Each changed blob equals the sanitizer's output for the original.
  - 0 infrastructure patterns, 0 former-username and 0 old-Gmail occurrences in any of the 297 objects.
  - `git fsck --full --strict` clean; branch relationships unchanged; `origin` restored.
- **Hash references in the working copies of the 4 handoff docs** were updated to the final hashes (15 references). Earlier entries in this log keep their wording; their hashes now point at the rewritten commits. Mapping:

| Original | After rewrite 1 | Final | Commit |
|---|---|---|---|
| `a7b6556` | `6cc0b89` | `2b37134` | chore: establish project specification and instructions |
| `4855614` | `34eca14` | `4dcb413` | docs: approved technical blueprint, decisions, limitations and session handoff |
| `bdc1ac8` | `b13c524` | `8b3606d` | feat(m0): foundation — workspace, auth, audit, dev stack, memory measurement |
| `48354af` | `1a10c01` | `c5281b4` | docs: session 2 handoff; defer Minecraft co-residency test to M5 |
| `0c42321` | `617c732` | `4153e69` | Merge branch 'm0-foundation': M0 Foundation milestone |
| `bf25ffc` | `7fb22b4` | `ef1bbbb` | docs(m1): approved DCF methodology specification; amend D27, add D35 |
| `e92b0f6` | `6afcd6a` | `1d5cd18` | test(m1): independent reference DCF valuation with benchmark fixtures |
| `f6e1305` | `0aad517` | `2dee8b8` | feat(m1): core valuation engine — FCFF/FCFE DCF, bridge, dilution, WACC |
| `057b963` | `1f3e89e` | `e3c17bc` | Merge branch 'm1-engine': M1 valuation engine milestone |
| `ee58bd2` | `7cb6b53` | `c651008` | feat(m2): ingestion foundation — job queue, worker, raw archive, provider client |
| `da3d4fb` | `e6b0e5a` | `92e31ed` | feat(m2): SEC EDGAR adapter — companies, filings, facts, selection, coverage |

- **Statements that are now historical:** earlier entries that say "author metadata contains the owner's Gmail" or give the author's former local username describe the state before these rewrites.

### Session 3, continued — initial GitHub publication (2026-11-06)

- **Published (owner-approved)** to `https://github.com/adriandelicdesouza/equity-research-platform` (public). `main` is the default branch.
- **Branch tips, verified identical locally and on `origin` with `git ls-remote`:**
  - `main`: `9ef5594a8e732e260a382b36be5275d6dea62464`
  - `m2-ingestion`: `51cd1fa31244d4f9074080e4c431fede3af4746f`
  - `m0-foundation`: `c5281b4ec45d5900cd1880b38ff499aad2eb5d89`
  - `m1-engine`: `2dee8b8f0ce4f10c2cb98fafe6ea171113e583bf`
- **Before the push:** an MIT `LICENSE` (GitHub detects it as MIT) and README sections for the AI disclosure, license and testing were added to both `main` (`9ef5594`) and `m2-ingestion` (`51cd1fa`). The `main` README describes only M0 and M1.
- **`make check`** passed on `m2-ingestion` (engine 109, backend 111, frontend 2 tests).
- **No merge, force-push or history rewrite.** `main` and `m2-ingestion` each have a separate README commit, so the future merge will conflict on `README.md`: keep the `m2-ingestion` version.

### Session 3, continued — M2 standardization mapping v1 (2026-11-06)

- **Task:** the first remaining M2 item (NEXT_SESSION #1). Datasets, integrity checks, conflicts and classification all work on standard line items.
- **Built:**
  - `mappings/v1/`: 81 line items, plus 226 us-gaap and ifrs-full candidates.
  - `erp_backend.standardization`:
    - `mapping.py`: loader, validation and fingerprint;
    - `resolve.py`: per-period resolution and annual coverage.
  - Migration `0005`: the `ref` schema, immutable, with the v1 hash pinned.
  - CLI `sec-standardize-report`.
  - Settings `ERP_MAPPINGS_ROOT` and `ERP_MAPPING_VERSION`; the image copies `mappings/`.
  - Decision D49; limitations in KNOWN_LIMITATIONS §4d.
- **Checked against stored real facts:** the key values for AAPL, MSFT, SHOP, CNI, CNQ and RY match the published figures. Annual coverage per year: AAPL 60–61/78, MSFT 60–61/78, CNI 60/78, SHOP 53–54/78, CNQ 52/72, RY 38/72 (bank). The candidate lists gained the MSFT debt-flow concepts and the IFRS no-NCI fallbacks.
- **Tests:** 41 new (backend 111 → 152). `make check` passes. Dev DB migrated `0004 → 0005` by the migrate container. No network requests.

### Session 3, continued — M2 versioned financial datasets (2026-11-06)

- **Task:** NEXT_SESSION item 2.
- **Built:**
  - pure fiscal periods (`standardization/periods.py`);
  - pure dataset build with Q4 = FY − 9M, canonical JSON and hashes (`standardization/dataset.py`);
  - the service (`erp_backend/datasets.py`): build, verify, lock and discard, all audited;
  - migration `0006`: the `fin` schema, with lifecycle triggers that bind every role;
  - five `dataset-*` CLI commands; decision D50; KNOWN_LIMITATIONS §4e.
  - `resolve.StandardValue` now also carries the accessions behind an ambiguous value.
- **Real data** (dev DB, migrated `0005 → 0006`): draft v1 built for AAPL (5,090 values, 422 derived Q4), MSFT (5,965), SHOP (1,189), CNI (1,170), CNQ (543, FY only: no interim XBRL) and RY (1,616).
  - All six verify byte-identically. Rebuilding AAPL created nothing.
  - The AAPL and CNI content hashes equal those of an earlier host-side build of the same facts.
  - AAPL FY2026: Q1 + Q2 + Q3 + derived Q4 = FY revenue (478,834 M).
  - Nothing was locked in dev.
- **Tests:** 43 new (backend 152 → 195). `make check` passes. Dev stack stopped. No network requests.
