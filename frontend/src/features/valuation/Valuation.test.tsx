import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../../api/client";
import type { CompanyDetailOut } from "../../api/schema.gen";
import { describeDetail, ErrorMessage } from "../../components/ui";
import { parseHash } from "../../lib/router";
import { apiError, company, COMPANY_ID, DS_LOCKED, makeMe, mockFetch, page, renderWithSession } from "../../test/helpers";
import { toChange } from "./AssumptionsPanel";
import { ProjectPage } from "./ProjectPage";
import { ProjectsPage } from "./ProjectsPage";

// ------------------------------------------------------------------ synthetic fixtures (no real data)

const ME = makeMe("analyst");
const PID = "77777777-7777-4777-8777-777777777777";
const SID = "88888888-8888-4888-8888-888888888888";
const RID = "99999999-9999-4999-8999-999999999999";
const PERIOD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SHA = "d".repeat(64);
const C = company() as unknown as CompanyDetailOut;

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: PID,
    company_id: COMPANY_ID,
    company_name: "Example Industries",
    listing_id: "66666666-6666-4666-8666-666666666666",
    name: "Core thesis",
    thesis: null,
    status: "active",
    status_reason: null,
    owner: { id: ME.user.id, username: ME.user.username },
    dataset: { id: DS_LOCKED, version_no: 1, basis: "standardized", status: "locked", currency: "USD", content_sha256: SHA },
    revision_no: 1,
    created_at: "2026-11-07T10:00:00Z",
    created_by: { id: ME.user.id, username: ME.user.username },
    updated_at: null,
    scenarios: [{ id: SID, project_id: PID, name: "Base", kind: "base", parent_scenario_id: null, revision_no: 1, status: "active" }],
    newer_locked_dataset: null,
    classification: "non_financial",
    recent_runs: [],
    ...overrides,
  };
}

const KEYS = [
  { key: "revenue_growth", kind: "ratio", label: "Revenue growth", unit: "ratio", per_period: true, choices: [], low: "-1", high: "5", sources: ["analyst"], source_required: false, market: false, methodologies: ["dcf_fcff", "dcf_fcfe"] },
  { key: "horizon_years", kind: "count", label: "Forecast horizon (years)", unit: "count", per_period: false, choices: [], low: "1", high: "20", sources: ["analyst"], source_required: false, market: false, methodologies: ["dcf_fcff", "dcf_fcfe"] },
  { key: "terminal_growth", kind: "ratio", label: "Terminal growth g", unit: "ratio", per_period: false, choices: [], low: "-0.1", high: "0.2", sources: ["analyst"], source_required: false, market: false, methodologies: ["dcf_fcff", "dcf_fcfe"] },
  { key: "risk_free_rate", kind: "ratio", label: "Risk-free rate", unit: "ratio", per_period: false, choices: [], low: "-0.05", high: "0.3", sources: ["analyst", "rate_observation"], source_required: true, market: true, methodologies: ["dcf_fcff", "dcf_fcfe"] },
  { key: "convention", kind: "choice", label: "Discounting convention", unit: "choice", per_period: false, choices: ["end_of_period", "mid_period"], low: null, high: null, sources: ["analyst"], source_required: false, market: false, methodologies: ["dcf_fcff", "dcf_fcfe"] },
];

function item(key: string, value: string | null, extra: Record<string, unknown> = {}) {
  return {
    key, period_index: null, value, json: null, unit: "ratio", status: "approved",
    provenance_kind: "analyst_assumption", source: null, note: null, revision_id: 1,
    scenario_revision_no: 1, created_by: ME.user.id, created_at: "2026-11-07T10:00:00Z", ...extra,
  };
}

const STATE = {
  scenario_id: SID,
  scenario_revision: 1,
  methodology: "dcf_fcff",
  items: [
    item("horizon_years", "2", { unit: "count" }),
    item("revenue_growth", "0.1", { period_index: 1 }),
    item("terminal_growth", "0.02", { status: "suggested" }),
  ],
  missing_required: [{ key: "ebit_margin", period_index: 1 }],
  unapproved: [{ key: "terminal_growth", period_index: null }],
};

