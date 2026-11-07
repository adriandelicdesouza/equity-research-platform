# Valuation API and UI (M3 phase 5)

Decision D62 (P16 resolved). Design and approved decisions: `docs/architecture/valuation-api.md`. Conventions shared with the other APIs: `data-api.md` (D59) and `workflow-api.md` (D60); UI conventions: `workflow-ui.md` (D61).

A valuation **project** pins one dataset version of one company. Its **base scenario** holds append-only **assumption revisions**. A **run** is one immutable FCFF or FCFE execution with its fully materialized input document, outputs, validation, trace, lineage and decisions. Previews compute the same thing without storing it.

## Every request

- **Session:** cookie login (`POST /api/auth/login`). Without one: `401 not_authenticated`.
- **Permissions (Q2, unchanged M0 mapping):**

  | | admin | analyst | reviewer |
  |---|---|---|---|
  | `valuation:read` — every GET, and `POST …/reproduce` | ✓ | ✓ | ✓ |
  | `valuation:write` — every other POST | ✓ | ✓ | — |

  Missing permission: `403 forbidden`. `dataset:lock` and `data:import` remain pending (P15) and are not used here.
- **Ownership (Q3):** changing a project, its assumptions, previewing and creating runs need the **owner**. An administrator may act on another user's project only with `admin_reason` (≥ 10 characters, with words); the reason is stored in the audit event and, for runs, as an `admin_override` run decision. Otherwise: `403 not_owner` or `403 admin_reason_required`. Ownership transfer is administrator-only.
- **CSRF and Origin:** every POST needs `X-CSRF-Token` and an allowed `Origin` (`403 csrf_failed`, `403 origin_rejected`).
- **Bodies:** strict JSON (unknown fields → `422 validation_error`). Decimals are strings. **Ratios are fractions** (`"0.05"` = 5 %); money is in the dataset currency in full units.
- **Writes** run through the D60 action runner: the change, its `audit.audit_events` row and an `audit.api_actions` record commit together; refusals are rolled back and recorded alone (`action_id` in the error). `Idempotency-Key` replays the first response.
- **Previews and reproduction** run on the read-only session (`READ ONLY` transaction): nothing is stored and they are not actions.
- **Refusals** keep a specific code: `409 <code>` with `details` (the affected inputs, keys, checks or acknowledgements). Envelope: `{"error": {"code", "message", "details"?, "action_id"?}}`.

## Endpoints (`/api/valuation`)

| Method and path | Permission | Purpose |
|---|---|---|
| `GET /assumption-keys` | read | Key registry: kind, unit, range, allowed sources, methodologies |
| `GET /blocks` | read | Classification blocks v1 (`?classification=&methodology=`) |
| `GET /projects` | read | `?company_id=&status=active\|archived&owner=me&limit=&offset=` |
| `POST /projects` | write | `{company_id, name, dataset_id, listing_id?, thesis?}` → 201; creates the `base` scenario |
| `GET /projects/{id}` | read | Detail: scenarios, pinned dataset, `newer_locked_dataset`, effective classification, 5 latest runs |
| `POST /projects/{id}/metadata` | write + owner | `{expected_revision, name?, thesis?, listing_id?, clear_thesis?, clear_listing?, admin_reason?}` |
| `POST /projects/{id}/rebase` | write + owner | `{expected_revision, dataset_id, content_sha256, reason, admin_reason?}`: same company, not discarded, hash confirmed |
| `POST /projects/{id}/archive`, `/restore` | write + owner | `{expected_revision, reason, admin_reason?}`; archived projects are read-only |
| `POST /projects/{id}/owner` | write + admin | `{expected_revision, user_id, reason}`; the new owner must hold `valuation:write` |
| `GET /projects/{id}/scenarios` | read | Phase 5: the base scenario |
| `GET /scenarios/{id}/assumptions` | read | Current value per key with `missing_required` and `unapproved` for `?methodology=` |
| `GET /scenarios/{id}/assumptions/history` | read | Every revision, newest first (`?key=`) |
| `POST /scenarios/{id}/assumptions` | write + owner | `{expected_revision, changes[], reason?, admin_reason?}`: one atomic scenario revision |
| `POST /scenarios/{id}/assumptions/approve` | write + owner | `{expected_revision, items[{key, period_index}]}`: approve suggested values unchanged |
| `GET /projects/{id}/input-candidates` | read | `?valuation_date=&listing_id=`: fiscal years, rates, prices, FX, reference datasets — suggestions with row ids |
| `GET /projects/{id}/historical-context` | read | Per-FY growth, margins and ratios of the pinned dataset (context only) |
| `POST /projects/{id}/preview` | write + owner | Run request → issues, outputs, validation, trace; stores nothing |
| `POST /projects/{id}/runs` | write + owner | Run request + `label?` → 201 new run, or 200 `existing: true` (identical inputs) |
| `GET /projects/{id}/runs` | read | Run summaries |
| `GET /runs/{id}` | read | Outputs, validation, warnings, decisions, versions, hashes |
| `GET /runs/{id}/inputs` | read | Stored input document + lineage rows |
| `GET /runs/{id}/trace` | read | All nodes, or `?node=&depth=` (≤ 5) |
| `POST /runs/{id}/reproduce` | read | Re-execute from the stored document; compare output hashes |

