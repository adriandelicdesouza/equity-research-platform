# Workflow UI (M3 phase 3)

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
| Imports | `#/imports`, `#/imports/{id}`, `#/imports/preview` | `GET /imports`, `/imports/{id}`; `POST /imports/{kind}/validate`, `POST /imports/{kind}` |
| Action history | `#/actions`, `#/actions/{id}` | `GET /actions`, `/actions/{id}` |
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

**Action history.** Lists your own actions (successes and refusals) with target and refusal reason. Each action's detail shows the stored response. Administrators can open any action by its ID.

## Role behavior

| Capability | admin | analyst | reviewer |
|---|---|---|---|
| Browse companies, statements, provenance, conflicts, imports, own actions | ✓ | ✓ | ✓ |
| Verify a dataset | ✓ | ✓ | ✓ |
| Build, rebuild, discard, record conflict decisions, preview files | ✓ | ✓ | disabled, with the reason |
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
| `409` | Refused by a business rule, with a link to the recorded refusal |
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
- exact decimal formatting.

The backend test `test_frontend_api_types.py` keeps the generated types current.
