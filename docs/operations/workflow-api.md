# Workflow actions API (M3 phase 2)

Decision D60, pending decision P15. The read-only data API is described in `data-api.md` (D59).

The API exposes the existing workflow services as authenticated, audited POST actions. No business rule was re-implemented. Each handler validates its request and calls the same service the CLI uses, so these all apply unchanged:
- reasons;
- the conflict gate before locking;
- verification before locking;
- append-only records;
- hash confirmation;
- the partial, conflict and supersede refusals;
- listing duplicate checks.

## Every action

1. **Login:** a cookie session is required (`POST /api/auth/login`).
2. **CSRF:** the `X-CSRF-Token` header must equal the session's token (returned by login and by `GET /api/auth/me`), and the `Origin` must be an allowed origin. Otherwise the request gets `403 csrf_failed` or `403 origin_rejected`.
3. **Permission:** each action needs the permission in the matrix below. Without it the request gets `403 forbidden`; for permissions that await approval it gets `403 pending_approval`.
4. **Validation:** bodies are strict JSON (unknown fields are refused) and parameters are typed. Invalid input gets `422 validation_error` with `details`.
5. **Execution:** the existing service runs in the request's transaction.
   - On success, the domain change, its `audit.audit_events` row (written by the service, with the acting user) and an `audit.api_actions` record are **committed together**.
   - On a refusal, everything is rolled back and only the refusal is recorded.
6. **Response:**
   - success: `{"action_id", "action", "result"}`;
   - refusal: `{"error": {"code", "message", "action_id"}}`.

   Codes:

   | Status | Code | When |
   |---|---|---|
   | `409` | `action_refused` | a business rule forbids the action now |
   | `404` | `not_found` | unknown target |
   | `503` | `archive_unavailable` | ingestion while the API cannot write the raw archive |
   | `500` | `internal_error` | unexpected failure: no SQL, paths or traceback |
7. **Results stay inspectable:**
   - `GET /api/actions` lists your actions;
   - `GET /api/actions/{action_id}` returns one result (yours; administrators see any).
8. **Idempotency:** send `Idempotency-Key: <8–128 chars [A-Za-z0-9_.:-]>`.
   - A retry with the same key and the same request **replays the first response** (header `Idempotent-Replayed: true`) without acting again.
   - The same key with a different request gets `422 idempotency_key_reused`.
   - Keys are per user.
   - Imports and builds are also naturally idempotent: an identical file or identical inputs store nothing.

**Previews** (`.../validate`) are POST because they carry a body, but they run on the **read-only session** (D59). They cannot persist anything and are not recorded as actions.

**Long-running work** runs synchronously. Measured on the dev data in the 384 MB API container, `verify` (a full rebuild) of the largest dataset takes 2.8 s with a 172 MB peak. The result is returned and also kept in the action log. A job queue can be added if datasets grow.

## Role-permission matrix

| Action | Endpoint (POST) | Permission | admin | analyst | reviewer | Status |
|---|---|---|---|---|---|---|
| Build dataset | `/api/companies/{id}/datasets/build` | `company:write` | ✓ | ✓ | – | active |
| Rebuild draft | `/api/companies/{id}/datasets/rebuild` | `company:write` | ✓ | ✓ | – | active |
| Verify dataset | `/api/datasets/{id}/verify` | `company:read` | ✓ | ✓ | ✓ | active (writes nothing) |
| Discard draft | `/api/datasets/{id}/discard` | `company:write` | ✓ | ✓ | – | active |
| **Lock dataset** | `/api/datasets/{id}/lock` | `dataset:lock` | – | – | – | **pending P15** |
| Resolve / override / revoke a conflict | `/api/datasets/{id}/conflicts/{key}/resolutions` | `company:write` | ✓ | ✓ | – | active (Blueprint §10.3: analyst) |
| Confirm classification | `/api/companies/{id}/classification/confirm` | `company:write` | ✓ | ✓ | – | active (D53: analyst) |
| Preview statement / market / reference file | `/api/imports/{statements,market,reference}/validate` | `company:write` | ✓ | ✓ | – | active (read-only) |
| **Ingest statement / market / reference file** | `/api/imports/{statements,market,reference}` | `data:import` | – | – | – | **pending P15** |
| Validate listing | `/api/listings/validate` | `company:write` | ✓ | ✓ | – | active (read-only) |
| Register listing | `/api/listings` | `company:write` | ✓ | ✓ | – | active |
| Declare trading currency | `/api/listings/{id}/trading-currency` | `company:write` | ✓ | ✓ | – | active |

**Pending (P15).**
- `data:import` and `dataset:lock` exist in code but are granted to **no role, not even admin** (`auth/rbac.py` `PENDING_APPROVAL`). Every call is answered with `403 pending_approval`.
- Proposal for the owner:
  - `dataset:lock` → **admin and analyst.** Locking is the analyst's sign-off that a dataset is fit for valuation. It is already gated by verification and the material-conflict rule.
  - `data:import` → **admin only** at first. This matches MASTER_BRIEF §19 "Administrator: data management". Analysts can preview, and could be added later.
- **Approval needed:**
  - the owner states which roles get each permission;
  - the change is two edits in `ROLE_PERMISSIONS` plus their tests;
  - for API ingestion, the API container also needs the raw-archive mount, the same bind mount the worker has, running as the host UID. Until then ingestion returns `503 archive_unavailable`.