### Assumption changes

```json
{"expected_revision": 3, "changes": [
  {"key": "revenue_growth", "period_index": 1, "value": "0.08",
   "source": {"type": "analyst", "reference": "Guidance, Q4 call"}},
  {"key": "risk_free_rate", "value": "0.0425",
   "source": {"type": "rate_observation", "observation_id": 12}},
  {"key": "mature_market_erp", "value": "0.046", "status": "suggested",
   "source": {"type": "reference_value", "reference_dataset_id": "…", "record_key": "…", "field": "implied_erp"}},
  {"key": "convention", "choice": "mid_period"},
  {"key": "instruments", "json": [{"id": "opt-2024", "kind": "option", "count": "1200000", "treatment": "tsm", "strike": "45"}]},
  {"key": "ronic", "clear": true}]}
```

- Per-year keys need `period_index` 1–20; others must not have one.
- A sourced value must equal its source after conversion (`409 source_mismatch`): rate observations are percentage points and are divided by 100 exactly; reference values convert by their stored unit (`percent` → fraction).
- Market inputs entered by an analyst (`risk_free_rate`) need `as_of` and `currency`. Cost-of-capital inputs (`risk_free_rate`, ERP, CRP, betas, cost of debt, spread) need a source.
- `status` is `approved` (default) or `suggested`. Suggested values can be previewed but never enter a saved run.

### Run request (preview and runs)

```json
{"methodology": "dcf_fcff", "scenario_id": "…", "expected_scenario_revision": 4,
 "dataset": {"id": "…", "content_sha256": "…"},
 "balance_sheet_period_id": "…", "valuation_date": "2026-11-06",
 "listing_id": null, "price_observation_id": 123, "fx_rate_ids": [456],
 "input_overrides": [{"input": "operating.nwc_0", "line_item": "BS.INVENTORY",
                      "kind": "not_applicable", "reason": "Service company: no inventory."}],
 "acknowledgements": [{"code": "reit_fair_value_gains", "reason": "Fair-value gains removed from EBIT."}],
 "check_overrides": [{"check_code": "BALANCE", "subject": "BS", "reason": "Rounding in the filing."}],
 "admin_reason": null}
```

## Data requirements and rules

