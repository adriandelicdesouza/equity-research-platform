# Equity Research Platform

A self-hosted application for fundamental equity research and valuation of public companies. It focuses on reproducible, auditable financial calculations: every valuation input is traceable to its source, every output to its calculation steps.

> **Not investment advice.** This software is an analytical research tool. Its outputs are model results that depend entirely on data quality and analyst assumptions. They are not recommendations to buy, sell or hold any security. Use at your own risk.

## Intended users

Individual analysts and small research teams who want institutional-style valuation workflows (integrated models, explicit assumptions, scenario and sensitivity analysis, audit history) on infrastructure they control.

## What it does today

The project is under active development. **Implemented and tested:**

- **Valuation engine (`engine/`)**: pure Python with exact decimal arithmetic.
  - FCFF and FCFE discounted cash flow, with mid-period and stub-period conventions.
  - Terminal value: value-driver, Gordon growth and exit multiple, with coherence checks.
  - Equity bridge, IFRS 16 / ASC 842 lease treatments, and treasury-stock-method dilution with double-count safeguards.
  - Cost of capital: ERP and country-risk separation, regression and bottom-up beta.
  - Calculation traces, validation reports and deterministic output hashes.
  - Validated against an **independent reference implementation** and hand-worked benchmarks (`docs/methodology/dcf.md`).
- **Backend (`backend/`)**: FastAPI service.
  - Server-side sessions, role-based access (admin, analyst, reviewer), CSRF protection and an append-only audit log.
  - PostgreSQL with least-privilege roles.
  - A job queue and worker.
- **Data ingestion from SEC EDGAR**:
  - Company profiles, filings and XBRL facts, with archive-first storage of raw payloads.
  - Deterministic first-reported / latest-reported fact selection.
  - Restatement detection and interim-data coverage detection.
- **Financial-business classification**: bank / insurer / specialty lender / REIT / asset manager-broker / investment holding suggested from SIC and XBRL balance-sheet evidence, confirmed by the analyst (the valuation engine blocks DCF for banks, insurers, lenders and holdings).
- **Versioned financial datasets**: company-specific fiscal periods (52/53-week years, quarters, H1/9M), standardized values with full provenance, derived quarters (incl. quarterly cash flows), gross profit, EBITDA, free cash flow and LTM values, accounting integrity checks on every build (balance, subtotals, cash roll-forward, cross-statement, shares/EPS, completeness), data-conflict detection with materiality thresholds and an audited resolve/override workflow that must be completed before locking, draft → locked lifecycle enforced in the database, and byte-identical rebuild verification.
- **Standardization mapping (v1)**: versioned YAML (`mappings/`) mapping US GAAP and IFRS concepts onto 81 standard line items, with per-period concept fallback, sign normalization and a read-only coverage report (`sec-standardize-report`).
- **Read and workflow APIs (`/api`)**: read-only data API (D59) and audited, CSRF-protected workflow actions (D60).
- **Frontend (`frontend/`)**: React/TypeScript research workspace (D61): company search and workspace, financial statement grid with per-value provenance, conflict review and resolution, classification confirmation, listings (prices, corporate actions, trading currency, registration), reference data, interest-rate and FX history (read-only), import preview, dataset workflow controls and action history. Guide: `docs/operations/workflow-ui.md`.

**Not yet available:**
- The valuation engine isn't yet exposed through the API or user interface.
- Market and rates data providers (disabled, pending P9), valuation, scenario and sensitivity UI.
- Dataset locking and file ingestion await the owner's permission decision (P15).

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| M0 | Foundation: workspace, auth, audit, deployment | Done |
| M1 | Valuation engine and methodology | Done |
| M2 | Data ingestion and standardization: SEC (done), statement mapping (v1 done), versioned datasets (done), integrity checks, classification, market data (offline boundary done), CSV market-data import (done), manual listing registration (done), statement import (done), rates/FX adapters (offline, done), Damodaran reference datasets (done) | In progress |
| M3 | Analyst workflow: read-only data API (done), workflow actions API (done; lock/import pending P15), workflow UI (phases 3–4 done: company, financials, conflicts, datasets, imports, classification, listings, reference data, rates & FX; next: valuation project → DCF → trace inspection) | In progress |
| M4 | Scenarios, sensitivity analysis, saved immutable valuations, exports | Planned |
| M5 | Production hardening: backups, deployment, monitoring | Planned |
| Later releases | Three-statement modelling, research library, comparables, other valuation methods | Planned |

## Architecture

```
Browser ──► Caddy (static frontend, TLS, security headers)
               └─► FastAPI API ──► erp_engine (pure calculation library)
                        │
                        ├─► PostgreSQL (authoritative store + job queue)
                        └─◄ Worker (ingestion jobs) ──► raw payload archive (zstd, content-addressed)
```

- **Python 3.12, FastAPI, SQLAlchemy/Alembic, PostgreSQL 17, procrastinate** (job queue), **React 19 + TypeScript + Vite**, **Docker Compose**.
- **Design notes:** `TECHNICAL_BLUEPRINT.md` (architecture), `DECISIONS.md` (decision log), `KNOWN_LIMITATIONS.md` (data, methodology and operational limitations), `docs/methodology/dcf.md` (valuation conventions), `docs/architecture/valuation-api.md` (proposed valuation API, not approved).

## Getting started (development)

Requirements: Linux with Docker Engine + Compose v2, Python 3.12, Node 24, GNU make.

```bash
make setup             # project-local uv, Python deps (uv.lock), frontend deps (package-lock.json)
make up-dev            # creates .env.dev, generates secrets, builds and starts the dev stack
make create-admin-dev  # first user 'admin'; password in secrets/dev/initial_admin_password
make check             # lint, type checks, all tests (needs the dev stack running)
```

Then open `http://localhost:8091`. All dev ports bind to loopback only.

- Full instructions: `docs/DEVELOPMENT.md`.
- Every setting and environment variable: `docs/CONFIGURATION.md`.
- SEC ingestion requires your own contact User-Agent (`ERP_SEC_USER_AGENT` in `.env.dev`), per SEC fair-access rules.

## Data sources

Only free, public sources are used. SEC EDGAR is implemented. Other planned providers and their limitations are listed in `KNOWN_LIMITATIONS.md`. No financial data is bundled in this repository; fetched data stays in git-ignored local storage.

## Testing

`make check` runs everything: Ruff lint and format checks, import-boundary checks, mypy, ESLint, the TypeScript compiler, and the engine, backend and frontend test suites. Individual suites: `make test-engine`, `make test-backend` (needs the dev stack running, for its database), `make test-frontend`. The engine suite includes golden-output tests. After an intended calculation change, bump `engine/src/erp_engine/version.py` and regenerate them with `ERP_UPDATE_GOLDEN=1 make test-engine`.

## Project status and contributions

This is a personal project in active development. Interfaces and schemas may change. Design history is kept in the repository (`DECISIONS.md`, `SESSION_LOG.md`).

## AI disclosure

This project was developed with substantial help from AI tools, including Claude Code. AI assistance contributed code, documentation, tests and architectural work. The maintainer directs the work and is responsible for reviewing, validating and maintaining it. AI assistance does not guarantee correctness, security or completeness, so verify results independently. No AI provider endorses or sponsors this project.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Adrian Delic de Souza. Third-party dependencies remain under their own licenses.