const OUTPUTS = {
  methodology: "dcf_fcff",
  produces: "enterprise_value",
  currency: "USD",
  primary: { kind: "enterprise_value", value: "1858.818181818181818181818181818182", date: "2024-12-31" },
  equity_value_b: "1608.818181818181818181818181818182",
  equity_value_v: "1608.818181818181818181818181818182",
  per_share: "16.08818181818181818181818181818182",
  diluted_shares: "100",
  sum_pv_flows: "358.9123966942148760330578512396694",
  pv_terminal: "1499.905785123966942148760330578512",
  terminal_share: "0.806914",
  terminal: { value: "1996.3746", time: "3", implied_exit_multiple: "6.341321", implied_growth: null, implied_ronic: null, terminal_flow: "159.709968" },
  flows: [{ year: 1, flow: "133", time: "1", discount_factor: "0.9090909090909090909090909090909091", present_value: "120.9090909090909090909090909090909" }],
  cost_of_capital: { risk_free_rate: "0.04", beta_levered: "1.2", erp_used: "0.05", crp_term: "0", cost_of_equity: "0.1", pre_tax_cost_of_debt: "0.06", marginal_tax_rate: "0.25", checks: [], wacc: "0.1", weight_equity: "1", weight_debt: "0", after_tax_cost_of_debt: "0.045", capital_structure_basis: "target" },
  market: null,
  engine: { version: "0.1.0" },
};

const CANDIDATES = {
  valuation_date: "2024-12-31",
  currency: "USD",
  dataset: project().dataset,
  periods: [{ id: PERIOD, fiscal_year: 2024, start_date: "2024-01-01", end_date: "2024-12-31", value_count: 40 }],
  rates: [],
  prices: [],
  fx: [],
  reference: [],
  notes: ["No stored risk-free observation in USD on or before 2024-12-31: enter the rate as an analyst assumption with a written source."],
};

const RUN = {
  id: RID, project_id: PID, scenario_id: SID, scenario_revision_no: 1, methodology: "dcf_fcff",
  produces: "enterprise_value", dataset_id: DS_LOCKED, dataset_version_no: 1, dataset_basis: "standardized",
  classification: "reit", valuation_date: "2024-12-31", balance_sheet_date: "2024-12-31", currency: "USD",
  primary_value: OUTPUTS.primary.value, per_share_value: OUTPUTS.per_share, label: "B1 check",
  warnings: [{ level: "warning", code: "input_stale", message: "risk_free_rate is stale." }],
  input_sha256: "a".repeat(64), output_sha256: "b".repeat(64), engine_version: "0.1.0",
  created_by: { id: ME.user.id, username: ME.user.username }, created_at: "2026-11-07T11:00:00Z",
};

const RUN_DETAIL = {
  ...RUN,
  method_version: "1.0.0",
  block_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  dataset_content_sha256: SHA,
  engine_input_sha256: "c".repeat(64),
  module_versions: { forecast: "1.0.0" },
  outputs: OUTPUTS,
  validation: [{ code: "reit_fair_value_gains", severity: "warning", message: "REIT: confirm fair-value gains are excluded." }],
  decisions: [{ kind: "classification_acknowledgement", code: "reit_fair_value_gains", reason: "Fair-value gains removed from EBIT.", detail: {}, user: { id: ME.user.id, username: ME.user.username }, created_at: "2026-11-07T11:00:00Z" }],
  trace_sha256: "e".repeat(64),
  trace_node_count: 2,
};

const TRACE = {
  run_id: RID,
  trace_sha256: "e".repeat(64),
  root: null,
  truncated: false,
  nodes: {
    "discounting.ev": { label: "Present value at B", value: "1858.8", unit: "currency", rule: "Σ flow × DF(t) + PV(TV)", inputs: ["discounting.pv_tv"], module: "discounting" },
    "discounting.pv_tv": { label: "PV of terminal value", value: "1499.9", unit: "currency", rule: "TV × DF(t_TV)", inputs: [], module: "discounting" },
  },
};

const P = `/api/valuation/projects/${PID}`;

function routeTo(hash: string) {
  return parseHash(hash);
}

// ------------------------------------------------------------------ tests

