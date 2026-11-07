import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ListingCheck } from "../../api/endpoints";
import type { ActionOut, IssuerIn, ListingOut, ListingRequest } from "../../api/schema.gen";
import { useIdempotency } from "../../components/idempotency";
import {
  Dialog,
  DialogButtons,
  ErrorMessage,
  GuardedButton,
  JsonView,
  Notice,
} from "../../components/ui";
import { href, type Route } from "../../lib/router";
import { PERM, useSession } from "../../session";

const MIN_REASON = 10;

type IssuerMode = "company" | "same_issuer" | "cik" | "new";
type SecurityType = ListingRequest["security_type"];

const SECURITY_TYPES: { value: SecurityType; label: string }[] = [
  { value: "common_equity", label: "Common equity" },
  { value: "preferred_equity", label: "Preferred equity" },
  { value: "depositary_receipt", label: "Depositary receipt" },
  { value: "fund_unit", label: "Fund unit" },
  { value: "other", label: "Other" },
];

interface Form {
  mode: IssuerMode;
  companyId: string;
  sameTicker: string;
  sameMic: string;
  cik: string;
  legalName: string;
  country: string;
  commonName: string;
  reportingCurrency: string;
  confirmNewIssuer: boolean;
  ticker: string;
  mic: string;
  currency: string;
  securityType: SecurityType;
  shareClass: string;
  isin: string;
  source: string;
  sourceReference: string;
  reason: string;
  symbols: string;
}

/** "code=symbol" per line → object; returns an error message for a malformed line. */
export function parseSymbols(text: string): { value: Record<string, string> } | { error: string } {
  const value: Record<string, string> = {};
  for (const [i, raw] of text.split("\n").entries()) {
    const line = raw.trim();
    if (!line) continue;
    const m = /^([^=:\s]+)\s*[=:]\s*(\S+)$/.exec(line);
    const [, code, symbol] = m ?? [];
    if (!code || !symbol) return { error: `Provider symbols, line ${i + 1}: write "provider=SYMBOL".` };
    value[code] = symbol;
  }
  return { value };
}

function opt(text: string): string | null {
  const t = text.trim();
  return t ? t : null;
}

/** The request body exactly as the backend receives it, or a client-side error. */
export function buildRequest(f: Form): { body: ListingRequest } | { error: string } {
  const symbols = parseSymbols(f.symbols);
  if ("error" in symbols) return symbols;
  let issuer: IssuerIn;
  if (f.mode === "company") {
    if (!f.companyId) return { error: "Choose the issuer." };
    issuer = { company_id: f.companyId };
  } else if (f.mode === "same_issuer") {
    issuer = { same_issuer_as: { ticker: f.sameTicker.trim(), mic: f.sameMic.trim().toUpperCase() } };
  } else if (f.mode === "cik") {
    const cik = Number(f.cik.trim());
    if (!Number.isInteger(cik) || cik < 1) return { error: "The CIK must be a positive whole number." };
    issuer = { cik };
  } else {
    issuer = {
      new: {
        legal_name: f.legalName.trim(),
        country: f.country.trim().toUpperCase(),
        common_name: opt(f.commonName),
        reporting_currency: opt(f.reportingCurrency)?.toUpperCase() ?? null,
      },
    };
  }
  return {
    body: {
      ticker: f.ticker.trim(),
      mic: f.mic.trim().toUpperCase(),
      currency: f.currency.trim().toUpperCase(),
      security_type: f.securityType,
      issuer,
      source: f.source.trim(),
      reason: f.reason.trim(),
      source_reference: opt(f.sourceReference),
      share_class: opt(f.shareClass),
      isin: opt(f.isin)?.toUpperCase() ?? null,
      provider_symbols: symbols.value,
      confirm_new_issuer: f.mode === "new" && f.confirmNewIssuer,
    },
  };
}

