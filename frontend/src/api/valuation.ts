// Typed wrappers over the valuation API (M3 phase 5; docs/operations/valuation-api.md).
// Every call goes through `request`, so CSRF, Idempotency-Key and the error envelope are
// handled once. Exact numbers stay decimal strings end to end.

import { request } from "./client";
import type {
  ActionOut,
  ApproveRequest,
  AssumptionStateOut,
  BlockOut,
  CandidatesOut,
  CreateProjectRequest,
  CreateRunRequest,
  HistoricalContextOut,
  KeySpecOut,
  MetadataRequest,
  Page_AssumptionItemOut_,
  Page_ProjectOut_,
  Page_RunSummaryOut_,
  PreviewOut,
  ProjectDetailOut,
  ProjectOut,
  RebaseRequest,
  ReproductionOut,
  ReviseRequest,
  RunInputsOut,
  RunOut,
  RunRequestIn,
  RunSummaryOut,
  ScenarioOut,
  StatusRequest,
  TraceOut,
} from "./schema.gen";

export type Methodology = "dcf_fcff" | "dcf_fcfe";
export const METHODOLOGIES: { value: Methodology; label: string }[] = [
  { value: "dcf_fcff", label: "DCF — FCFF (enterprise value, bridge to equity)" },
  { value: "dcf_fcfe", label: "DCF — FCFE (equity value directly, no bridge)" },
];

/** Result of a successful valuation action (`ActionOut.result`), typed per action. */
export interface Action<T> extends Omit<ActionOut, "result"> {
  result: T;
}

export interface RunCreated {
  existing: boolean;
  run: RunSummaryOut;
}

function id(value: string): string {
  return encodeURIComponent(value);
}

const V = "/valuation";

export const valuationApi = {
  keys: () => request<KeySpecOut[]>("GET", `${V}/assumption-keys`),
  blocks: (q: { classification?: string; methodology?: Methodology } = {}) =>
    request<BlockOut[]>("GET", `${V}/blocks`, { query: { ...q } }),

  projects: (q: { company_id?: string; status?: string; owner?: "me"; limit?: number; offset?: number }) =>
    request<Page_ProjectOut_>("GET", `${V}/projects`, { query: { ...q } }),
  project: (projectId: string) => request<ProjectDetailOut>("GET", `${V}/projects/${id(projectId)}`),
  scenarios: (projectId: string) =>
    request<ScenarioOut[]>("GET", `${V}/projects/${id(projectId)}/scenarios`),
  createProject: (body: CreateProjectRequest, key?: string) =>
    request<Action<ProjectOut>>("POST", `${V}/projects`, { json: body, idempotencyKey: key }),
  updateProject: (projectId: string, body: MetadataRequest, key?: string) =>
    request<Action<ProjectOut>>("POST", `${V}/projects/${id(projectId)}/metadata`, {
      json: body,
      idempotencyKey: key,
    }),
  rebase: (projectId: string, body: RebaseRequest, key?: string) =>
    request<Action<ProjectOut>>("POST", `${V}/projects/${id(projectId)}/rebase`, {
      json: body,
      idempotencyKey: key,
    }),
  setStatus: (projectId: string, to: "archive" | "restore", body: StatusRequest, key?: string) =>
    request<Action<ProjectOut>>("POST", `${V}/projects/${id(projectId)}/${to}`, {
      json: body,
      idempotencyKey: key,
    }),

  assumptions: (scenarioId: string, methodology: Methodology) =>
    request<AssumptionStateOut>("GET", `${V}/scenarios/${id(scenarioId)}/assumptions`, {
      query: { methodology },
    }),
  history: (scenarioId: string, q: { key?: string; limit?: number; offset?: number }) =>
    request<Page_AssumptionItemOut_>("GET", `${V}/scenarios/${id(scenarioId)}/assumptions/history`, {
      query: { ...q },
    }),
  revise: (scenarioId: string, body: ReviseRequest, key?: string) =>
    request<Action<AssumptionStateOut>>("POST", `${V}/scenarios/${id(scenarioId)}/assumptions`, {
      json: body,
      idempotencyKey: key,
    }),
  approve: (scenarioId: string, body: ApproveRequest, key?: string) =>
    request<Action<AssumptionStateOut>>(
      "POST",
      `${V}/scenarios/${id(scenarioId)}/assumptions/approve`,
      { json: body, idempotencyKey: key },
    ),

  candidates: (projectId: string, valuationDate: string, listingId?: string | null) =>
    request<CandidatesOut>("GET", `${V}/projects/${id(projectId)}/input-candidates`, {
      query: { valuation_date: valuationDate, listing_id: listingId ?? undefined },
    }),
  historicalContext: (projectId: string, years = 10) =>
    request<HistoricalContextOut>("GET", `${V}/projects/${id(projectId)}/historical-context`, {
      query: { years },
    }),
  preview: (projectId: string, body: RunRequestIn) =>
    request<PreviewOut>("POST", `${V}/projects/${id(projectId)}/preview`, { json: body }),
  createRun: (projectId: string, body: CreateRunRequest, key?: string) =>
    request<Action<RunCreated>>("POST", `${V}/projects/${id(projectId)}/runs`, {
      json: body,
      idempotencyKey: key,
    }),
  runs: (projectId: string, q: { limit?: number; offset?: number } = {}) =>
    request<Page_RunSummaryOut_>("GET", `${V}/projects/${id(projectId)}/runs`, { query: { ...q } }),
  run: (runId: string) => request<RunOut>("GET", `${V}/runs/${id(runId)}`),
  runInputs: (runId: string) => request<RunInputsOut>("GET", `${V}/runs/${id(runId)}/inputs`),
  trace: (runId: string, q: { node?: string; depth?: number } = {}) =>
    request<TraceOut>("GET", `${V}/runs/${id(runId)}/trace`, { query: { ...q } }),
  reproduce: (runId: string) => request<ReproductionOut>("POST", `${V}/runs/${id(runId)}/reproduce`),
};
