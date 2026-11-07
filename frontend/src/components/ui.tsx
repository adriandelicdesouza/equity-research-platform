import {
  type ReactNode,
  useEffect,
  useId,
  useRef,
} from "react";
import { ApiError, isValidationDetail, type ErrorDetail } from "../api/client";
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

/** Titles for valuation refusals (409 codes, docs/operations/valuation-api.md). */
export const VALUATION_REFUSALS: Record<string, string> = {
  dataset_not_locked: "The dataset is a draft — saved runs need a locked dataset",
  dataset_mismatch: "Not the project's pinned dataset",
  dataset_hash_mismatch: "The dataset changed since you loaded it",
  dataset_discarded: "The pinned dataset was discarded",
  assumption_not_approved: "Unapproved assumptions",
  input_missing: "Inputs are missing",
  input_ambiguous: "An input is ambiguous in the dataset",
  input_lookahead: "An input is dated after the valuation date",
  input_superseded: "An input has a newer revision",
  input_stale: "An input is stale",
  currency_mismatch: "Currency mismatch",
  classification_unconfirmed: "The financial classification is not confirmed",
  classification_blocked: "This methodology is not permitted for the company's classification",
  acknowledgement_required: "A warning must be acknowledged with a reason",
  invalid_acknowledgement: "Unexpected acknowledgement",
  checks_failed: "Dataset checks failed for the period used",
  invalid_check_override: "Unexpected check override",
  invalid_override: "Invalid input override",
  reason_required: "A reason is required",
  revision_conflict: "Changed since you loaded it — reload and retry",
  engine_refused: "The calculation engine refused the inputs",
  validation_blocked: "Validation blocks saving this run",
  source_mismatch: "The value differs from its source",
  out_of_range: "Value out of range",
  project_archived: "The project is archived",
  lease_treatment_not_supported: "Lease treatment not supported in Phase 5",
  unsupported_capital_structure: "Capital structure not supported",
};

/** One refusal detail in words: the affected input/key/check and its message. */
export function describeDetail(d: ErrorDetail): string {
  if (isValidationDetail(d)) {
    return `${d.loc.filter((x) => x !== "body" && x !== "query").join(".") || "request"}: ${d.msg}`;
  }
  const subject = [d.input ?? d.key, d.line_item, d.check_code, d.ack, d.engine_code]
    .filter((x) => x !== undefined && x !== null && x !== "")
    .map(String)
    .join(" / ");
  const period = typeof d.period_index === "number" ? ` [year ${d.period_index}]` : "";
  const message = typeof d.message === "string" ? d.message : "";
  return `${subject}${period}${subject && message ? ": " : ""}${message}` || JSON.stringify(d);
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
      if (err.code === "not_owner")
        return {
          title: "Only the project owner may change this project",
          message: `${err.message} Administrators can act with a recorded reason.`,
        };
      if (err.code === "admin_reason_required")
        return { title: "A reason is required", message: err.message };
      if (err.code === "csrf_failed" || err.code === "origin_rejected")
        return {
          title: "Security check failed",
          message: `${err.message} Reload the page and sign in again if this persists.`,
        };
      return { title: "Not permitted", message: err.message };
    case 404:
      return { title: "Not found", message: err.message };
    case 409:
      if (err.code === "ambiguous_series")
        return {
          title: "Choose a series",
          message: `${err.message} Choose one in the filters above.`,
        };
      if (err.code in VALUATION_REFUSALS)
        return { title: VALUATION_REFUSALS[err.code] ?? "Refused", message: err.message };
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
            <li key={i}>{describeDetail(d)}</li>
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

/** Cancel + confirm buttons of a confirmation dialog. */
export function DialogButtons({
  pending,
  label,
  danger,
  disabled,
  onCancel,
  onConfirm,
}: {
  pending: boolean;
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="form-actions">
      <button type="button" className="secondary" onClick={onCancel}>
        Cancel
      </button>
      <button type="button" className={danger ? "danger" : undefined} disabled={pending || disabled} onClick={onConfirm}>
        {pending ? "Working…" : label}
      </button>
    </div>
  );
}
