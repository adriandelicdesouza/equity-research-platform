# Workflow UI (M3 phases 3–4)

Decision D61. The UI is a client of the read-only data API (`data-api.md`, D59) and the workflow actions API (`workflow-api.md`, D60). It implements no business rule of its own: every value, status and permission comes from the backend, which enforces all of them again on every request.

## Running it

- **Dev stack:** `make up-dev` builds the frontend into the web image. Open `https://127.0.0.1:8444/` (or `http://127.0.0.1:8091/` on loopback) and sign in.
- **Live reload:** `cd frontend && npm run dev` serves `http://localhost:5173` and proxies `/api` to the dev gateway. That origin is in the dev allow-list, so CSRF/Origin checks pass.
- **Contract types:** after a backend schema change, run `make api-types`. `make check` fails while `frontend/src/api/schema.gen.ts` is stale.

## Navigation

All views are hash URLs, so every view, including the chosen dataset, statement and conflict, can be bookmarked.

| View | URL | API |
|---|---|---|
| Company search | `#/companies?q=` | `GET /companies` |
| Workspace overview | `#/companies/{id}` | `GET /companies/{id}`, `/companies/{id}/datasets`, `/datasets/{id}`, `/datasets/{id}/integrity`, `/imports/{id}` |
| Financial statements | `#/companies/{id}/financials?dataset=&statement=IS&period=FY&from=&to=` | `GET /datasets/{id}/statements/{IS\|BS\|CF}`, `/datasets/{id}/values`, `/datasets/{id}/conflicts` |
| Conflicts | `#/companies/{id}/conflicts?dataset=&status=&material=&conflict=` | `GET /datasets/{id}/conflicts`, `/companies/{id}/conflict-resolutions`; `POST …/conflicts/{key}/resolutions` |
| Datasets & workflow | `#/companies/{id}/datasets?dataset=` | `POST /companies/{id}/datasets/build\|rebuild`, `/datasets/{id}/verify\|discard\|lock` |
| Classification | `#/companies/{id}/classification` | `GET /companies/{id}/classifications`; `POST /companies/{id}/classification/confirm` |
| Listings | `#/listings?ticker=&mic=` | `GET /listings` |
| Listing workspace | `#/listings/{id}?view=prices\|actions&provider=&close_basis=&start=&end=&revisions=&sort=&kind=` | `GET /listings/{id}`, `/listings/{id}/prices`, `/listings/{id}/corporate-actions`; `POST /listings/{id}/trading-currency` |
| Register a listing | `#/listings/register?company=` | `POST /listings/validate`, `POST /listings` |
| Reference data | `#/reference?kind=&versions=all`, `#/reference/{id}?record=` | `GET /reference/datasets`, `/reference/datasets/{id}`, `/reference/datasets/{id}/records` |
| Rate series | `#/rates?provider=&currency=&rate_type=` | `GET /rates/series` |
| Rate series history | `#/rates/series/{code}?start=&end=&revisions=all&sort=date&offset=` | `GET /rates/series/{code}`, `/rates/series/{code}/observations` |
| FX rates | `#/rates/fx?base=&quote=&provider=&start=&end=&revisions=&sort=&offset=` | `GET /fx/rates` |
| Imports | `#/imports`, `#/imports/{id}`, `#/imports/preview` | `GET /imports`, `/imports/{id}`; `POST /imports/{kind}/validate`, `POST /imports/{kind}` |
| Action history | `#/actions`, `#/actions/{id}` | `GET /actions`, `/actions/{id}` |
| Valuation projects | `#/valuations?status=&owner=me`, `#/companies/{id}/valuation` | `GET/POST /valuation/projects` |
| Valuation project | `#/valuations/{id}[/assumptions\|run\|runs]`, `#/valuations/{id}/runs/{runId}` | `/valuation/…` (see `valuation-api.md`) |
| System | `#/system` | `GET /health`, `/auth/me` |

## Workflows

**Dataset choice.** Values are always read from one dataset version, named in `?dataset=`. Without it, the newest locked version (else the newest draft) is proposed and labelled "proposed by default". The picker always shows basis (`standardized` = SEC, `reported` = imports), version, status, currency and facts-as-of date. A dataset ID that belongs to another company is refused; no other dataset is substituted.

