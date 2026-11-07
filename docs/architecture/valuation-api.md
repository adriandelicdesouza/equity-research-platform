# Valuation API design (M3 phase 5)

**Status: APPROVED (P16 resolved, 2026-11-07) and IMPLEMENTED (Phase 5, Session 14) — decision D62.** Phase 5b (scenarios §9.3, sensitivities §9.5) is implemented in Session 15 — decision D63 (migration `0018`); regression beta and assumption impact remain open. Operations guide (endpoints, codes, data requirements): `docs/operations/valuation-api.md`.

**Written:** 2026-11-07, Session 13, against `m2-ingestion` @ `771bb0b` (Alembic `0016`, engine `0.1.0`). Implemented in Session 14 (Alembic `0017`).
**Builds on:** Blueprint §3.3–§3.4, §6, §7, §8, §10, §11.2, §14.1 (M3/M4); D9, D10, D14, D18–D27, D35–D39, D50–D53, D58–D61; `docs/methodology/dcf.md`; `docs/operations/workflow-api.md`.

## 0. Implementation notes (Session 14)

The approved decisions (§12) are implemented as specified. Where the implementation refines the proposal:

- **Run decisions table:** `val.run_decisions` (append-only) records every classification acknowledgement, dataset-check override, input override and administrator override with user, time, reason and run (approved qualification 3). It was not in the §10 table list.
- **Sensitivity table not created:** `val.sensitivity_runs` belongs to Phase 5b and will come with its own migration.
- **Extra read endpoint:** `GET /valuation/assumption-keys` exposes the key registry (types, units, ranges, sources) so the UI builds its forms from the server. 24 routes in total.
- **Lease treatments:** Phase 5 supports `none`, `ifrs16_as_debt` and `asc842_as_operating` (FCFF), the combinations that need no per-year lease forecasts; FCFE supports `none`.
- **Market-value weights:** equity = market price × basic shares at B (not diluted shares); preferred equity with market weights is refused (`unsupported_capital_structure`: no cost of preferred in Phase 5).
- **Roll-forward:** dividends between B and V are not deducted; a run with V > B carries the warning `dividends_not_modelled`.
- **Identity (approved qualification 2):** the content identity is the SHA-256 of the canonical input document, which includes the project, scenario and **scenario revision**, methodology, method version, dataset identity, every input with provenance, settings, decisions and the engine version. Unique per (project, input hash, engine version).
- **Reproduction (qualification 4):** a pure `compute(document)` function serves previews, runs and reproduction, so reproduction cannot read current market data or newer reference versions.
- **Error envelope:** valuation refusals add an optional `details` list to the existing `{"error": {...}}` envelope; `ApiError` gained the same optional field.

## 1. Scope and principles

The valuation workflow is Blueprint §14.1 M3's remaining part: **project → assumptions → DCF → trace**. This design covers the backend API and schema that the UI will call. The engine (`erp_engine`, M1) already implements FCFF, FCFE, WACC, terminal value, bridge, dilution, roll-forward, classification gate, trace and hashes. Phase 5 does not re-implement any of it.

Principles (each is an existing decision, restated as an API rule):

1. **Python is authoritative** (D25, brief §16.5). The browser never calculates; previews call the engine through a non-persisting endpoint.
2. **Explicit inputs, never "latest"** (D10, brief §17.1). Every run names one dataset version and every market or reference input by row id. The server refuses a run rather than substituting another version.
3. **Self-contained runs** (Blueprint §15). A saved run stores its fully materialized input document, so reproduction never reads mutable or newer rows.
4. **No suggestion in a saved run** (D14, brief §2.2). Suggested values must be approved first.
5. **Reuse, nothing parallel** (D59/D60). Cookie sessions, CSRF + Origin checks, RBAC dependencies, the error envelope, `workflow.perform` (audit + `audit.api_actions` + `Idempotency-Key`), read-only sessions, decimal strings, paginated envelopes.
6. **Append-only where history matters.** Runs, run inputs, assumption revisions and sensitivity results are never updated or deleted, enforced by triggers for every role (D31 pattern).

## 2. Proposed architecture

```
UI (D61) ──HTTP──► api/valuation.py          thin handlers, Pydantic request/response schemas
                    │  GET  → read/valuation.py   (read-only session, D59)
                    │  POST → workflow.perform → valuation/* services (audited, one transaction)
                    ▼
        erp_backend/valuation/
          projects.py      project + scenario lifecycle, ownership, optimistic revisions
          assumptions.py   append-only assumption revisions, approval, key registry
          blocks.py        classification blocks (§7): load, look up, check against the engine gate
          inputs.py        resolver: dataset values, rates, FX, prices, reference values → InputDocument
          canonical.py     canonical JSON + SHA-256 (reuses the D50 decimal normalization)
          methods/         one adapter per methodology: InputDocument → engine dataclasses → outputs
            dcf.py           dcf_fcff, dcf_fcfe (Phase 5)
          runs.py          gates, engine call, persistence of the immutable run
          sensitivity.py   grids over a stored run (Phase 5b)
                    ▼
        erp_engine (unchanged API: run_valuation, wacc.*)   pure, no I/O, no clock (D36)
```

