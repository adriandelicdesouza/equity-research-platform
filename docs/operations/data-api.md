# Read-only data API (M3 phase 1)

Decision D59; Blueprint §11 (authentication and authorization), §3.4 (immutable datasets), §10 (provenance). The API exposes the M2 data (companies, datasets, conflicts, imports, market data, rates/FX and reference datasets) for reading only.

The framework is the existing FastAPI app (`erp_backend.main:create_app`). OpenAPI is served at `/api/openapi.json`, and in non-production environments the docs are at `/api/docs`.

## Architecture

```
HTTP GET ─► api/data.py           thin handlers: parse + validate parameters, call a service
              │  Depends(require("company:read"))    cookie session + RBAC (existing, §11)
              │  Depends(read_only_db)               READ ONLY transaction, always rolled back
              ▼
           read/*.py              query services: SELECTs only, batch loading, response schemas
              │                   reuse domain code (datasets.stored_canonical/stored_checks,
              │                   classifications.effective_classification)
              ▼
           PostgreSQL             append-only triggers and role grants unchanged
```

**Read-only enforcement** works at three layers, independent of which HTTP methods exist:
1. **Routing:** every data route is `GET`. Other methods return `405`, and a test walks the router to confirm this.
2. **Session:** `erp_backend/readonly.py`.
   - The ORM refuses to flush pending objects and refuses INSERT/UPDATE/DELETE statements (`ReadOnlyViolationError` → `405 read_only`).
   - PostgreSQL rejects any write, because each request runs in a `SET TRANSACTION READ ONLY` transaction.
   - Every statement is capped by a 10 s `statement_timeout`.
3. **Database:** the existing append-only triggers and app-role grants still apply underneath.

The authentication dependency keeps using the normal session, because it refreshes `last_seen_at`. Data reads never use that session.

## Authentication and authorization

- Data routes use the **existing** cookie-session login (`POST /api/auth/login`).
- Every data route requires the **`company:read`** permission. All three roles (admin, analyst, reviewer) hold it; a user without a role gets `403 forbidden`, and an unauthenticated request gets `401 not_authenticated`.
- Nothing is public, and there are no API tokens: `auth.api_tokens` is still a future item (§3.2).
- Remote access will still go through Cloudflare Access (P3, not configured).
- The dev API is bound to localhost through the dev Caddy only. No deployment change was made.

## Conventions

- **Pagination:** lists return `{"items": [...], "total": n, "limit": l, "offset": o}`.
  - `limit` is 1–500 (default 50); `offset` is 0 or more.
  - Totals are exact.
  - The number of queries does not grow with the page size; a test checks this.
- **Exact numbers:** decimals are serialized as **JSON strings** (`"4.15"`, `"394328000000"`), never as floats. Every value states its unit, and money states its currency.
- **Errors:** every error has the form `{"error": {"code", "message"[, "details"]}}`.

  | Status | Code | When |
  |---|---|---|
  | `401` | `not_authenticated` | no valid session |
  | `403` | `forbidden` | missing permission |
  | `404` | `not_found` | unknown id |
  | `405` | method not allowed / `read_only` | non-GET method, or a write attempted in a read session |
  | `409` | `ambiguous_series` | a series choice is required |
  | `422` | `validation_error` / `too_many_periods` | invalid parameter or range |
  | `500` | `internal_error` | unexpected failure: no SQL, paths or traceback; only the error type and route are logged |
- **Identifiers:**
  - companies, datasets, listings, imports and reference datasets use UUIDs;
  - rate series use their code (`UST_PAR_10Y`);
  - a listing is found by ticker **and** MIC (`/api/listings?ticker=RY&mic=XTSE`).
- **No silent selection:**
  - Values are always read from **one** dataset version, named in the URL. Each dataset response states its basis (`standardized` = SEC, `reported` = imports), version and status.
  - A price series is one provider and one close basis. If a listing has several and you don't choose, the request returns `409 ambiguous_series` with the options.
  - `revisions=latest` (the default) returns the latest stored observation per date, and every row says how many `revisions` exist; `revisions=all` returns all of them.
  - FX is returned only in the stored direction.
- **No filesystem paths:** archive locations (`stored_path`, `payload_path`) are never returned, and there is no endpoint that serves file bytes.

## Endpoints

All paths below are under `/api` and are GET only.

