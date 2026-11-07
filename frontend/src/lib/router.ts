// Minimal hash router. Hash URLs need no server change (Caddy already serves index.html for
// every path) and keep each view bookmarkable, including the selected dataset, statement and
// conflict. TanStack Router (D28) is not installed; see D61.

import { useSyncExternalStore } from "react";

export interface Route {
  /** Path segments after "#/", e.g. ["companies", "<id>", "financials"]. */
  segments: string[];
  query: URLSearchParams;
  /** The raw hash, for keys. */
  href: string;
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

function snapshot(): string {
  return window.location.hash;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path = "", search = ""] = raw.split("?", 2);
  const segments = path
    .split("/")
    .filter(Boolean)
    .map((s) => {
      try {
        return decodeURIComponent(s);
      } catch {
        return s;
      }
    });
  return { segments, query: new URLSearchParams(search), href: hash || "#/" };
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, snapshot, () => "");
  return parseHash(hash);
}

/** Build "#/a/b?x=1" from segments and query values (empty values are dropped). */
export function href(
  segments: readonly string[],
  query: Record<string, string | number | null | undefined> = {},
): string {
  const path = segments.map((s) => encodeURIComponent(s)).join("/");
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const search = params.toString();
  return `#/${path}${search ? `?${search}` : ""}`;
}

export function navigate(to: string): void {
  if (window.location.hash !== to) window.location.hash = to;
}

/** Replace some query parameters of the current route, keeping the others. */
export function withQuery(
  route: Route,
  changes: Record<string, string | number | null | undefined>,
): string {
  const next = new Map<string, string>();
  route.query.forEach((v, k) => next.set(k, v));
  for (const [k, v] of Object.entries(changes)) {
    if (v === undefined || v === null || v === "") next.delete(k);
    else next.set(k, String(v));
  }
  return href(route.segments, Object.fromEntries(next));
}