- The CLI keeps working for all of these in the meantime.

## Endpoints and examples

Headers on every action: `Cookie: erp_session=…`, `X-CSRF-Token: …`, `Origin: https://127.0.0.1:8444`, and an optional `Idempotency-Key`.

### Datasets

```http
POST /api/companies/{company_id}/datasets/build
{"basis": "standardized", "policy": "latest_reported", "facts_as_of": null}
```
- `201` = new version; `200` = identical to the current version, nothing stored.
- `basis` is `standardized` (SEC) or `reported` (imports).

```json
{"action_id": "…", "action": "dataset.build",
 "result": {"dataset": {"id": "…", "basis": "standardized", "version_no": 1, "status": "draft",
                        "mapping_version": "v2", "facts_as_of": "2026-02-01", …},
            "created": true,
            "validation": {"fail": 0, "pass": 12, …},
            "conflicts": {"open_material": 1, …}}}
```

Other dataset actions:
- `POST /api/companies/{id}/datasets/rebuild` with body `{"basis": "standardized", "reason": "Apply the recorded conflict decision."}`. It discards the open draft and builds the next one; locked versions are untouched.
- `POST /api/datasets/{id}/verify` (no body). Returns `ok`, `stored_matches`, `validation_matches`, `rebuild_matches`, `inputs_match`, `mapping_matches` and `recomputed_sha256`.
- `POST /api/datasets/{id}/discard` with body `{"reason": "…"}`. Drafts only.
- `POST /api/datasets/{id}/lock` with body `{"reason": "…"}`. **Pending P15.** Once approved, it is refused while material conflicts are open, values are ambiguous or verification fails.

### Conflicts

```http
POST /api/datasets/{dataset_id}/conflicts/{conflict_key}/resolutions
{"action": "select_candidate", "candidate_id": 1316, "candidate_source": "reported_fact",
 "reason": "Original vintage is consistent with the liabilities reported."}
```

| `action` | Required fields |
|---|---|
| `select_candidate` | `candidate_id`, plus `candidate_source` (`reported_fact` / `import_value`) when the id is ambiguous |
| `enter_value` | `value` (a decimal string) and `source_reference` |
| `override` | none |
| `revoke` | none; refused with `409` when nothing is active |

All actions also need `reason` (at least 10 characters, with words).

Each decision is appended. The detected conflict and earlier decisions are never edited, and the decision takes effect at the next `rebuild`.

### Classification

```http
POST /api/companies/{company_id}/classification/confirm
{"classification": "non_financial", "reason": "Industrial company; no lending or insurance."}
```

### Imports

The file is the **raw body** (`Content-Type: text/csv`, at most 20 MB). Metadata goes in query parameters.

```http
POST /api/imports/statements/validate?filename=stc_2024.csv         → the validation report
POST /api/imports/statements?filename=stc_2024.csv&expect_sha256=<from the report>
     &source=Annual%20report%202024&allow_partial=false&allow_conflicts=false   (pending P15)
POST /api/imports/market/validate?template=market_prices.v1&filename=…
POST /api/imports/market?template=corporate_actions.v1&filename=…&expect_sha256=…&source=…
POST /api/imports/reference/validate?template=damodaran_country_risk.v1&edition_date=2026-01-05&filename=…
POST /api/imports/reference?…&supersede=true&reason=Corrected%20transcription
```

Results come back with the import id and counts, and for statements the resulting `reported` draft. `GET /api/imports/{id}` (D59) shows the stored import. Providers stay disabled: file imports make no network request (P13).

### Listings

```http
POST /api/listings/validate     (same body as below; read-only check report)
POST /api/listings
{"ticker": "NWC", "mic": "XTSE", "currency": "CAD", "security_type": "common_equity",
 "issuer": {"new": {"legal_name": "New Workflow Corp", "country": "CA"}},
 "source": "TSX listed-company directory", "reason": "Listing confirmed on the exchange.",
 "provider_symbols": {"yahoo_chart": "NWC.TO"}}
```

`issuer` takes exactly one of:
- `company_id`;
- `same_issuer_as` (`{"ticker", "mic"}`);
- `cik`;
- `new`.

Name collisions with existing issuers need `confirm_new_issuer: true`.

```http
POST /api/listings/{listing_id}/trading-currency
{"currency": "USD", "reason": "Nasdaq listing trades in USD."}
```
The currency is declared once. Declaring the same value again returns `changed: false`; a different value is refused.

## Database

Migration `0016` adds `audit.api_actions`:
- append-only for every role; the app role has INSERT/SELECT only;
- columns: user, action, target, outcome, status, response, request fingerprint, optional idempotency key;
- unique on (user, key).

Domain tables are unchanged.

## Tests

The workflow tests run on `erp_test` with synthetic data and are rolled back per test:
- `test_workflow_infra.py`;
- `test_workflow_datasets.py`;
- `test_workflow_conflicts.py`;
- `test_workflow_imports.py`;
- `test_workflow_listings.py`;
- `test_workflow_routes.py`: every action × no session / no role / CSRF / Origin / permission, with no data mutation.

Pending permissions are exercised only through a test-only grant (`grant_pending`), which simulates the owner's approval.
