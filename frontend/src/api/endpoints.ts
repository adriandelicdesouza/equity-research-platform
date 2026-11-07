// Typed wrappers over the read API (D59) and the workflow actions API (D60). Every call goes
// through `request`, so CSRF, Idempotency-Key and the error envelope are handled once.

import { request, setCsrfToken } from "./client";
import type { IngestParams, PreviewParams, PreviewReport } from "./imports";
import type {
  ActionOut,
  ActionRecordOut,
  BuildRequest,
  CompanyDetailOut,
  DatasetDetailOut,
  HealthOut,
  ImportDetailOut,
  IntegrityOut,
  MeOut,
  Page_ActionRecordOut_,
  Page_CompanyOut_,
  Page_ConflictOut_,
  Page_DatasetOut_,
  Page_FinancialValueOut_,
  Page_ImportOut_,
  Page_ResolutionOut_,
  RebuildRequest,
  ResolutionRequest,
  StatementOut,
} from "./schema.gen";

export type Statement = "IS" | "BS" | "CF";

export interface Paging {
  limit?: number;
  offset?: number;
}

function id(value: string): string {
  return encodeURIComponent(value);
}

export const api = {
  // ---------------------------------------------------------------- session
  health: () => request<HealthOut>("GET", "/health"),
  me: async () => {
    const me = await request<MeOut>("GET", "/auth/me");
    setCsrfToken(me.csrf_token);
    return me;
  },
  login: async (username: string, password: string) => {
    const me = await request<MeOut>("POST", "/auth/login", { json: { username, password } });
    setCsrfToken(me.csrf_token);
    return me;
  },
  logout: async () => {
    await request<undefined>("POST", "/auth/logout");
    setCsrfToken(null);
  },

  // ---------------------------------------------------------------- read API (GET)
  companies: (q: { q?: string; sort?: string } & Paging) =>
    request<Page_CompanyOut_>("GET", "/companies", { query: { ...q } }),
  company: (companyId: string) => request<CompanyDetailOut>("GET", `/companies/${id(companyId)}`),
  datasets: (companyId: string, q: { basis?: string; status?: string } & Paging = {}) =>
    request<Page_DatasetOut_>("GET", `/companies/${id(companyId)}/datasets`, { query: { ...q } }),
  dataset: (datasetId: string) => request<DatasetDetailOut>("GET", `/datasets/${id(datasetId)}`),
  integrity: (datasetId: string) =>
    request<IntegrityOut>("GET", `/datasets/${id(datasetId)}/integrity`),
  statement: (
    datasetId: string,
    statement: Statement,
    q: { period_type: string; fiscal_year_from?: number; fiscal_year_to?: number },
  ) =>
    request<StatementOut>("GET", `/datasets/${id(datasetId)}/statements/${statement}`, {
      query: { ...q },
    }),
  values: (
    datasetId: string,
    q: {
      statement?: Statement;
      line_item?: string[];
      period_type?: string;
      fiscal_year_from?: number;
      fiscal_year_to?: number;
    } & Paging,
  ) =>
    request<Page_FinancialValueOut_>("GET", `/datasets/${id(datasetId)}/values`, {
      query: { ...q },
    }),
  conflicts: (
    datasetId: string,
    q: { material?: boolean; status?: string; line_item?: string } & Paging = {},
  ) =>
    request<Page_ConflictOut_>("GET", `/datasets/${id(datasetId)}/conflicts`, { query: { ...q } }),
  resolutions: (companyId: string, q: { conflict_key?: string } & Paging = {}) =>
    request<Page_ResolutionOut_>("GET", `/companies/${id(companyId)}/conflict-resolutions`, {
      query: { ...q },
    }),
  imports: (q: { category?: string; status?: string; file_sha256?: string } & Paging = {}) =>
    request<Page_ImportOut_>("GET", "/imports", { query: { ...q } }),
  importDetail: (importId: string) =>
    request<ImportDetailOut>("GET", `/imports/${id(importId)}`),
  actions: (q: { action?: string } & Paging = {}) =>
    request<Page_ActionRecordOut_>("GET", "/actions", { query: { ...q } }),
  action: (actionId: string) => request<ActionRecordOut>("GET", `/actions/${id(actionId)}`),

  // ---------------------------------------------------------------- workflow actions (POST)
  buildDataset: (companyId: string, body: BuildRequest, key: string) =>
    request<ActionOut>("POST", `/companies/${id(companyId)}/datasets/build`, {
      json: body,
      idempotencyKey: key,
    }),
  rebuildDraft: (companyId: string, body: RebuildRequest, key: string) =>
    request<ActionOut>("POST", `/companies/${id(companyId)}/datasets/rebuild`, {
      json: body,
      idempotencyKey: key,
    }),
  verifyDataset: (datasetId: string, key: string) =>
    request<ActionOut>("POST", `/datasets/${id(datasetId)}/verify`, { idempotencyKey: key }),
  discardDataset: (datasetId: string, reason: string, key: string) =>
    request<ActionOut>("POST", `/datasets/${id(datasetId)}/discard`, {
      json: { reason },
      idempotencyKey: key,
    }),
  /** Pending P15: the backend answers 403 pending_approval for every role until approved. */
  lockDataset: (datasetId: string, reason: string, key: string) =>
    request<ActionOut>("POST", `/datasets/${id(datasetId)}/lock`, {
      json: { reason },
      idempotencyKey: key,
    }),
  resolveConflict: (datasetId: string, conflictKey: string, body: ResolutionRequest, key: string) =>
    request<ActionOut>(
      "POST",
      `/datasets/${id(datasetId)}/conflicts/${id(conflictKey)}/resolutions`,
      { json: body, idempotencyKey: key },
    ),

  /** Read-only preview: runs on a READ ONLY session, never stores, not action-logged. */
  previewImport: (p: PreviewParams, file: Blob) =>
    request<PreviewReport>("POST", `/imports/${p.kind}/validate`, {
      query: {
        filename: p.filename,
        template: p.kind === "statements" ? undefined : p.template,
        edition_date: p.kind === "reference" ? p.edition_date : undefined,
        region: p.kind === "reference" ? p.region : undefined,
        variant: p.kind === "reference" ? p.variant : undefined,
      },
      raw: { body: file, contentType: "text/csv" },
    }),
  /** Ingestion: pending P15 (403 pending_approval for every role); 503 without the archive. */
  ingestImport: (p: IngestParams, file: Blob, key: string) =>
    request<ActionOut>("POST", `/imports/${p.kind}`, {
      query: {
        filename: p.filename,
        template: p.kind === "statements" ? undefined : p.template,
        edition_date: p.kind === "reference" ? p.edition_date : undefined,
        region: p.kind === "reference" ? p.region : undefined,
        variant: p.kind === "reference" ? p.variant : undefined,
        expect_sha256: p.expect_sha256,
        source: p.source,
        source_reference: p.source_reference,
        allow_partial: p.allow_partial,
        allow_conflicts: p.kind === "statements" ? p.allow_conflicts : undefined,
        supersede: p.kind === "reference" ? p.supersede : undefined,
        reason: p.kind === "reference" && p.supersede ? p.reason : undefined,
      },
      raw: { body: file, contentType: "text/csv" },
      idempotencyKey: key,
    }),
};