export function RegisterListing({ route }: { route: Route }) {
  const session = useSession();
  const qc = useQueryClient();
  const idem = useIdempotency();
  const presetCompany = route.query.get("company") ?? "";
  const [f, setF] = useState<Form>({
    mode: presetCompany ? "company" : "new",
    companyId: presetCompany,
    sameTicker: "",
    sameMic: "",
    cik: "",
    legalName: "",
    country: "",
    commonName: "",
    reportingCurrency: "",
    confirmNewIssuer: false,
    ticker: "",
    mic: "",
    currency: "",
    securityType: "common_equity",
    shareClass: "",
    isin: "",
    source: "",
    sourceReference: "",
    reason: "",
    symbols: "",
  });
  const [open, setOpen] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((prev) => ({ ...prev, [k]: v }));

  const company = useQuery({
    queryKey: ["company", f.companyId],
    queryFn: () => api.company(f.companyId),
    enabled: f.mode === "company" && f.companyId !== "",
  });

  const built = buildRequest(f);
  const fingerprint = "body" in built ? JSON.stringify(built.body) : null;

  const check = useMutation({
    mutationFn: (body: ListingRequest) => api.validateListing(body).then((report) => ({ report, fingerprint: JSON.stringify(body) })),
  });
  const register = useMutation({
    mutationFn: (body: ListingRequest) => api.registerListing(body, idem.keyFor(["listing", body])),
    onSuccess: () => {
      setOpen(false);
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["listings"] });
      void qc.invalidateQueries({ queryKey: ["company"] });
      void qc.invalidateQueries({ queryKey: ["companies"] });
      void qc.invalidateQueries({ queryKey: ["actions"] });
    },
    onError: () => setOpen(false),
  });

  const canWrite = session.can(PERM.write);
  const checked = check.data && check.data.fingerprint === fingerprint ? check.data.report : null;
  const checkWhy = !canWrite
    ? session.whyNot(PERM.write)
    : "error" in built
      ? built.error
      : f.reason.trim().length < MIN_REASON
        ? `Enter a reason of at least ${MIN_REASON} characters.`
        : null;
  const registerWhy =
    checkWhy ??
    (!checked
      ? "Check the proposal first; any change to the form needs a new check."
      : !checked.ok
        ? "The check found errors; correct them and check again."
        : null);

  return (
    <div className="stack">
      {!canWrite && <Notice tone="info">{session.whyNot(PERM.write)} You can still read every listing.</Notice>}
      <fieldset className="card stack" disabled={!canWrite}>
        <legend>Issuer</legend>
        <div className="form-row" role="radiogroup" aria-label="Issuer">
          {(
            [
              ["company", "Existing company"],
              ["same_issuer", "Same issuer as a listing"],
              ["cik", "SEC CIK"],
              ["new", "New issuer"],
            ] as [IssuerMode, string][]
          ).map(([mode, label]) => (
            <label key={mode} className="checkbox">
              <input type="radio" name="issuer-mode" checked={f.mode === mode} onChange={() => set("mode", mode)} />
              {label}
            </label>
          ))}
        </div>
        {f.mode === "company" && (
          <>
            <label>
              Company ID
              <input value={f.companyId} onChange={(e) => set("companyId", e.target.value.trim())} />
            </label>
            {company.data && (
              <p className="small">
                Issuer: <a href={href(["companies", company.data.id])}>{company.data.legal_name}</a>
              </p>
            )}
            {company.isError && <ErrorMessage error={company.error} />}
          </>
        )}
        {f.mode === "same_issuer" && (
          <div className="form-row">
            <label>
              Existing listing ticker
              <input value={f.sameTicker} onChange={(e) => set("sameTicker", e.target.value)} />
            </label>
            <label>
              Its MIC
              <input value={f.sameMic} maxLength={4} onChange={(e) => set("sameMic", e.target.value)} />
            </label>
          </div>
        )}
        {f.mode === "cik" && (
          <label>
            SEC CIK
            <input value={f.cik} inputMode="numeric" onChange={(e) => set("cik", e.target.value)} />
          </label>
        )}
        {f.mode === "new" && (
          <>
            <div className="form-row">
              <label>
                Legal name
                <input value={f.legalName} onChange={(e) => set("legalName", e.target.value)} />
              </label>
              <label>
                Country (ISO 3166 alpha-2)
                <input value={f.country} maxLength={2} onChange={(e) => set("country", e.target.value)} />
              </label>
            </div>
            <div className="form-row">
              <label>
                Common name (optional)
                <input value={f.commonName} onChange={(e) => set("commonName", e.target.value)} />
              </label>
              <label>
                Reporting currency (optional)
                <input value={f.reportingCurrency} maxLength={3} onChange={(e) => set("reportingCurrency", e.target.value)} />
              </label>
            </div>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={f.confirmNewIssuer}
                onChange={(e) => set("confirmNewIssuer", e.target.checked)}
              />
              Confirm a new issuer even if an existing company has a similar name
            </label>
          </>
        )}
      </fieldset>

      <fieldset className="card stack" disabled={!canWrite}>
        <legend>Listing</legend>
        <div className="form-row">
          <label>
            Ticker
            <input value={f.ticker} onChange={(e) => set("ticker", e.target.value)} />
          </label>
          <label>
            Exchange MIC
            <input value={f.mic} maxLength={4} onChange={(e) => set("mic", e.target.value)} />
          </label>
          <label>
            Trading currency (ISO 4217)
            <input value={f.currency} maxLength={3} onChange={(e) => set("currency", e.target.value)} />
          </label>
          <label>
            Security type
            <select value={f.securityType} onChange={(e) => set("securityType", e.target.value as SecurityType)}>
              {SECURITY_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-row">
          <label>
            Share class (optional)
            <input value={f.shareClass} onChange={(e) => set("shareClass", e.target.value)} />
          </label>
          <label>
            ISIN (optional)
            <input value={f.isin} maxLength={12} onChange={(e) => set("isin", e.target.value)} />
          </label>
        </div>
        <label>
          Provider symbols (optional; one "provider=SYMBOL" per line)
          <textarea value={f.symbols} onChange={(e) => set("symbols", e.target.value)} />
        </label>
      </fieldset>

      <fieldset className="card stack" disabled={!canWrite}>
        <legend>Evidence</legend>
        <div className="form-row">
          <label>
            Source
            <input value={f.source} onChange={(e) => set("source", e.target.value)} />
          </label>
          <label>
            Source reference (optional; URL or document)
            <input value={f.sourceReference} onChange={(e) => set("sourceReference", e.target.value)} />
          </label>
        </div>
        <label>
          Reason (at least {MIN_REASON} characters; recorded in the audit trail)
          <textarea value={f.reason} onChange={(e) => set("reason", e.target.value)} />
        </label>
      </fieldset>

      <section className="card stack">
        <div className="form-row">
          <GuardedButton
            className="secondary"
            disabledReason={checkWhy ?? (check.isPending ? "Checking…" : null)}
            onClick={() => "body" in built && check.mutate(built.body)}
          >
            Check
          </GuardedButton>
          <GuardedButton disabledReason={registerWhy} onClick={() => setOpen(true)}>
            Register…
          </GuardedButton>
        </div>
        <p className="small muted">
          <strong>Check</strong> resolves the issuer and looks for duplicates, currency and symbol problems
          without storing anything. <strong>Register</strong> repeats the check under a lock and records the
          listing with an append-only registration record.
        </p>
        {check.isError && <ErrorMessage error={check.error} />}
        {check.data && <CheckReport report={check.data.report} stale={check.data.fingerprint !== fingerprint} />}
        {register.isError && <ErrorMessage error={register.error} />}
        {register.isSuccess && <RegistrationResult out={register.data} />}
      </section>

      {open && "body" in built && (
        <Dialog title="Register this listing" onClose={() => setOpen(false)}>
          <p>
            Register <strong>{built.body.ticker.toUpperCase()}</strong> on <strong>{built.body.mic}</strong>, trading
            in {built.body.currency}
            {f.mode === "new" ? (
              <>
                , for a <strong>new issuer</strong> ({built.body.issuer.new?.legal_name})
              </>
            ) : null}
            . Registrations are append-only.
          </p>
          <DialogButtons
            pending={register.isPending}
            label="Register listing"
            onCancel={() => setOpen(false)}
            onConfirm={() => register.mutate(built.body)}
          />
        </Dialog>
      )}
    </div>
  );
}