- **Methodology registry.** `methods/` maps a methodology code (`dcf_fcff`, `dcf_fcfe`; later `comps_trading`, `ddm`, …) to an adapter with `version`, `produces` (D18 output kind), `required_keys`, `resolve(document) → engine inputs` and `outputs(result) → typed outputs`. The run service is methodology-agnostic: it resolves inputs, applies gates, calls the adapter and stores the result.
- **Request schemas are discriminated unions** on `methodology`, so each method validates only its own settings and a new method adds a variant, not new endpoints.
- **Synchronous execution.** A 10-year DCF is a few hundred Decimal operations; a 7×7 grid is 49 runs (Blueprint §8.2: well under a second). No job queue is needed in Phase 5. Grids above 100 cells are refused until a job queue is approved.

## 3. Valuation projects (A)

A **project** is the mutable research container for one company: its thesis, its pinned dataset, its scenarios and assumptions. **Runs** are immutable results created from a project at a point in time.

```
company 1─n project 1─n scenario 1─n assumption revision (append-only)
                │                 │
                └──────1─n run ───┘   each run: one scenario at one revision, one dataset version
```

| Field | Rule |
|---|---|
| `company_id` | Required, existing company. Immutable after creation. |
| `listing_id` | Optional default listing (must belong to the company). Needed for a market price. |
| `name` | 1–120 characters, trimmed; unique among the company's active projects. |
| `thesis` | Optional text, ≤ 20 000 characters. |
| `dataset_id` | Required **pinned dataset**: same company, not discarded. A draft may be pinned for previews; runs require it locked (§4). |
| `owner_user_id` | Creator by default. Transfer is an admin action. |
| `status` | `active` → `archived` → `active` (both with reason). No deletion. |
| `revision_no` | Incremented on every change; mutations send `expected_revision` (optimistic concurrency → `409 revision_conflict`). |

**Lifecycle actions:** create (also creates the `base` scenario), update metadata, **rebase** (pin another dataset version of the same company: explicit, with reason; Blueprint §3.4 step 3), archive, restore, transfer ownership. Archived projects are read-only; their runs stay readable. Rebasing never changes existing runs.