- **Dataset:** exactly the project's pinned version, hash-confirmed. A draft may be previewed (Q4); a saved run needs it **locked** (`409 dataset_not_locked`; also enforced by a database trigger). The balance-sheet date is a fiscal year-end of that dataset (no stub periods).
- **Line items at B** come from the classification block (`mappings/valuation-blocks/v1.yaml`): revenue; operating NWC = receivables + inventory − payables; cash; short-term investments; debt = short-term + long-term; preferred; NCI; basic shares (`BS.SHARES_OUTSTANDING`, never weighted-average counts). FCFE uses opening debt and cash instead of the bridge. A missing or ambiguous component is **never zero**: the run needs an `input_overrides` entry (`value` with a source, or `not_applicable`), each with a reason.
- **Leases:** `none`, `ifrs16_as_debt` (lease liabilities deducted) or `asc842_as_operating` (finance-lease liabilities deducted). Combinations that need per-year lease forecasts are refused (`lease_treatment_not_supported`). FCFE supports `none` only.
- **Cost of capital:** constant WACC; beta analyst-entered or bottom-up (unlevered beta relevered with Hamada or Harris–Pringle); cost of debt direct or Rf + spread; weights target (D/E) or market (price × basic shares vs book debt). Regression beta: Phase 5b.
- **Dates (Q6):** an observation or edition dated after the valuation date is an error (`input_lookahead`); this includes the `as_of` of any analyst source (e.g. an analyst-entered ERP, beta or cost of debt). Prices, risk-free rates and FX older than 7 calendar days, and Damodaran editions older than 13 months, are warnings (`input_stale`) stored on the run.
- **Revisions:** a rate, price or FX row with a newer revision for the same date, or a reference dataset superseded by a newer version, is refused (`input_superseded`).
- **Currency:** the risk-free rate must be in the dataset currency. A price in another currency needs one stored FX row; the exact reciprocal is allowed at this boundary (Q5) and recorded as a trace node (`market.fx_reciprocal`); stored FX rows are never changed and no derived rate is written. Cross rates are not computed.
- **Classification (Q11):** `needs_review` → `classification_unconfirmed`; bank, insurer, specialty lender, investment holding → `classification_blocked`; REIT and asset manager/broker need an acknowledgement with reason (`acknowledgement_required`).
- **Dataset checks (Q12):** failed error-severity checks for the balance-sheet period refuse the run (`checks_failed`) unless each is overridden with a reason.

## Scenarios, probabilities and sensitivities (Phase 5b, D63)