function CheckReport({ report, stale }: { report: ListingCheck; stale: boolean }) {
  const suggested = Object.entries(report.suggested_provider_symbols);
  return (
    <div className="stack">
      {stale && <Notice tone="warn">The form changed after this check. Check again before registering.</Notice>}
      <Notice tone={report.ok ? "success" : "warn"}>
        {report.ok ? "The check passed." : `The check found ${report.errors.length} error(s).`}
        {report.warnings.length > 0 && ` ${report.warnings.length} warning(s).`}
      </Notice>
      {report.errors.length > 0 && (
        <div className="alert alert-error">
          <strong>Errors</strong>
          <ul className="details">
            {report.errors.map((e, i) => (
              <li key={i}>
                <code>{e.code}</code>: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {report.warnings.length > 0 && (
        <div className="alert alert-warn">
          <strong>Warnings</strong>
          <ul className="details">
            {report.warnings.map((w, i) => (
              <li key={i}>
                <code>{w.code}</code>: {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {suggested.length > 0 && (
        <p className="small">
          Suggested provider symbols: {suggested.map(([k, v]) => `${k}=${v}`).join(", ")}
        </p>
      )}
      <details>
        <summary>Issuer resolution and normalized listing</summary>
        <JsonView value={{ issuer: report.issuer, listing: report.listing }} />
      </details>
    </div>
  );
}

function RegistrationResult({ out }: { out: ActionOut }) {
  const r = (out.result ?? {}) as {
    listing?: ListingOut;
    company_id?: string;
    company_created?: boolean;
    warnings?: { code: string; message: string }[];
  };
  return (
    <Notice tone="success">
      Registered{r.listing ? ` ${r.listing.ticker} on ${r.listing.exchange_name}` : ""}
      {r.company_created ? " with a new issuer" : ""}.{" "}
      {r.listing && <a href={href(["listings", r.listing.id])}>Open the listing</a>}
      {r.company_id && (
        <>
          {" · "}
          <a href={href(["companies", r.company_id])}>Open the company</a>
        </>
      )}{" "}
      <span className="small">
        (action <a href={href(["actions", out.action_id])}>{out.action_id}</a>)
      </span>
      {r.warnings && r.warnings.length > 0 && (
        <ul className="details">
          {r.warnings.map((w, i) => (
            <li key={i}>
              <code>{w.code}</code>: {w.message}
            </li>
          ))}
        </ul>
      )}
    </Notice>
  );
}