**Newer data:** project detail reports `newer_locked_dataset` (id, version, diff summary from the dataset's `source_summary`) when a later locked version exists. It is information only; nothing re-bases automatically.

**Authorization:** reading needs `valuation:read`. Changing a project needs `valuation:write` **and** ownership; an administrator may act on any project, and such actions are audited with `admin_override: true` and a required reason (§9; approval question Q3).

## 4. Valuation run model (B)

A run is created by `POST …/runs` and is immutable from that moment. It records exactly one methodology execution for one scenario.

### 4.1 Gates (all checked server-side, in this order; any failure → no run, `409`)

1. Project active; caller may write it; `expected_scenario_revision` matches.
2. **Dataset:** the request's `dataset.id` equals the project's pinned dataset (`dataset_mismatch`), its `content_sha256` equals the request's (`dataset_hash_mismatch`), status is `locked` (`dataset_not_locked`), and it belongs to the project's company. Both bases are accepted: `standardized` (SEC, D50) and `reported` (file import, D57); the basis is recorded.
3. **Classification:** the effective class (D53) is read at run time. `needs_review` → `classification_unconfirmed`; a class the block marks `blocked` for this methodology → `classification_blocked` (§7).
4. **Dataset checks** (D51 leaves "approval for valuation" to this workflow): failed `error`-severity checks on the periods the run uses refuse the run unless the request carries an override with reason (`checks_failed`; Q12).
5. **Assumptions:** every required key for the methodology and block is present and `approved` (`assumption_not_approved`, `input_missing`, each listing keys).
6. **Inputs:** every market/reference input resolves to the named row, is consistent in currency and date, and is current (§5; `input_*` codes).
7. **Engine:** `EngineError` → `engine_refused` with the engine's code (e.g. `dcf_not_permitted`, `g_ge_wacc`); validation issues with `blocks_save` → `validation_blocked`.

### 4.2 What is stored, referenced or reconstructed

| Content | Treatment | Why |
|---|---|---|
| Company, listing, scenario id + revision, methodology + adapter version | **Stored** (columns) | Identity and query keys |
| Dataset id, version_no, basis, `content_sha256`, `mapping_version`, `facts_as_of` | **Stored** (columns) | Proves which version was used; verifiable against `fin` |
| Every numeric input actually used (dataset values at `B`, Rf, ERP, CRP, beta, spread, FX, price, assumptions, overrides) | **Snapshotted** in `input_document` with value, unit, provenance and source reference | Self-contained reproduction (§15 of Blueprint) even though sources are append-only |
| Source row ids (fact ids, observation ids, reference dataset id + record key + field, revision ids) | **Referenced** inside each input, and indexed in `val.run_inputs` | Lineage drill-down (Blueprint §10.2) and "which runs used X" queries |
| Settings (convention, terminal form/timing, lease and SBC treatment, stub mode, ERP basis, CRP method, relevering) | **Snapshotted** | Part of the input |
| Classification at run time, block id/version, acknowledgements, check overrides | **Snapshotted** | Gates must be explainable later |
| Engine version, module versions, module order | **Stored** | Brief §17.1 |
| Outputs (typed, D18), forecast table, terminal, bridge, dilution, WACC build-up, validation report | **Stored** unrounded | Output hash covers them |
| Trace | **Stored** in `val.run_traces` (JSONB, own hash) | Inspection without re-execution (Q7) |
| `input_sha256` (backend canonical document), engine `input_hash`, `output_sha256`, `trace_sha256` | **Stored** | Integrity and reproduction |
| Display rounding, scaled units, scenario diff tables, comparisons between runs | **Reconstructed** at read time | Presentation only |
| Historical context (growth, margins) shown beside assumptions | **Reconstructed** from the pinned dataset | Context, not an input |

`created_by`, `created_at`, `request_id` and the `audit.api_actions` id are stored on the run.

### 4.3 Reproduction

`POST /runs/{id}/reproduce` re-executes the stored `input_document` with the installed engine in a read-only session and stores nothing:
- same engine and module versions → the output hash must match; a mismatch is reported as `integrity_failure` (a defect, never "fixed" silently);
- different versions → returns both hashes and the per-output differences.

Old engine versions are not installed side by side; exact re-execution is guaranteed for the current version only, and stored outputs remain the record for older ones (Blueprint §6.5).

### 4.4 Content idempotency

A run whose `input_sha256` and engine version equal an existing run of the same project returns that run (`200`, `existing: true`) instead of creating a duplicate, like dataset builds (D50). An `Idempotency-Key` additionally replays the first HTTP response (D60).

## 5. Financial inputs (C)

### 5.1 Sources and selection

| Input | Source (existing) | Selected by | Phase 5 |
|---|---|---|---|
| Historical statements at `B` (revenue, EBIT, D&A, capex, NWC items, cash, ST investments, debt, leases, preferred, NCI, shares) | `fin.financial_values` of the pinned dataset | The run's `balance_sheet_period_id` (an FY period of that dataset) + the block's line-item map (§7) | Yes |
| Point-in-time basic shares | `BS.SHARES_OUTSTANDING` in the dataset, or an analyst assumption with source | Line-item map; never `IS.SHARES_*_WA` (D23; the engine refuses weighted-average bases) | Yes |
| Risk-free rate | `market.rate_observations` (percentage points, D58) | Assumption `risk_free_rate` with source `{rate_observation_id}`; series currency must equal the cash-flow currency | Yes (analyst value with source until a fetch is approved, P9) |
| Market price | `market.price_observations` | Run request: `price_observation_id` (one provider, one close basis) | Yes |
| FX | `market.fx_rates` (stored direction, D58) | Run request: `fx_rate_id` per conversion | Yes, only when price and reporting currencies differ |
| Mature ERP, CRP, industry unlevered beta, rating spreads | `ref.reference_values` (Damodaran, D58) | Assumption with source `{reference_dataset_id, record_key, field}` | Yes |
| Forecast drivers, terminal, WACC choices, instruments, bridge additions | Analyst | Assumption revisions | Yes |

The read-only `GET …/input-candidates` endpoint proposes candidates (latest observation on or before the valuation date, current reference versions, dataset values at `B`) **as explicit ids with status `suggested`**. The client submits the ids it accepts; the server never fills an omitted input with "latest".

### 5.2 Validation and missing data

- **Required inputs** come from the methodology adapter plus the classification block. A missing required input refuses the run with the complete list (`input_missing`); the preview returns the same list as issues.
- **Missing is never zero.** A bridge item absent from the dataset at `B` must be resolved by the analyst: an assumption with value and source, or `not_applicable` with reason. Both are recorded.
- **No defaults, interpolation or carry-forward** (D58). The resolver reads only stored rows.
- **Ambiguity** (a `status ≠ ok` value, an open or within-filing conflict surviving in the dataset) refuses that input (`input_ambiguous`).

### 5.3 Units, currency and precision

- **Decimal only** (D9): `Decimal`, precision 34, `ROUND_HALF_EVEN`; `NUMERIC` without scale in Postgres; decimal strings in JSON. Stored values are unrounded.
- **Ratios are fractions** in the API and the engine (`"0.0425"`, unit `ratio`). **Percentage points are converted at the input boundary only**: `fraction = pp / 100` (exact). Both the stored value (with its unit) and the converted value are kept in the input document, with `conversion: "pp_to_fraction"`. Reference values convert by their stored `unit`; an unknown unit refuses the input.
- **Money** is in the dataset currency at scale 0: `value × 10^scale` at the boundary; currency and scale are carried on every input.
- **Cash-flow currency = dataset currency.** Rf series currency must match it (D21, engine `rate_currency`).
- **Price currency** = the listing's declared trading currency (D56). If it differs from the dataset currency, one FX input is required. Rates are used in their stored direction; whether the exact reciprocal may be derived at the boundary (rule recorded) is Q5. Cross rates are never computed.

### 5.4 Stale, conflicting and unavailable inputs

| Situation | Behaviour |
|---|---|
| Observation or edition dated **after** the valuation date | Error `input_lookahead` |
| Price, Rf or FX older than 7 calendar days before the valuation date | Warning `input_stale`, recorded on the run (threshold configurable; Q6) |
| Damodaran edition older than 13 months before the valuation date | Warning `input_stale` |
| Selected rate/FX observation superseded by a later revision for the same date (D58 `revised`) | Error `input_superseded`; the client selects the current revision |
| Selected reference dataset superseded by a newer version of its lineage | Error `input_superseded` |
| Price series ambiguous (several providers / bases) | Already resolved by the explicit observation id; the candidate endpoint returns `409 ambiguous_series` like D59 |
| Pinned dataset discarded | Project must be rebased (`dataset_discarded`) |
| No stored observation at all (providers disabled, P9) | Input unavailable: the analyst enters the value as an assumption with a written source |

Warnings never block; they are stored on the run and shown on every output.

## 6. Valuation methodologies (D)

| Capability | Engine status | Phase 5 (MVP) | Later |
|---|---|---|---|
| DCF — FCFF, FCFE | Implemented (M1) | `dcf_fcff`, `dcf_fcfe` | — |
| Forecast drivers, horizon 1–20 | Implemented | Per-year approved drivers | Segment/three-statement (R2) |
| Terminal value: value-driver (D35), simple growth, exit multiple; timing (D27) | Implemented | All forms | — |
| Stub Mode A/B, roll-forward | Implemented (FCFE stub refused, D39) | **No stub**: `B` = a fiscal year-end | Interim `B` when interim periods are used |
| WACC: CAPM, ERP/CRP separation, Hamada/Harris–Pringle, Kd, weights | Implemented (`erp_engine.wacc`) | Constant WACC; beta analyst-entered or bottom-up (Damodaran) | Per-period WACC vector; synthetic rating |
| Regression beta | Implemented | — (no prices stored yet) | When price history exists |
| Scenarios (inheritance, probabilities) | Engine-independent | Base scenario only | **Phase 5b**: bull/bear/custom, inheritance, weights |
| Sensitivity (1-D tornado, 2-D grids) | Re-runs of the engine | — | **Phase 5b**: grids over a stored run |
| Comparable companies | Not implemented | — | R4 (schema reserved: methodology registry, output kinds, `basis`) |
| Reverse DCF, Monte Carlo, DDM, residual income, SOTP | Not implemented | — | R4 |

**Sequencing note:** the Blueprint puts scenarios, sensitivity and saved immutable runs in M4. This design stores immutable runs from the first phase-5 increment (a saved valuation must never be mutable) and specifies scenario and sensitivity contracts now so the schema does not change later; their endpoints ship in 5b.

## 7. Classification blocks (E)

A **classification block** is a versioned, declarative record keyed by **(financial classification, methodology)** that says how a methodology may be applied to companies of that class. It is data, not code (Blueprint §7.6.1: "policy is data-driven").

```yaml
# mappings/valuation-blocks/v1.yaml  (proposed; loaded and pinned by migration, like D49 mappings)
version: v1
blocks:
  - classification: non_financial
    methodology: dcf_fcff
    policy: permitted                     # permitted | permitted_with_warnings | blocked
    line_items:                            # engine input ← standard line items at B (signed sums)
      bridge.cash: [BS.CASH]
      bridge.short_term_investments: [BS.ST_INVESTMENTS]
      bridge.debt: [BS.ST_DEBT, BS.LT_DEBT]          # exact codes fixed at implementation
      bridge.preferred: [BS.PREFERRED_EQUITY]
      bridge.nci: [BS.NCI]
      shares.basic: [BS.SHARES_OUTSTANDING]
    drivers: [revenue_growth, ebit_margin, tax_rate, da_pct, capex_pct, nwc_pct]
    required_assumptions: [terminal_form, terminal_g, ronic, risk_free_rate, mature_market_erp, beta_method, ...]
    checks: []
  - classification: reit
    methodology: dcf_fcff
    policy: permitted_with_warnings
    acknowledgements: [reit_fair_value_gains]
  - classification: bank
    methodology: dcf_fcff
    policy: blocked                        # no override in R1 (Blueprint §7.6.1)
```

| Aspect | Rule |
|---|---|
| **Line-item mapping** | Each engine input that comes from the dataset is a declared signed sum of standard line-item codes (the D49 subtotal style; not a formula language). Lease items follow the run's `lease_treatment` (D22). |
| **Driver selection** | The block lists the forecast drivers the methodology needs for that class; assumptions outside the set are refused (`assumption_not_applicable`). |
| **Model-specific assumptions** | `required_assumptions` per block (e.g. FCFE adds `debt_path`, `cash_path`, `cost_of_debt`, `cash_yield`). |
| **Validation** | `policy` and `acknowledgements` (warnings the analyst must acknowledge with a reason before a run). Extra checks are named engine/backend checks, never expressions. |
| **Overrides** | A run may replace one mapped input with an analyst value (`input_override`: value, source, reason ≥ 10 characters). The dataset is never edited; analyst adjustments to datasets (`fin.analyst_adjustments`) do not exist yet. `blocked` cannot be overridden in Release 1. |
| **Consistency with the engine** | The engine's own gate (`erp_engine.classification.BLOCKED`, warnings) stays authoritative. A test asserts that every `blocked` / `permitted_with_warnings` block equals the engine gate, so the two cannot drift (same pattern as the rate-series registry test, D58). |
| **Auditability** | Blocks are append-only (`ref.valuation_blocks`), pinned by version and SHA-256; each run stores the block id, version and hash it used, plus its acknowledgements and overrides. |

## 8. Permissions (F)

**Finding.** `valuation:read` and `valuation:write` are **not new**: they were defined in `auth/rbac.py` in M0 (`8b3606d`) and are already mapped — admin all, analyst read + write, reviewer read. Before Phase 5 no route checked them. `rbac.py` is unchanged. **Q2 (approved):** the existing mapping is kept and now enforced on every valuation route.

| Capability | Permission | admin | analyst | reviewer |
|---|---|---|---|---|
| List/read projects, scenarios, assumptions, runs, inputs, traces, sensitivities, blocks | `valuation:read` | ✓ | ✓ | ✓ |
| Reproduce a run (read-only re-execution) | `valuation:read` | ✓ | ✓ | ✓ |
| Input candidates, historical context | `valuation:read` | ✓ | ✓ | ✓ |
| Create project | `valuation:write` | ✓ | ✓ | — |
| Change a project, its scenarios, assumptions; preview; create runs and sensitivities | `valuation:write` + owner | ✓ (any project, reason) | own projects | — |
| Transfer ownership | `valuation:write` + admin role | ✓ | — | — |
| Lock datasets / import files (inputs to valuation) | `dataset:lock` / `data:import` | **pending P15** | **pending P15** | — |

- Ownership is a service-level check (`403 not_owner`), not a new permission.
- Runs, run inputs, assumption revisions and sensitivity results cannot be changed or deleted by anyone, administrators included (database triggers).
- A test extends the existing route walk: every valuation route declares exactly one policy.

## 9. API contracts (G)

### 9.1 Conventions (unchanged from D59/D60)

- Prefix `/api/valuation`. Cookie session; unsafe methods need `X-CSRF-Token` and an allowed `Origin` (`403 csrf_failed` / `origin_rejected`). `401 not_authenticated` without a session.
- Bodies are strict JSON (`extra="forbid"`); decimals are strings; dates ISO; ids UUID.
- GETs run on the D59 read-only session (`READ ONLY`, 10 s timeout) and return `Page{items,total,limit,offset}` for lists (`limit` ≤ 500).
- Mutations go through `workflow.perform`: one transaction for the domain change, its `audit.audit_events` row and the `audit.api_actions` record; refusals roll back and are recorded alone; success `{"action_id","action","result"}`; refusal `{"error":{"code","message","action_id","details"?}}`. `Idempotency-Key` is accepted on every mutation.
- Previews and reproduction are `POST` (they take a body) but run on a read-only session, store nothing and are not recorded as actions (like `POST /listings/validate`).
- **Error codes.** Valuation refusals use a `ValuationRefused(code, details)` domain error mapped by `perform` to `409` with its specific code (e.g. `dataset_not_locked`) instead of the generic `action_refused`, so the UI can explain them. `details` lists keys/inputs. Other statuses: `404 not_found`, `403 forbidden` / `not_owner`, `422 validation_error` / `idempotency_key_reused`, `409 revision_conflict` / `idempotency_conflict`, `500 internal_error` (no SQL, paths or tracebacks).

### 9.2 Projects

| Method and path | Permission | Request | Response | Notes |
|---|---|---|---|---|
| `GET /projects` | read | query `company_id`, `status`, `owner=me`, `limit`, `offset` | `Page[ProjectOut]` | |
| `POST /projects` | write | `{company_id, listing_id?, name, thesis?, dataset_id}` | 201 `ActionOut{ProjectOut}` | Creates the `base` scenario; audit `project_created` |
| `GET /projects/{id}` | read | — | `ProjectDetailOut` (+ scenarios, pinned dataset status/basis/version, `newer_locked_dataset`, latest runs) | |
| `POST /projects/{id}/metadata` | write + owner | `{expected_revision, name?, listing_id?, thesis?}` | `ActionOut{ProjectOut}` | before/after audited |
| `POST /projects/{id}/rebase` | write + owner | `{expected_revision, dataset_id, content_sha256, reason}` | `ActionOut{ProjectOut}` | Same company; not discarded; reason ≥ 10 chars |
| `POST /projects/{id}/archive`, `/restore` | write + owner | `{expected_revision, reason}` | `ActionOut{ProjectOut}` | |
| `POST /projects/{id}/owner` | write + admin | `{expected_revision, user_id, reason}` | `ActionOut{ProjectOut}` | Target user must hold `valuation:write` |

`ProjectOut`: `id, company_id, listing_id, name, thesis, status, owner {id, username}, dataset {id, version_no, basis, status, content_sha256}, revision_no, created_at/by, updated_at/by`.

### 9.3 Scenarios and assumptions

| Method and path | Permission | Request | Response | Phase |
|---|---|---|---|---|
| `GET /projects/{id}/scenarios` | read | — | `list[ScenarioOut]` | 5 |
| `GET /scenarios/{id}/assumptions` | read | query `resolved=true` (inheritance applied, 5b) | `AssumptionStateOut{scenario_revision, items[{key, period_index, value, unit, status, provenance_kind, source, revision_id, inherited_from?}], missing_required[]}` | 5 |
| `GET /scenarios/{id}/assumptions/history` | read | query `key`, paging | `Page[AssumptionRevisionOut]` | 5 |
| `POST /scenarios/{id}/assumptions` | write + owner | `{expected_revision, changes:[{key, period_index?, value|choice|clear, unit, provenance_kind, source?, status, note?}], reason?}` | `ActionOut{AssumptionStateOut}` | 5 |
| `POST /scenarios/{id}/assumptions/approve` | write + owner | `{expected_revision, items:[{key, period_index?}], reason?}` | `ActionOut{AssumptionStateOut}` | 5 |
| `POST /projects/{id}/scenarios` | write + owner | `{name, kind, parent_scenario_id?}` | 201 `ActionOut{ScenarioOut}` | 5b |
| `POST /scenarios/{id}/metadata`, `/archive` | write + owner | `{expected_revision, …, reason?}` | `ActionOut{ScenarioOut}` | 5b |
| `POST /projects/{id}/probabilities` | write + owner | `{expected_revision, weights:{scenario_id: "0.25"}}` | `ActionOut{ProjectOut}` | 5b |

Assumption rules:
- **Key registry in code** (`assumptions.py`), with type (`ratio`, `money`, `multiple`, `count`, `choice`, `date`), allowed range and per-period flag. Unknown keys → `422`. Examples: drivers per year; `horizon_years` (1–20); `terminal_form`, `terminal_g`, `ronic`, `exit_multiple`, `exit_metric`, `terminal_timing`; `risk_free_rate`, `erp_basis`, `mature_market_erp`, `total_country_erp`, `crp_exposures`, `beta_method`, `beta_unlevered`/`beta_levered`, `relevering`, `target_debt_to_equity`, `marginal_tax_rate`, `pre_tax_cost_of_debt`, `specific_premium` (+ rationale); `convention`, `lease_treatment`, `sbc_treatment`; FCFE `debt_path`, `cash_path`, `cash_yield`; `instruments`, `share_adjustments`, `other_claims`, `non_operating_assets`.
- **Typed sources:** `analyst` (free-text source reference required), `rate_observation` (id), `reference_value` (dataset id + record key + field), `dataset_value` (period + line item), `historical_context` (suggestions only). For sourced values the server checks that the value equals the stored source after unit conversion (`source_mismatch`).
- **Status:** `suggested` or `approved`. The system writes only `suggested` revisions; `approved` requires a user action. Changing a value creates a new revision; approval is a revision too, so who approved what, when, is history.
- One request = one scenario revision: all changes apply atomically or none does.

### 9.4 Inputs, preview and runs

| Method and path | Permission | Request | Response | Transaction |
|---|---|---|---|---|
| `GET /projects/{id}/input-candidates` | read | query `valuation_date`, `balance_sheet_period_id`, `listing_id?` | `{dataset_values[], rate_candidates[], price_candidates[], fx_candidates[], reference_candidates[]}`, all with ids, dates, units, `status: suggested` | read-only |
| `GET /projects/{id}/historical-context` | read | query `years` (≤ 15) | per-FY growth, margins, ratios from the pinned dataset, labelled context | read-only |
| `POST /projects/{id}/preview` | write + owner | `RunRequest` | `PreviewOut{preview: true, dataset_status, gates[], issues[], outputs?, validation}` | read-only; not an action |
| `POST /projects/{id}/runs` | write + owner | `RunRequest` + `{label?, acknowledgements[], check_overrides[]}` | 201 `ActionOut{RunSummaryOut}`; 200 `existing: true` on identical content | one transaction; `SELECT … FOR UPDATE` on the project |
| `GET /projects/{id}/runs` | read | query `scenario_id`, `methodology`, paging | `Page[RunSummaryOut]` | read-only |
| `GET /runs/{id}` | read | — | `RunOut` (summary, outputs, forecast table, WACC build-up, bridge, dilution, validation, warnings, versions, hashes) | read-only |
| `GET /runs/{id}/inputs` | read | — | `{input_document, lineage: Page[RunInputOut]}` | read-only |
| `GET /runs/{id}/trace` | read | query `node?`, `depth` (≤ 5) | trace nodes with inputs, rule, module + version; leaf nodes link to their `run_inputs` lineage | read-only |
| `POST /runs/{id}/reproduce` | read | — | `{matches, stored_output_sha256, recomputed_output_sha256, engine_versions, differences[]}` | read-only; not an action |

```jsonc
// RunRequest (dcf_fcff variant; dcf_fcfe adds nothing here — its keys are assumptions)
{
  "methodology": "dcf_fcff",
  "scenario_id": "…", "expected_scenario_revision": 7,
  "dataset": {"id": "…", "content_sha256": "…"},
  "balance_sheet_period_id": "…",          // an FY period of that dataset (no stub in Phase 5)
  "valuation_date": "2026-11-06",
  "listing_id": "…",
  "price_observation_id": 123,
  "fx_rate_ids": [456],                     // only when price and dataset currencies differ
  "input_overrides": [{"input": "bridge.cash", "value": "…", "source": "…", "reason": "…"}]
}
```

Run creation is **synchronous**: lock the project row, re-check every gate on committed data, resolve inputs, call the engine, insert `valuation_runs`, `run_traces`, `run_inputs`, the audit event `run_created` (after: dataset, hashes, primary outputs) and the action record, commit. Engine time is milliseconds; the transaction holds no external resource.

### 9.5 Sensitivity (Phase 5b)

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `POST /runs/{id}/sensitivities` | write + owner | `{kind: "one_way"|"two_way", x:{variable, values[]}, y?:{variable, values[]}, metric: "per_share"|"equity_value"|"enterprise_value"}` | 201 `ActionOut{SensitivityOut}` |
| `GET /runs/{id}/sensitivities` | read | paging | `Page[SensitivitySummaryOut]` |
| `GET /sensitivities/{id}` | read | — | `SensitivityOut{config, cells[{x, y, value?, refused_code?}], engine_version, hashes}` |

- Variables come from a registry (`wacc`, `terminal_g`, `exit_multiple`, `revenue_growth_shift`, `ebit_margin_shift`, `capex_pct_shift`); values are absolute or shifts, stated per variable.
- Every cell is a full engine run on the base run's `input_document` with one or two inputs replaced. Cells violating hard checks (`g ≥ WACC`) are stored as refused with the reason, never as a number (Blueprint §8.2).
- ≤ 100 cells synchronous; larger grids `422 grid_too_large` until a job queue is approved.
- Content-idempotent: an identical config on the same run returns the stored result.

### 9.6 Blocks

`GET /blocks?classification=&methodology=` (read) → the current block version per key, with policy, required assumptions and line-item map.

## 10. Database (H)

Migration `0017` (proposed): schema `val`, one `ref` table, triggers. All amounts `NUMERIC` without scale; ids UUID except append-only identity rows.

| Table | Kind | Key columns | Constraints and indexes |
|---|---|---|---|
| `val.valuation_projects` | **Mutable**, audited; DELETE refused | `id, company_id → market.companies, listing_id → market.securities, name, thesis, status, owner_user_id → auth.users, dataset_id → fin.financial_datasets, revision_no, created_by/at, updated_by/at, archived_at, status_reason` | Trigger: dataset and listing belong to `company_id`; `company_id` immutable. Partial unique `(company_id, lower(name)) WHERE status='active'`. Index `(company_id, status)`, `(owner_user_id)` |
| `val.scenarios` | **Mutable** header; DELETE refused | `id, project_id, name, kind (base/bull/bear/custom), parent_scenario_id, probability, sort_order, revision_no, status, created/updated` | Unique `(project_id, name)`; partial unique one `base` per project; parent in the same project; cycle check in the service (5b); probability 0–1 |
| `val.assumption_revisions` | **Append-only** | `id identity, scenario_id, scenario_revision_no, key, period_index, value NUMERIC, value_json JSONB (choices, lists), unit, provenance_kind, source JSONB, status (suggested/approved/cleared), note, reason, created_by/at` | Exactly one of `value`/`value_json` unless `cleared`; unique `(scenario_id, key, coalesce(period_index,-1), scenario_revision_no)`; index `(scenario_id, key, period_index, id DESC)` for current state |
| `val.valuation_runs` | **Immutable** | `id, project_id, scenario_id, scenario_revision_no, company_id, listing_id, methodology, method_version, produces, dataset_id, dataset_version_no, dataset_basis, dataset_content_sha256, block_id, valuation_date, balance_sheet_date, currency, input_document JSONB, input_sha256, engine_input_sha256, engine_version, module_versions JSONB, outputs JSONB, primary_value NUMERIC, per_share_value NUMERIC, validation JSONB, warnings JSONB, output_sha256, label, action_id, request_id, created_by/at` | FK RESTRICT everywhere; unique `(project_id, input_sha256, engine_version)` (content idempotency); index `(project_id, created_at DESC)`, `(dataset_id)`, `(company_id, valuation_date)` |
| `val.run_traces` | **Immutable** | `run_id PK → valuation_runs, trace JSONB, trace_sha256, node_count` | Separate so run listings stay light |
| `val.run_inputs` | **Immutable** lineage index | `run_id, input_key, value NUMERIC, unit, converted_value NUMERIC, conversion, provenance_kind, source_type (dataset_value/rate_observation/fx_rate/price_observation/reference_value/assumption_revision/analyst_override), source_ref TEXT, source_sha256, as_of_date, warnings JSONB` | PK `(run_id, input_key)`; index `(source_type, source_ref)` ("which runs used this ERP edition / observation"). A test asserts it equals the `input_document` |
| `val.sensitivity_runs` (5b) | **Immutable** | `id, run_id, config JSONB, config_sha256, cells JSONB, result_sha256, engine_version, action_id, created_by/at` | Unique `(run_id, config_sha256, engine_version)` |
| `ref.valuation_blocks` | **Append-only**, loaded by migration | `id, block_version, classification, methodology, policy, definition JSONB, definition_sha256` | Unique `(block_version, classification, methodology)` |

- **Immutability:** `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` triggers on every immutable/append-only table refuse the statement for every role, owner included (D31, D50). Tests use the rolled-back `db` fixture and never clean up with DELETE/TRUNCATE.
- **Grants:** `erp_app` gets SELECT/INSERT on immutable tables and SELECT/INSERT/UPDATE on the two mutable ones; no DELETE anywhere in `val`.
- **Mutable vs immutable:** projects and scenario headers are configuration (current state + audit before/after). Everything that a result depends on is either immutable (assumption revisions, runs) or snapshotted into the run.
- **Audit events** (`audit.audit_events`, same transaction): entity types `valuation_project`, `valuation_scenario`, `valuation_assumption`, `valuation_run`, `valuation_sensitivity`; actions `project_created/updated/rebased/archived/restored/owner_changed`, `scenario_created/updated/archived`, `assumptions_revised` (before/after per key), `assumptions_approved`, `probabilities_set`, `run_created`, `sensitivity_created`. Administrator actions on another user's project carry `admin_override: true` and the reason. `valuation_impact` (Blueprint §10.4: price before/after an assumption change) is deferred to 5b and stored as `null` until then.

## 11. Validation and audit rules (summary)

1. A run uses exactly one locked dataset, named and hash-confirmed by the request; no substitution, ever.
2. Every market or reference input is a named row; the server never resolves "latest" while creating a run.
3. No `suggested` value in a run; approval is an explicit, attributed revision.
4. Missing is never zero; no defaults, interpolation or carry-forward.
5. Look-ahead inputs are errors; stale inputs are recorded warnings; superseded revisions are refused.
6. Percentage points become fractions only at the input boundary, recorded; FX only in a permitted direction; no cross rates.
7. Classification `needs_review` and blocked classes refuse runs; warning classes need acknowledgement with reason.
8. Failed error-severity dataset checks on used periods need an override with reason (Q12).
9. Runs, traces, run inputs, assumption revisions and sensitivity results are immutable for every role.
10. Every mutation writes its audit event and action record in the same transaction; refusals are recorded alone.
11. Reproduction with the same engine version must match the stored output hash.

## 12. Dependencies and approval gates

**What blocks what:**

| Item | Blocks implementation? | What it blocks |
|---|---|---|
| **P16 — approval of this design** (incl. Q1–Q12) | **Resolved 2026-11-07** (D62) | — |
| P15 — `dataset:lock` / `data:import` role assignment | No (backend work and tests use `erp_test`) | The **in-app** path to a lockable dataset; until approved, a dataset can be locked only by the owner via the CLI. Phase 5 acceptance on real data needs at least one locked dataset |
| P15 implementation + raw-archive mount on the API container | No | In-app file ingestion (TSX/imported companies, Damodaran tables, price CSVs) |
| P9 — first live provider fetch | No | Stored Rf/FX/prices from providers; until then Rf is an analyst value with a written source and prices/FX come from file imports |
| P13 — provider gate for file imports | No | Same as today: file imports run with the provider disabled |
| D28 frontend libraries (unapproved) | No | Charts and editable grids; Phase 5 UI uses the D61 approach (tables, plain forms) |

**Approved decisions (owner, 2026-11-07; binding):**

| # | Decision |
|---|---|
| Q1 | Design approved: `val` schema, migration `0017`, §9 endpoints, immutable runs |
| Q2 | Keep the M0 mapping: admin and analyst read/write, reviewer read-only; enforced on every route |
| Q3 | Owner controls edits; an administrator override requires a recorded reason |
| Q4 | Draft datasets for previews; saved runs need a locked dataset |
| Q5 | Exact reciprocal FX at the input boundary, original direction and provenance preserved; never cross rates or change stored observations |
| Q6 | Staleness: 7 calendar days for prices, risk-free rates and FX; 13 months for Damodaran editions; look-ahead is an error |
| Q7 | Traces in PostgreSQL JSONB (`val.run_traces`) |
| Q8 | Identical canonical inputs and engine version return the existing run |
| Q9 | Phase 5: base scenario, FCFF/FCFE, constant WACC, analyst or bottom-up beta, no stub. Phase 5b: scenarios, probabilities, sensitivities, assumption impact, regression beta |
| Q10 | Dilutive instruments are scenario-level assumptions; the company-level register is deferred |
| Q11 | REIT and financial-institution (asset manager/broker) warnings need an acknowledgement with reason |
| Q12 | Error-severity check failures for the periods used refuse a run unless overridden with a reason |

Qualifications: (1) the reciprocal calculation is a trace node and no derived rate is written to `market.fx_rates`; (2) the identity includes every output-determining input, the project context, the engine version and configuration, and runs are never shared across projects; (3) overrides and acknowledgements record user, time, reason and run; (4) reproduction uses only the immutable stored inputs and the recorded engine version.

## 13. Phase 5 implementation sequence (completed in Session 14; step 8 deferred)

Each stage: tests first-class, `make check`, commit by explicit path.

1. **Schema:** migration `0017` (`val` schema, `ref.valuation_blocks`, triggers, grants), ORM models, block YAML v1; tests for every trigger, grant and the block ↔ engine-gate equality.
2. **Projects and assumptions:** services for projects (create with base scenario, metadata, rebase, archive/restore, owner), assumption registry, revisions, approval; audit events; ownership checks.
3. **Input resolver:** line-item mapping via blocks, rate/FX/price/reference resolution with unit and currency rules, staleness/look-ahead/superseded rules, canonical input document + hash; tests including the methodology benchmarks (B1/B3) reproduced through the resolver.
4. **Runs:** methodology adapters (`dcf_fcff`, `dcf_fcfe`, WACC wiring per D37), gates, persistence, content idempotency, reproduction; tests for every refusal code.
5. **API:** routes in §9.2–§9.4 and §9.6, `ValuationRefused` mapping in `perform`, route-policy test, OpenAPI, `make api-types`.
6. **UI (D61 rules):** project list/create, assumption panel with historical context and approval, input selection, preview, run creation, DCF output, trace inspector; vitest with mocked `fetch`.
7. **Docs and handoff:** decision record replacing P16, `docs/operations/valuation-api.md`, KNOWN_LIMITATIONS, integrated check on the dev stack.
8. **Phase 5b:** scenarios with inheritance and probabilities, sensitivity grids, assumption impact; then R4 methodologies through the same registry.

**Phase 5 acceptance (from the handoff, made concrete):** endpoints with D60 conventions, migration and tests; runs refused for unlocked datasets, `needs_review` and blocked classifications, unapproved assumptions and look-ahead inputs; a stored run reproduces to an identical output hash; UI project → assumptions → DCF output → trace with vitest; `make check` green; docs and handoff updated.