**Statement grid.**
- Line items appear in mapping order; one column per period (at most 60, per the API).
- Controls: statement, period type, fiscal-year range, display scale and decimals.
- Monetary and share-count rows scale; per-share rows never do. Negatives are shown in parentheses.
- Each cell shows:
  - a provenance tag: **R** reported, **D** derived, **I** imported, **A** analyst decision;
  - **!** for an open conflict, **✓** for a decided or immaterial one;
  - *ambiguous* when no value was chosen;
  - — when no value is stored.
- Selecting a cell opens its provenance:
  - the exact stored value, unit, status, concept and sign convention;
  - the SEC facts, with the original reported value, form, accession, filing date and fact period;
  - or the imported file, with its SHA-256, cell, source document and page;
  - the derivation or selection detail;
  - a link to the cell's conflict.

**Conflicts.**
- Filter the list by status and materiality, then select a conflict to see:
  - the materiality figures;
  - every candidate with its provenance, and the policy choice;
  - the active decision and the append-only decision history.
- Analysts and administrators can select a candidate, enter an exact value with a source, record an override, or revoke the active decision. Revoke is offered only when a decision is active.
- Every decision needs a reason and a confirmation.
- A decision takes effect when the draft is rebuilt; the result links to *Datasets & workflow*.

**Import preview.**
- Choose the file type, template and parameters, then a `.csv` file of at most 20 MB. XLSX is refused (P14).
- The preview runs on a read-only session: nothing is stored, archived or logged.
- The result shows:
  - file errors and warnings;
  - issuer, periods and metadata (statements), or lineage, outcome and current version (reference data);
  - counts per record status (accepted, duplicate, conflict/conflicting, unmapped, ambiguous, rejected);
  - a filterable record table with every row's errors and warnings.
- The *ingest* step lists what ingestion requires: the previewed SHA-256 as `expect_sha256`, a source, `allow_partial` when records would be skipped, `allow_conflicts` for conflicting statement values, and `supersede` + reason for a changed reference lineage.

**Classification (D53).**
- Shows the effective classification, its status, the latest suggestion and confirmation, and the append-only history (suggestions and confirmations, with evidence on demand).
- Analysts and administrators confirm a class with a reason (at least 10 characters) and a confirmation. A warning appears when the choice differs from the classifier's suggestion.

**Listings and market data (D56, D58).**
- *Listings* lists every listing (filter by ticker/MIC); the company overview links each ticker to its listing workspace.
- The listing workspace shows identity, provider symbols and origin, then:
  - **prices** for one provider and one close basis, with date range, latest-or-all revisions and order; values are the stored decimal strings; each row links to its import row (or names the provider fetch) and shows its revision count. When several series exist and none is chosen, the backend's `409 ambiguous_series` is shown as *Choose a series*;
  - **corporate actions** (splits with exact ratios, cash dividends with amount and currency source).
- **Trading currency** is declared once, with an ISO 4217 code, a reason and a confirmation. Once declared, the control is disabled with the reason; declaring the same value again returns `changed: false`, a different one is refused (`409`).
- **Registration** (`#/listings/register`, or *Register another listing* from a company): name the issuer (existing company, same issuer as an existing listing, SEC CIK, or a new issuer), the listing (ticker, MIC, trading currency, security type, optional share class, ISIN and `provider=SYMBOL` lines) and the evidence (source, optional reference, reason).
  - *Check* runs the read-only validation and shows errors, warnings, suggested provider symbols and the issuer resolution. Nothing is stored or logged.
  - *Register* is enabled only for the exact request that passed the check; changing any field requires a new check. It asks for confirmation and links to the new listing and company.

**Reference data (D57).**
- Current version per lineage by default; *every version* includes superseded ones. Filter by kind.
- A version shows its lineage, supersedes/superseded-by links, correction reason, source, imported file and hashes, then its records in file order: one column per field with its unit (percentages are percentage points), text values as stored, the file location on hover, and lookup by record key.
- Read-only: reference files are imported through *Imports* (ingestion pending P15).