| Group | Endpoint | Filters / notes |
|---|---|---|
| Companies | `/companies` | `q` (name substring or exact ticker), `country`, `sort` = `name`/`-name`/`created`/`-created` |
| | `/companies/{id}` | identifiers, listings, classification status (latest suggestion + confirmation) |
| | `/companies/{id}/classifications` | full append-only history |
| Listings | `/listings` | `company_id`, `ticker`, `mic` |
| | `/listings/{id}` | `origin` (`sec` / `manual_registration`), provider symbols, declared currency |
| Datasets | `/companies/{id}/datasets` | `basis`, `status`; newest version first |
| | `/datasets/{id}` | header, `source_summary`, check and conflict counts |
| | `/datasets/{id}/values` | `statement` (IS/BS/CF), `line_item` (repeatable), `period_type`, `fiscal_year_from`/`_to`, `provenance`. Each value has `fact_sources` (concept, accession, form, filing date) or `import_source` (file, SHA-256, cell, document, page), plus `derivation` |
| | `/datasets/{id}/statements/{IS\|BS\|CF}` | grid of line items (mapping order) × periods for one `period_type` (default FY); at most 60 periods |
| | `/datasets/{id}/checks` | `severity`, `status`, `check_code` (stored D51 results) |
| | `/datasets/{id}/integrity` | stored values and checks re-hashed against their fingerprints. The full rebuild comparison stays in the CLI `dataset-verify` |
| Conflicts | `/datasets/{id}/conflicts` | `material`, `status`, `kind`, `line_item`; candidates (source, id, value, role, accession) and applied resolution |
| | `/companies/{id}/conflict-resolutions` | `conflict_key`; resolution history, newest first |
| Imports | `/imports` | `category` (statement/market/reference), `template`, `status`, `file_sha256`, `imported_from`/`_to` |
| | `/imports/{id}` | summary, skipped records (first 200), stored objects (value counts, observation counts, reference dataset versions) |
| Market | `/listings/{id}/prices` | `provider`, `close_basis`, `start`/`end`, `revisions`, `sort=date\|-date`; each row has `origin` (provider fetch, or import + row) |
| | `/listings/{id}/corporate-actions` | `kind`, `provider`, `start`/`end` |
| Rates | `/rates/series` | `currency`, `provider`, `rate_type`, `tenor`; observation counts and date range |
| | `/rates/series/{code}` | definition: provider series, rate type, tenor, unit, quote convention |
| | `/rates/series/{code}/observations` | `start`/`end`, `revisions`, `sort`; values in percentage points |
| FX | `/fx/rates` | `base` and `quote` required; `provider`, `start`/`end`, `revisions`, `sort`. Each row has `direction: "CAD per 1 USD"` |
| Reference | `/reference/datasets` | `kind`, `edition_date`, `region`, `variant`, `current_only`; each row has `is_current`, `supersedes_id`, `superseded_by_id` |
| | `/reference/datasets/{id}` | plus source and import provenance (file name, SHA-256) |
| | `/reference/datasets/{id}/records` | `record_key`, `field` (repeatable); one record per item in file order, reproducible per version |

## Examples

Requests from a logged-in session cookie (`erp_session`):

```http
GET /api/datasets/7c1e…/statements/IS?period_type=FY&fiscal_year_from=2022
```
```json
{"dataset_id": "7c1e…", "basis": "standardized", "version_no": 3, "status": "draft",
 "currency": "USD", "statement": "IS",
 "periods": [{"fiscal_year": 2022, "period_type": "FY", "start_date": "2021-09-26",
              "end_date": "2022-09-24", "weeks": 52}, …],
 "rows": [{"line_item_code": "IS.REVENUE", "label": "Revenue", "is_subtotal": false,
           "value_type": "flow", "unit": "USD",
           "cells": [{"value": "394328000000", "provenance_kind": "reported", "status": "ok"}, …]}, …]}
```

```http
GET /api/fx/rates?base=USD&quote=CAD&sort=-date&limit=1
```
```json
{"items": [{"observation_id": 3, "base": "USD", "quote": "CAD", "direction": "CAD per 1 USD",
            "obs_date": "2024-03-05", "rate": "1.3571", "provider_code": "bank_of_canada",
            "provider_series_id": "FXUSDCAD", "fetch_id": "…", "revisions": 1}],
 "total": 3, "limit": 1, "offset": 0}
```

```http
GET /api/listings/…/prices
```
```json
{"error": {"code": "ambiguous_series",
           "message": "Several values of 'close_basis' are stored (raw, split_adjusted); choose one with 'close_basis' — series are never mixed."}}
```

## Running locally

```bash
make up-dev                                  # the API container serves the app behind dev Caddy
# from a browser at the dev origin: log in via POST /api/auth/login, then GET /api/...
# or open https://127.0.0.1:8444/api/docs (dev only)
```

To create a login, use `make create-admin-dev`, or `create-user` with a role. The tests and this guide never create users in the dev DB.

## Tests

`backend/tests/test_data_api.py` (`make test-backend`, which needs `make up-dev`):
- runs against its own disposable database `erp_test_api`, seeded with synthetic data through the real ingestion and import services;
- reads through genuine READ ONLY transactions;
- covers every resource group, filters, pagination, validation errors, 401/403/404/405/409/500, provenance, version selection, FX direction, rate-series identity and reference versioning;
- checks that table row counts are unchanged after the API calls;
- checks that the number of queries does not grow with the page size.

## Limitations

See `KNOWN_LIMITATIONS.md` §4n.
