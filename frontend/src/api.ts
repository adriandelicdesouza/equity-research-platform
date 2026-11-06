// Thin API client. Same-origin only; the session cookie is HttpOnly, so the CSRF token
// (returned by /auth/login and /auth/me) is kept in memory and sent on unsafe methods.

export interface User {
  id: string;
  username: string;
  display_name: string;
  roles: string[];
  is_active: boolean;
  last_login_at: string | null;
}

export interface Me {
  user: User;
  permissions: string[];
  csrf_token: string;
  session_expires_at: string;
}

export interface Health {
  status: string;
  database: string;
  version: string;
  engine_version: string;
  environment: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? res.statusText);
  }
  return data as T;
}

export const api = {
  health: () => request<Health>("GET", "/health"),
  me: async () => {
    const me = await request<Me>("GET", "/auth/me");
    setCsrfToken(me.csrf_token);
    return me;
  },
  login: async (username: string, password: string) => {
    const me = await request<Me>("POST", "/auth/login", { username, password });
    setCsrfToken(me.csrf_token);
    return me;
  },
  logout: async () => {
    await request<undefined>("POST", "/auth/logout");
    setCsrfToken(null);
  },
};
