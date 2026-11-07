// HTTP core for the whole UI. Same-origin only; the session cookie is HttpOnly, so the CSRF
// token (returned by /auth/login and /auth/me) is kept in memory and sent on every unsafe
// method. The browser adds the Origin header itself; the backend checks both (D60).

export type QueryValue = string | number | boolean | null | undefined | readonly string[];
export type Query = Record<string, QueryValue>;

export interface ValidationDetail {
  loc: (string | number)[];
  msg: string;
}

/** Valuation refusals (409) list the affected inputs, keys, checks or acknowledgements. */
export type RefusalDetail = Record<string, unknown>;
export type ErrorDetail = ValidationDetail | RefusalDetail;

export function isValidationDetail(d: ErrorDetail): d is ValidationDetail {
  return Array.isArray((d as ValidationDetail).loc) && typeof (d as ValidationDetail).msg === "string";
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetail[];
  /** Set when the backend recorded the refusal in the action log (409 etc.). */
  readonly actionId: string | null;
  constructor(
    status: number,
    code: string,
    message: string,
    details: ErrorDetail[] = [],
    actionId: string | null = null,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.actionId = actionId;
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function buildQuery(query: Query | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) params.append(key, v);
    } else {
      params.append(key, String(value));
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export interface RequestOptions {
  query?: Query;
  /** JSON body. */
  json?: unknown;
  /** Raw body (file uploads are sent as the raw request body, D60). */
  raw?: { body: Blob | string; contentType: string };
  idempotencyKey?: string;
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: ErrorDetail[];
    action_id?: string;
  };
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.raw) {
    headers["Content-Type"] = opts.raw.contentType;
    body = opts.raw.body;
  }
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(`/api${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      credentials: "same-origin",
      body,
    });
  } catch {
    throw new ApiError(0, "network_error", "The server could not be reached.");
  }
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as ErrorEnvelope | null)?.error;
    throw new ApiError(
      res.status,
      err?.code ?? "http_error",
      err?.message ?? (res.statusText || `HTTP ${res.status}`),
      Array.isArray(err?.details) ? err.details : [],
      err?.action_id ?? null,
    );
  }
  return data as T;
}

/** A fresh key for one user-intended action; reused when that same action is retried. */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return `ui-${c.randomUUID()}`;
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
