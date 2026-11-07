import {
  type ReactNode,
  useEffect,
  useId,
  useRef,
} from "react";
import { ApiError } from "../api/client";
import { href } from "../lib/router";

// ------------------------------------------------------------------ status/loading/empty

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {label}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty muted">{children}</p>;
}

/** What an error means for the user, by HTTP status and backend error code. */
export function explainError(err: unknown): { title: string; message: string } {
  if (!(err instanceof ApiError)) {
    return { title: "Unexpected error", message: err instanceof Error ? err.message : String(err) };
  }
  switch (err.status) {
    case 0:
      return { title: "Server unreachable", message: err.message };
    case 401:
      return {
        title: "Not signed in",
        message: "Your session has ended. Sign in again to continue.",
      };
    case 403:
      if (err.code === "pending_approval")
        return {
          title: "Awaiting owner approval",
          message: `${err.message} No role holds this permission until the owner decides (P15).`,
        };
      if (err.code === "csrf_failed" || err.code === "origin_rejected")
        return {
          title: "Security check failed",
          message: `${err.message} Reload the page and sign in again if this persists.`,
        };
      return { title: "Not permitted", message: err.message };
    case 404:
      return { title: "Not found", message: err.message };
    case 409:
      return { title: "Refused by a business rule", message: err.message };
    case 413:
      return { title: "File too large", message: err.message };
    case 422:
      return { title: "Invalid input", message: err.message };
    case 503:
      return { title: "Temporarily unavailable", message: err.message };
    default:
      return { title: `Error ${err.status}`, message: err.message };
  }
}

export function ErrorMessage({ error }: { error: unknown }) {
  const { title, message } = explainError(error);
  const details = error instanceof ApiError ? error.details : [];
  const actionId = error instanceof ApiError ? error.actionId : null;
  return (
    <div className="alert alert-error" role="alert">
      <strong>{title}.</strong> {message}
      {details.length > 0 && (
        <ul className="details">
          {details.map((d, i) => (
            <li key={i}>
              <code>{d.loc.filter((x) => x !== "body" && x !== "query").join(".") || "request"}</code>
              : {d.msg}
            </li>
          ))}
        </ul>
      )}
      {actionId && (
        <p className="small">
          The refusal is recorded as action <a href={href(["actions", actionId])}>{actionId}</a>.
        </p>
      )}
    </div>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "success";
  children: ReactNode;
}) {
  return (
    <div className={`alert alert-${tone}`} role={tone === "success" ? "status" : "note"}>
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ badges

const TONES: Record<string, string> = {
  locked: "good",
  ok: "good",
  pass: "good",
  succeeded: "good",
  accepted: "good",
  resolved: "good",
  ingested: "good",
  draft: "warn",
  open: "bad",
  conflict: "bad",
  conflicting: "bad",
  ambiguous: "bad",
  rejected: "bad",
  fail: "bad",
  refused: "bad",
  unmapped: "warn",
  overridden: "warn",
  warning: "warn",
  warn: "warn",
  duplicate: "neutral",
  discarded: "neutral",
  auto_resolved_immaterial: "neutral",
};

export function Badge({ value, tone }: { value: string; tone?: string }) {
  return <span className={`badge badge-${tone ?? TONES[value] ?? "neutral"}`}>{value.replaceAll("_", " ")}</span>;
}

// ------------------------------------------------------------------ data display

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k} className="kv-row">
          <dt>{k}</dt>
          <dd>{v ?? <span className="muted">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Hash({ value, length = 12 }: { value: string | null | undefined; length?: number }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <code className="hash" title={value}>
      {value.slice(0, length)}…
    </code>
  );
}

export function DateTime({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return <>{value}</>;
  return (
    <time dateTime={value} title={value}>
      {d.toISOString().replace("T", " ").slice(0, 19)} UTC
    </time>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function Pager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit && offset === 0) return null;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pager" aria-label="Pagination">
      <button
        type="button"
        className="secondary"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Previous
      </button>
      <span className="muted">
        {from}–{to} of {total}
      </span>
      <button
        type="button"
        className="secondary"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Next
      </button>
    </nav>
  );
}

// ------------------------------------------------------------------ dialog

/**
 * Modal confirmation for consequential actions. Escape and Cancel close it; focus moves into
 * the dialog on open and returns to the opener on close.
 */
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>(
      "input, textarea, select, button:not([disabled])",
    );
    first?.focus();
    return () => opener?.focus?.();
  }, []);
  return (
    <div className="overlay">
      <div
        ref={ref}
        className="dialog card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/** A button that is disabled with a visible, announced reason. */
export function GuardedButton({
  disabledReason,
  onClick,
  children,
  className,
}: {
  disabledReason: string | null;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  const reasonId = useId();
  return (
    <span className="guarded">
      <button
        type="button"
        className={className}
        disabled={disabledReason !== null}
        aria-describedby={disabledReason ? reasonId : undefined}
        onClick={onClick}
      >
        {children}
      </button>
      {disabledReason && (
        <span id={reasonId} className="reason small muted">
          {disabledReason}
        </span>
      )}
    </span>
  );
}
