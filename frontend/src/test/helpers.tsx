import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { setCsrfToken } from "../api/client";
import type { MeOut } from "../api/schema.gen";
import { createQueryClient } from "../queryClient";
import { SessionProvider } from "../session";

// ------------------------------------------------------------------ users (no real data)

const PERMS = {
  admin: [
    "company:read",
    "company:write",
    "valuation:read",
    "valuation:write",
    "document:read",
    "document:write",
    "report:generate",
    "audit:read",
    "provider:configure",
    "system:configure",
    "data:manage",
    "user:manage",
  ],
  analyst: [
    "company:read",
    "valuation:read",
    "document:read",
    "audit:read",
    "company:write",
    "valuation:write",
    "document:write",
    "report:generate",
  ],
  reviewer: ["company:read", "valuation:read", "document:read", "audit:read"],
} as const;

/** Mirrors backend ROLE_PERMISSIONS: no role holds dataset:lock or data:import (P15). */
export function makeMe(role: keyof typeof PERMS, extra: string[] = []): MeOut {
  return {
    user: {
      id: "00000000-0000-4000-8000-000000000001",
      username: `test-${role}`,
      display_name: `Test ${role}`,
      roles: [role],
      is_active: true,
      last_login_at: null,
    },
    permissions: [...PERMS[role], ...extra],
    csrf_token: "csrf-test-token",
    session_expires_at: "2099-01-01T00:00:00Z",
  };
}

// ------------------------------------------------------------------ fetch mock

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;

/**
 * Route table keyed by "METHOD /api/path" (path without the query). A value is either a
 * static JSON body (200) or a handler. Unknown routes fail the test loudly.
 */
export function mockFetch(routes: Record<string, unknown>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(
      Object.entries((init?.headers ?? {}) as Record<string, string>).map(([k, v]) => [k.toLowerCase(), v]),
    );
    let body: unknown = init?.body;
    if (typeof body === "string" && headers["content-type"] === "application/json") body = JSON.parse(body);
    const call: Call = { method, path: url.pathname, query: url.searchParams, headers, body };
    calls.push(call);
    const route = routes[`${method} ${url.pathname}`];
    if (route === undefined) {
      return new Response(JSON.stringify({ error: { code: "test_unmocked", message: `${method} ${url.pathname}` } }), {
        status: 599,
      });
    }
    const out = typeof route === "function" ? await (route as Handler)(call) : { body: route };
    const status = out.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(out.body ?? null), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

export function apiError(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
  return () => ({ status, body: { error: { code, message, ...extra } } });
}

// ------------------------------------------------------------------ rendering

export function renderWithSession(ui: ReactElement, me: MeOut = makeMe("analyst")) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, staleTime: 0 } });
  client.setQueryData(["me"], me);
  setCsrfToken(me.csrf_token);
  const result = render(
    <QueryClientProvider client={client}>
      <SessionProvider me={me}>{ui}</SessionProvider>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

export function setHash(hash: string) {
  window.location.hash = hash;
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

// ------------------------------------------------------------------ synthetic fixtures

export const COMPANY_ID = "11111111-1111-4111-8111-111111111111";
export const DS_LOCKED = "22222222-2222-4222-8222-222222222222";
export const DS_DRAFT = "33333333-3333-4333-8333-333333333333";
export const DS_REPORTED = "44444444-4444-4444-8444-444444444444";
export const IMPORT_ID = "55555555-5555-4555-8555-555555555555";

export function company(overrides: Record<string, unknown> = {}) {
  return {
    id: COMPANY_ID,
    legal_name: "Example Industries Inc.",
    common_name: "Example Industries",
    country: "US",
    reporting_currency: "USD",
    fiscal_year_end_mmdd: "12-31",
    accounting_standard: "US-GAAP",
    accounting_standard_inferred: true,
    financial_classification: "non_financial",
    status: "active",
    tickers: ["EXMP"],
    sic_code: "3571",
    sic_description: "Electronic Computers",
    state_of_incorporation: "DE",
    interim_coverage: "quarterly",
    identifiers: [{ scheme: "cik", value: "0000000001" }],
    listings: [
      {
        id: "66666666-6666-4666-8666-666666666666",
        company_id: COMPANY_ID,
        ticker: "EXMP",
        exchange_mic: "XNAS",
        exchange_name: "Nasdaq",
        trading_currency: "USD",
        is_primary: true,
        security_type: "common_equity",
        share_class: null,
        isin: null,
        provider_symbols: {},
        origin: "sec",
        registration_source: null,
      },
    ],
    classification: {
      effective: "non_financial",
      status: "suggested",
      latest_suggestion: null,
      latest_confirmation: null,
    },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function dataset(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    company_id: COMPANY_ID,
    basis: "standardized",
    version_no: 1,
    status: "draft",
    parent_dataset_id: null,
    currency: "USD",
    mapping_version: "v2",
    taxonomy: "us-gaap",
    selection_policy: "latest_reported",
    facts_as_of: "2026-02-01",
    interim_coverage: "quarterly",
    value_count: 120,
    format_version: 1,
    content_sha256: "a".repeat(64),
    input_sha256: "b".repeat(64),
    created_at: "2026-02-02T10:00:00Z",
    locked_at: null,
    discarded_at: null,
    status_reason: null,
    ...overrides,
  };
}

export function datasetDetail(id: string, overrides: Record<string, unknown> = {}) {
  return {
    ...dataset(id, overrides),
    source_summary: {},
    check_counts: { passed: 40, failed: 2 },
    conflict_counts: { open: 1 },
    open_material_conflicts: 1,
    ...overrides,
  };
}

export function page<T>(items: T[], total = items.length) {
  return { items, total, limit: 50, offset: 0 };
}

export const FY2024 = {
  fiscal_year: 2024,
  period_type: "FY",
  start_date: "2024-01-01",
  end_date: "2024-12-31",
  weeks: null,
};
export const FY2025 = {
  fiscal_year: 2025,
  period_type: "FY",
  start_date: "2025-01-01",
  end_date: "2025-12-31",
  weeks: null,
};

export function conflict(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    dataset_id: DS_DRAFT,
    conflict_key: "abcdef0123456789",
    kind: "mixed_vintage",
    line_item_code: "IS.REVENUE",
    period: FY2025,
    is_material: true,
    absolute_difference: "2000000",
    relative_difference: "0.0125",
    threshold: "0.005",
    materiality_basis: "relative",
    status: "open",
    candidates: [
      {
        source: "reported_fact",
        candidate_id: 1316,
        value: "160000000",
        role: "original",
        concept: "Revenues",
        form: "10-K",
        filed: "2026-01-20",
        accession: "0000000001-26-000001",
        detail: {},
      },
      {
        source: "reported_fact",
        candidate_id: 1400,
        value: "162000000",
        role: "restated",
        concept: "Revenues",
        form: "10-K/A",
        filed: "2026-03-01",
        accession: "0000000001-26-000009",
        detail: {},
      },
    ],
    policy_candidate_id: 1400,
    resolution: null,
    detail: {},
    ...overrides,
  };
}