| Method and path | Permission | Purpose |
|---|---|---|
| `POST /projects/{id}/scenarios` | write + owner | `{name, kind: bull\|bear\|custom, parent_scenario_id?}` → 201 scenario (revision 0) |
| `POST /scenarios/{id}/metadata` | write + owner | `{expected_revision, name?, sort_order?, parent_scenario_id?, clear_parent?, reason?}`; bumps the scenario revision |
| `POST /scenarios/{id}/archive`, `/restore` | write + owner | `{expected_revision, reason}`; not the base; no active children or probability |
| `POST /projects/{id}/probabilities` | write + owner | `{expected_revision (project), weights: {scenario_id: "0.25"}}`; exact sum 1; others cleared; `{}` clears all |
| `GET /projects/{id}/scenario-summary` | read | `?methodology=`: latest run per active scenario; weighted per-share/primary value when complete, else `issues` |
| `GET /scenarios/{id}/assumptions` | read | `?resolved=true` (default) applies inheritance, items carry `inherited_from`; `false` shows own rows |
| `GET /sensitivity-variables` | read | Variable registry: range, absolute or shift, methodologies |
| `POST /runs/{id}/sensitivities` | write + owner | `{kind: one_way\|two_way\|tornado, metric: per_share\|equity_value\|enterprise_value, x?, y?, tornado?, label?}` → 201 stored grid, or 200 `existing: true` for an identical configuration |
| `GET /runs/{id}/sensitivities`, `GET /sensitivities/{id}` | read | Summaries; one grid with `config`, `base` (the run's value and variable values; tornado `ranking`) and `cells` (`value` or `refused_code` + `message`) |

```json
{"kind": "two_way", "metric": "per_share", "label": "WACC × g",
 "x": {"variable": "wacc", "values": ["0.0525", "0.055", "0.0575"]},
 "y": {"variable": "terminal_g", "values": ["0.015", "0.02", "0.025"]}}
```

- **Inheritance:** a child uses its parent's values unless it sets the key or clears it (clearing hides the inherited value). Revise and approve act on the scenario's own rows only. Runs of an inheriting scenario record `inherited_from` per inherited input and `scenario_lineage`; base runs are unchanged.
- **Sensitivity cells** re-execute the run's stored input document with the named inputs replaced (values are decimal strings, up to 30 decimals; ratios are fractions; shifts are added to every forecast year). Everything else keeps the run's value — including RONIC when WACC varies. Cells the engine refuses (e.g. `g ≥ WACC`) are stored as refused with the code. ≤ 100 cells; the result is immutable and content-idempotent.

## Refusal codes (409)

`dataset_mismatch`, `dataset_hash_mismatch`, `dataset_discarded`, `dataset_not_locked`, `invalid_period`, `invalid_dates`, `classification_unconfirmed`, `classification_blocked`, `acknowledgement_required`, `invalid_acknowledgement`, `checks_failed`, `invalid_check_override`, `input_missing`, `input_ambiguous`, `unit_mismatch`, `invalid_override`, `reason_required`, `assumption_not_approved`, `input_lookahead`, `input_superseded`, `currency_mismatch`, `listing_mismatch`, `invalid_input`, `lease_treatment_not_supported`, `unsupported_capital_structure`, `engine_refused` (with `engine_code`), `validation_blocked`, `revision_conflict`, `project_archived`, `duplicate_name`, `status_unchanged`, `dataset_unchanged`, `invalid_owner`, `owner_unchanged`, `source_required`, `source_invalid`, `source_not_found`, `source_mismatch`, `source_unit_unsupported`, `unknown_key`, `invalid_value`, `out_of_range`, `invalid_status`, `duplicate_change`, `nothing_to_clear`, `not_suggested`, `invalid_request`.

Phase 5b (D63): `scenario_archived`, `scenario_cycle`, `scenario_has_children`, `scenario_weighted`, `invalid_parent`, `parent_unchanged`, `invalid_kind`, `invalid_name`, `too_many_scenarios`, `base_scenario`, `invalid_weights`, `weights_not_one`, `unknown_variable`, `variable_not_applicable`, `grid_too_large`, `engine_version_mismatch`.

## Runs, identity and reproduction

- **Stored per run:** the canonical input document (every input with value, unit, provenance, source reference and hash, as-of date, conversion, warnings, components), outputs (typed EV/equity/per share, terminal value, flows, cost-of-capital build-up, market comparison), engine validation, warnings, the trace (`val.run_traces`, JSONB, Q7), lineage rows (`val.run_inputs`), decisions (`val.run_decisions`: acknowledgements, check overrides, input overrides, administrator override — user, time, reason, run), engine and module versions, and SHA-256 of inputs, engine inputs, outputs and trace. All immutable for every role.
- **Content idempotency (Q8):** the identity is the SHA-256 of the input document, which includes project, scenario and its revision, methodology and method version, dataset identity, all inputs and settings, decisions and the engine version. An identical document **in the same project** returns the existing run; other projects never share runs.
- **Reproduction:** `POST /runs/{id}/reproduce` re-computes from the stored document only — no current market data, rates or reference versions are read — and compares output hashes. With the same engine version it must match; a mismatch is a defect. After an engine upgrade it lists the differences.

## UI

Valuations (main navigation) and a company's **Valuation** tab: project list and creation; project overview (edit, rebase, archive/restore); **Assumptions** (per-year driver grid, settings and cost-of-capital inputs with typed sources, approval, history, historical context); **Inputs, preview & run** (fiscal year, valuation date, price and FX rows, candidates with ids, server preview with errors/gates/warnings and outputs, reasoned decisions offered from the preview); **Saved runs** (outputs, WACC build-up, warnings, decisions, reproduction, inputs with provenance, trace inspector, **Sensitivity**: new grid, saved grids, grid with refused cells and the run's own cell, tornado); **Scenarios** (list with parents and probabilities, add, edit/re-parent, archive/restore, probabilities, weighted value). A `?scenario=` picker selects the scenario in Assumptions and Inputs/preview/run; inherited values are marked. The UI never calculates: every number comes from the server; decimals are formatted by string arithmetic.

## Tests

`backend/tests/test_valuation_{scenarios,sensitivity}.py` (Phase 5b: inheritance, cycles, archive rules, probabilities, weighted summary, grids, refused cells, tornado ranking, idempotency, limits, immutability) and `backend/tests/test_valuation_{schema,projects,runs,api}.py` (66 tests: triggers, grants, block ↔ engine-gate equality, B1, B1-VD and B3 reproduced through the resolver, every gate, idempotency, reproduction, FX reciprocal, ownership, HTTP workflow) and `frontend/src/features/valuation/Valuation.test.tsx` (mocked `fetch`).