describe("Valuation projects", () => {
  it("lists projects and creates one pinned to a dataset with CSRF and an idempotency key", async () => {
    const { calls } = mockFetch({
      "GET /api/valuation/projects": page([project()]),
      [`GET /api/companies/${COMPANY_ID}/datasets`]: page([
        { id: DS_LOCKED, version_no: 1, basis: "standardized", status: "locked", currency: "USD" },
        { id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", version_no: 2, basis: "standardized", status: "discarded", currency: "USD" },
      ]),
      "POST /api/valuation/projects": () => ({ status: 201, body: { action_id: "act-1", action: "valuation.project_create", result: project() } }),
    });
    const user = userEvent.setup();
    renderWithSession(<ProjectsPage company={C} route={routeTo(`#/companies/${COMPANY_ID}/valuation`)} />, ME);
    expect(await screen.findByRole("link", { name: "Core thesis" })).toHaveAttribute("href", `#/valuations/${PID}`);
    await user.click(screen.getByRole("button", { name: "New valuation project" }));
    const form = screen.getByRole("form", { name: "New valuation project" });
    await user.type(within(form).getByLabelText("Name"), "New thesis");
    const select = within(form).getByLabelText(/Dataset version/);
    expect(within(select).queryByText(/discarded/)).toBeNull();
    await user.selectOptions(select, DS_LOCKED);
    await user.click(within(form).getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/valuations/${PID}`));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toMatchObject({ company_id: COMPANY_ID, name: "New thesis", dataset_id: DS_LOCKED });
    expect(post?.headers["x-csrf-token"]).toBe("csrf-test-token");
    expect(post?.headers["idempotency-key"]).toMatch(/^ui-/);
  });

  it("explains why a reviewer cannot create projects", async () => {
    mockFetch({ "GET /api/valuation/projects": page([]) });
    renderWithSession(<ProjectsPage company={C} route={routeTo(`#/companies/${COMPANY_ID}/valuation`)} />, makeMe("reviewer"));
    expect(await screen.findByText(/No valuation projects for this company/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New valuation project" })).toBeDisabled();
    expect(screen.getByText(/does not include the valuation:write permission/)).toBeInTheDocument();
  });
});

describe("Assumptions", () => {
  it("shows missing and suggested values, saves edits as one revision and approves suggestions", async () => {
    const { calls } = mockFetch({
      [`GET ${P}`]: project(),
      "GET /api/valuation/assumption-keys": KEYS,
      [`GET /api/valuation/scenarios/${SID}/assumptions`]: STATE,
      [`GET ${P}/historical-context`]: { dataset: project().dataset, years: [], note: "" },
      [`POST /api/valuation/scenarios/${SID}/assumptions`]: { action_id: "a", action: "x", result: { ...STATE, scenario_revision: 2 } },
      [`POST /api/valuation/scenarios/${SID}/assumptions/approve`]: { action_id: "b", action: "y", result: STATE },
    });
    const user = userEvent.setup();
    renderWithSession(<ProjectPage projectId={PID} tab="assumptions" rest={[]} route={routeTo(`#/valuations/${PID}/assumptions`)} />, ME);
    expect(await screen.findByText(/Missing for dcf_fcff: ebit_margin\[1\]/)).toBeInTheDocument();
    const cell = screen.getByLabelText("Revenue growth year 1");
    expect(cell).toHaveValue("0.1");
    expect(screen.getByText("10.00%")).toBeInTheDocument(); // percent hint by string arithmetic
    await user.clear(cell);
    await user.type(cell, "0.12");
    await user.type(screen.getByLabelText("Source for changed drivers (analyst reference)"), "Guidance FY25");
    await user.click(screen.getByRole("button", { name: /Save 1 change/ }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/assumptions") && c.method === "POST")).toBe(true));
    const revise = calls.find((c) => c.path.endsWith("/assumptions") && c.method === "POST");
    expect(revise?.body).toEqual({
      expected_revision: 1,
      changes: [{ key: "revenue_growth", period_index: 1, status: "approved", value: "0.12", source: { type: "analyst", reference: "Guidance FY25" } }],
    });
    await user.click(screen.getByRole("button", { name: "Approve all suggested values" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({
      expected_revision: 1,
      items: [{ key: "terminal_growth", period_index: null }],
    });
  });

  it("validates drafts before sending", () => {
    const rf = KEYS[3] as Parameters<typeof toChange>[0];
    expect(toChange(rf, null, { value: "4%" }, "")).toMatch(/plain decimal/);
    expect(toChange(rf, null, { value: "0.04", sourceType: "rate_observation", observationId: "12" }, "")).toEqual({
      key: "risk_free_rate", period_index: null, status: "approved", value: "0.04",
      source: { type: "rate_observation", observation_id: 12 },
    });
    expect(toChange(KEYS[4] as Parameters<typeof toChange>[0], null, {}, "")).toMatch(/choose a value/);
  });
});

describe("Preview and run", () => {
  function routes(previewBodies: unknown[], extra: Record<string, unknown> = {}) {
    let n = 0;
    return {
      [`GET ${P}`]: project(),
      [`GET ${P}/input-candidates`]: CANDIDATES,
      [`POST ${P}/preview`]: () => ({ body: previewBodies[Math.min(n++, previewBodies.length - 1)] }),
      ...extra,
    };
  }

  it("previews on the server, shows gates and outputs, and saves only when nothing blocks", async () => {
    const gate = { preview: true, dataset_status: "locked", can_save: false, input_document: null, outputs: OUTPUTS, validation: [], trace: null, engine_refusal: null,
      issues: [{ level: "gate", code: "acknowledgement_required", message: "Acknowledge reit_fair_value_gains with a reason (≥ 10 characters).", details: { ack: "reit_fair_value_gains" } }] };
    const ok = { ...gate, can_save: true, issues: [] };
    const { calls } = mockFetch(routes([gate, ok], {
      [`POST ${P}/runs`]: () => ({ status: 201, body: { action_id: "r", action: "valuation.run_create", result: { existing: false, run: RUN } } }),
    }));
    const user = userEvent.setup();
    renderWithSession(<ProjectPage projectId={PID} tab="run" rest={[]} route={routeTo(`#/valuations/${PID}/run`)} />, ME);
    await screen.findByText(/FY2024 · ends 2024-12-31/);
    expect(screen.getByText(/enter the rate as an analyst assumption/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save run…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview (nothing is saved)" }));
    expect(await screen.findByText(/Acknowledge reit_fair_value_gains/)).toBeInTheDocument();
    expect(screen.getByText("16.0882 USD")).toBeInTheDocument(); // per share, string-rounded
    expect(screen.getByText("WACC (constant)").nextSibling).toHaveTextContent("10.00%");
    expect(screen.getByRole("button", { name: "Save run…" })).toBeDisabled();
    await user.type(screen.getByLabelText(/Acknowledge reit_fair_value_gains/), "Fair-value gains removed from EBIT.");
    await user.click(screen.getByRole("button", { name: "Preview (nothing is saved)" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save run…" })).toBeEnabled());
    const second = calls.filter((c) => c.path.endsWith("/preview"))[1];
    expect(second?.body).toMatchObject({
      methodology: "dcf_fcff", scenario_id: SID, expected_scenario_revision: 1,
      dataset: { id: DS_LOCKED, content_sha256: SHA }, balance_sheet_period_id: PERIOD,
      acknowledgements: [{ code: "reit_fair_value_gains", reason: "Fair-value gains removed from EBIT." }],
    });
    await user.click(screen.getByRole("button", { name: "Save run…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save run" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/valuations/${PID}/runs/${RID}`));
    expect(calls.find((c) => c.path.endsWith("/runs"))?.headers["idempotency-key"]).toMatch(/^ui-/);
  });

  it("explains a refused save with the code and the affected inputs", async () => {
    const ok = { preview: true, dataset_status: "locked", can_save: true, input_document: null, outputs: OUTPUTS, validation: [], trace: null, engine_refusal: null, issues: [] };
    mockFetch(routes([ok], {
      [`POST ${P}/runs`]: apiError(409, "input_superseded", "risk_free_rate: its source has been superseded.", {
        action_id: "act-9",
        details: [{ level: "error", code: "input_superseded", message: "risk_free_rate: superseded.", input: "risk_free_rate" }],
      }),
    }));
    const user = userEvent.setup();
    renderWithSession(<ProjectPage projectId={PID} tab="run" rest={[]} route={routeTo(`#/valuations/${PID}/run`)} />, ME);
    await screen.findByText(/FY2024/);
    await user.click(screen.getByRole("button", { name: "Preview (nothing is saved)" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save run…" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Save run…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save run" }));
    expect(await screen.findByText(/An input has a newer revision/)).toBeInTheDocument();
    expect(screen.getByText("risk_free_rate: risk_free_rate: superseded.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "act-9" })).toBeInTheDocument();
  });

  it("offers a reasoned override for a missing line item and never fills it in", async () => {
    const missing = { preview: true, dataset_status: "locked", can_save: false, input_document: null, outputs: null, validation: [], trace: null, engine_refusal: null,
      issues: [{ level: "error", code: "input_missing", message: "BS.INVENTORY has no value.", details: { input: "operating.nwc_0", line_item: "BS.INVENTORY" } }] };
    const { calls } = mockFetch(routes([missing]));
    const user = userEvent.setup();
    renderWithSession(<ProjectPage projectId={PID} tab="run" rest={[]} route={routeTo(`#/valuations/${PID}/run`)} />, ME);
    await screen.findByText(/FY2024/);
    await user.click(screen.getByRole("button", { name: "Preview (nothing is saved)" }));
    expect(await screen.findByText(/never treated as zero/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Reason for BS.INVENTORY"), "Service company without inventory.");
    await user.click(screen.getByRole("button", { name: "Preview (nothing is saved)" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/preview"))).toHaveLength(2));
    expect(calls.filter((c) => c.path.endsWith("/preview"))[1]?.body).toMatchObject({
      input_overrides: [{ input: "operating.nwc_0", line_item: "BS.INVENTORY", kind: "not_applicable", value: null, source: null, reason: "Service company without inventory." }],
    });
  });
});

describe("Run inspection", () => {
  it("shows outputs, decisions and warnings; reproduces; walks the trace; lists input provenance", async () => {
    mockFetch({
      [`GET ${P}`]: project(),
      [`GET /api/valuation/runs/${RID}`]: RUN_DETAIL,
      [`POST /api/valuation/runs/${RID}/reproduce`]: {
        run_id: RID, matches: true, same_engine: true, stored_output_sha256: "b".repeat(64),
        recomputed_output_sha256: "b".repeat(64), stored_engine: {}, current_engine: {}, differences: [], refusal: null,
      },
      [`GET /api/valuation/runs/${RID}/trace`]: (call: { query: URLSearchParams }) => ({
        body: call.query.get("node") ? { ...TRACE, root: call.query.get("node") } : TRACE,
      }),
      [`GET /api/valuation/runs/${RID}/inputs`]: {
        run_id: RID, input_sha256: "a".repeat(64), input_document: { format: 1 },
        lineage: [{ input_key: "assumption:risk_free_rate", value: "0.0425", json: null, unit: "ratio", source_value: "4.25", source_unit: "percent", conversion: "pp_to_fraction", provenance_kind: "provider_market", source_type: "rate_observation", source_ref: "rate_observation:12", source_sha256: null, as_of_date: "2024-12-31", warnings: [] }],
      },
    });
    const user = userEvent.setup();
    renderWithSession(<ProjectPage projectId={PID} tab="runs" rest={[RID]} route={routeTo(`#/valuations/${PID}/runs/${RID}`)} />, ME);
    expect(await screen.findByText(/Fair-value gains removed from EBIT/)).toBeInTheDocument();
    expect(screen.getByText(/risk_free_rate is stale/)).toBeInTheDocument();
    expect(screen.getByText("1,858.82")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reproduce from stored inputs" }));
    expect(await screen.findByText(/Reproduced: the recalculated output hash equals the stored one/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Trace inspector" }));
    await user.click(await screen.findByRole("button", { name: "discounting.ev" }));
    const node = await screen.findByRole("generic", { name: "Trace node discounting.ev" });
    expect(within(node).getByText("Σ flow × DF(t) + PV(TV)")).toBeInTheDocument();
    await user.click(within(node).getByRole("button", { name: "discounting.pv_tv" }));
    expect(await screen.findByRole("generic", { name: "Trace node discounting.pv_tv" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Inputs & provenance" }));
    expect(await screen.findByText("rate_observation:12")).toBeInTheDocument();
    expect(screen.getByText(/4.25 percent → pp_to_fraction/)).toBeInTheDocument();
  });
});

describe("Refusal details", () => {
  it("describes valuation refusal details and validation details", () => {
    expect(describeDetail({ input: "operating.nwc_0", line_item: "BS.INVENTORY", message: "missing" })).toBe(
      "operating.nwc_0 / BS.INVENTORY: missing",
    );
    expect(describeDetail({ key: "revenue_growth", period_index: 2, message: "out of range" })).toBe(
      "revenue_growth [year 2]: out of range",
    );
    expect(describeDetail({ loc: ["body", "reason"], msg: "too short" })).toBe("reason: too short");
    renderWithSession(<ErrorMessage error={new ApiError(403, "not_owner", "Only the project owner may change this project.")} />);
    expect(screen.getByText(/Only the project owner may change this project\./, { selector: "strong" })).toBeInTheDocument();
  });
});