**Rates & FX (D58).** Read-only; there is no fetch, import or provider control.
- *Interest-rate series* lists the registered series (US Treasury par yields, Government of Canada benchmark bond yields) with code, description, provider and provider series ID, currency, tenor, unit and the number of stored observations with their first and last dates. A series without observations says *none stored*.
- A series shows its quotation convention, the **latest stored observation**, and the stored history: date range, *latest per date* or *every stored revision*, newest or oldest first, 50 per page.
  - Values are the stored decimal strings in **percentage points** as published (4.84 = 4.84 % per annum); the UI never converts them.
  - Dates the provider did not publish (holidays, `N/A`) are not stored and are not filled: the table lists stored dates only, and an empty range says so.
  - In *every stored revision* mode, each date's revision count is shown; when all of a date's revisions are on the page, the one in effect (highest observation ID, the value returned in *latest* mode) and the superseded ones are marked.
- *FX rates* needs a base and a quote (ISO codes, validated locally). The stored direction is stated (`CAD per 1 USD`: 1 USD = rate CAD); rates are never inverted and cross rates are never calculated. Asking for the reverse of a published pair returns nothing and links to the published direction. The Bank of Canada pairs the adapter is configured for (USD, EUR, GBP → CAD) are shortcuts; whether anything is stored comes from the API. Several providers for one pair → `409 ambiguous_series` (*Choose a series*). Each row names its provider series and fetch.

**Action history.** Lists your own actions (successes and refusals) with target and refusal reason. Each action's detail shows the stored response. Administrators can open any action by its ID.

## Role behavior

| Capability | admin | analyst | reviewer |
|---|---|---|---|
| Browse companies, statements, provenance, conflicts, imports, own actions | ✓ | ✓ | ✓ |
| Browse classification history, listings, prices, corporate actions, reference data, rates and FX | ✓ | ✓ | ✓ |
| Verify a dataset | ✓ | ✓ | ✓ |
| Build, rebuild, discard, record conflict decisions, preview files | ✓ | ✓ | disabled, with the reason |
| Confirm a classification; check and register a listing; declare a trading currency | ✓ | ✓ | disabled, with the reason |
| Open any user's action by id | ✓ | – | – |
| **Lock a dataset** | **pending P15** | **pending P15** | **pending P15** |
| **Ingest a file** | **pending P15** | **pending P15** | **pending P15** |

- **Lock and ingestion** are shown as unavailable, with the P15 explanation, because no role holds `dataset:lock` or `data:import`.
- **When the owner grants them:** the controls enable themselves from `/auth/me`. Lock is still explained as unavailable while material conflicts are open. Any backend refusal (`403 pending_approval`, `409`, `503 archive_unavailable`) is shown as returned.

## Errors

Every failure shows the backend's message under a plain-language title:

| Status | Title |
|---|---|
| `401` | Not signed in. Any `401` also returns the app to the sign-in page. |
| `403` | Not permitted, Awaiting owner approval (`pending_approval`), or Security check failed (`csrf_failed` / `origin_rejected`) |
| `404` | Not found |
| `409` | Refused by a business rule, with a link to the recorded refusal; `ambiguous_series` (prices) → Choose a series |
| `413` | File too large |
| `422` | Invalid input, with field details |
| `503` | Temporarily unavailable; for ingestion, plus the archive explanation |
| network failure | Server unreachable |

`4xx` responses are not retried.

## Tests

`make test-frontend` (Vitest + Testing Library; `fetch` is mocked, no server needed) covers:
- workspace rendering;
- dataset defaults and basis visibility;
- grid formatting and markers, and provenance for SEC and imported values;
- conflict inspection, the authorized resolution flow and reviewer read-only behavior;
- import preview statuses and row issues, XLSX refusal, the pending-ingestion state for every role and the simulated-approval ingestion;
- action-history visibility;
- pending-permission behavior for lock and import;
- CSRF and `Idempotency-Key` headers on POSTs;
- 401/403/409/422/503 handling;
- loading and empty states;
- exact decimal formatting;
- classification history, confirmation flow, reviewer read-only and refusals;
- listing workspace: exact prices and ratios, origin links, ambiguous series, empty states, currency declaration and its guards, 409 with the recorded action;
- listing registration: check → register of the identical body, stale checks, error reports, preset issuer, 422 details, reviewer read-only;
- reference data: current/all versions, lineage and provenance, records with units, record lookup, 404;
- rates & FX: series list (units, empty series), exact values, latest observation, date range and paging, revision marks, reversed range refused locally, FX direction, no inversion, pair validation, ambiguous provider, read-only for every role, 404/401;
- routing of the Listings, Reference data and Rates & FX sections.

The backend test `test_frontend_api_types.py` keeps the generated types current.
