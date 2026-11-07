import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type {
  AssumptionItemOut,
  AssumptionStateOut,
  ChangeIn,
  KeySpecOut,
  ProjectDetailOut,
} from "../../api/schema.gen";
import { METHODOLOGIES, type Methodology, valuationApi } from "../../api/valuation";
import { useIdempotency } from "../../components/idempotency";
import { Badge, DateTime, Dialog, Empty, ErrorMessage, Loading, Notice } from "../../components/ui";
import { formatPercent, isDecimalString } from "../../lib/decimal";
import { navigate, type Route, withQuery } from "../../lib/router";
import { Pct, str } from "./common";
import { useEditRights } from "./ProjectPage";

type SourceType = "analyst" | "rate_observation" | "reference_value";

interface Draft {
  value?: string;
  choice?: string;
  text?: string;
  json?: string;
  clear?: boolean;
  status?: "approved" | "suggested";
  sourceType?: SourceType;
  reference?: string;
  asOf?: string;
  currency?: string;
  observationId?: string;
  referenceDatasetId?: string;
  recordKey?: string;
  field?: string;
}

const ident = (key: string, period: number | null) => `${key}|${period ?? ""}`;

function current(state: AssumptionStateOut, key: string, period: number | null): AssumptionItemOut | undefined {
  return state.items.find((i) => i.key === key && i.period_index === period);
}

/** The value of an item as text (decimal string, choice, text or JSON). */
function shown(item: AssumptionItemOut | undefined): string {
  if (!item) return "";
  if (item.value !== null) return item.value;
  return typeof item.json === "string" ? item.json : JSON.stringify(item.json);
}

function sourceSummary(source: Record<string, unknown> | null): string {
  if (!source) return "analyst (no source)";
  const t = String(source.type);
  if (t === "rate_observation")
    return `rate ${String(source.series_code)} ${String(source.obs_date)} (${String(source.published_value)} pp → fraction)`;
  if (t === "reference_value")
    return `${String(source.dataset_kind)} ${String(source.edition_date)} · ${String(source.record_key)}/${String(source.field)}`;
  return `analyst: ${String(source.reference ?? "")}${source.as_of ? ` (as of ${String(source.as_of)})` : ""}`;
}

/** Build the API change for one draft, or an error message. */
export function toChange(spec: KeySpecOut, period: number | null, d: Draft, driverSource: string): ChangeIn | string {
  const base: ChangeIn = { key: spec.key, period_index: period, status: d.status ?? "approved" };
  if (d.clear) return { key: spec.key, period_index: period, clear: true };
  if (spec.kind === "choice") {
    if (!d.choice) return `${spec.key}: choose a value`;
    base.choice = d.choice;
  }
  else if (spec.kind === "text") base.text = d.text ?? "";
  else if (spec.kind === "json") {
    try {
      const parsed: unknown = JSON.parse(d.json ?? "[]");
      if (!Array.isArray(parsed)) return `${spec.key}: a JSON list is required`;
      base.json = parsed as Record<string, unknown>[];
    } catch {
      return `${spec.key}: invalid JSON`;
    }
  } else {
    const v = (d.value ?? "").trim();
    if (!isDecimalString(v) || /e/i.test(v)) return `${spec.key}${period ? ` [year ${period}]` : ""}: enter a plain decimal (fractions: 5% is 0.05)`;
    base.value = v;
  }
  const type = d.sourceType ?? (spec.source_required ? "analyst" : undefined);
  if (type === "rate_observation") base.source = { type, observation_id: Number(d.observationId) };
  else if (type === "reference_value")
    base.source = { type, reference_dataset_id: d.referenceDatasetId ?? "", record_key: d.recordKey ?? "", field: d.field ?? "" };
  else if (type === "analyst" || (spec.per_period && driverSource.trim())) {
    const reference = (d.reference ?? (spec.per_period ? driverSource : "")).trim();
    base.source = {
      type: "analyst",
      reference,
      ...(d.asOf ? { as_of: d.asOf } : {}),
      ...(d.currency ? { currency: d.currency } : {}),
    };
  }
  return base;
}

export function AssumptionsPanel({ project, route }: { project: ProjectDetailOut; route: Route }) {
  const qc = useQueryClient();
  const idem = useIdempotency();
  const rights = useEditRights(project);
  const method = (route.query.get("method") as Methodology | null) ?? "dcf_fcff";
  const scenario = project.scenarios.find((s) => s.kind === "base") ?? project.scenarios[0];
  const scenarioId = scenario?.id ?? "";
  const keys = useQuery({ queryKey: ["valuation", "keys"], queryFn: valuationApi.keys, staleTime: Infinity });
  const state = useQuery({
    queryKey: ["valuation", "assumptions", scenario?.id, method],
    queryFn: () => valuationApi.assumptions(scenarioId, method),
    enabled: !!scenario,
  });
  const context = useQuery({
    queryKey: ["valuation", "history-context", project.id],
    queryFn: () => valuationApi.historicalContext(project.id),
  });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [driverSource, setDriverSource] = useState("");
  const [adminReason, setAdminReason] = useState("");
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const specs = useMemo(() => (keys.data ?? []).filter((k) => k.methodologies.includes(method)), [keys.data, method]);
  const save = useMutation({
    mutationFn: (changes: ChangeIn[]) => {
      const body = {
        expected_revision: state.data?.scenario_revision ?? 0,
        changes,
        ...(rights.needsAdminReason ? { admin_reason: adminReason.trim() } : {}),
      };
      return valuationApi.revise(scenarioId, body, idem.keyFor(body));
    },
    onSuccess: () => {
      idem.reset();
      setDrafts({});
      void qc.invalidateQueries({ queryKey: ["valuation"] });
    },
  });
  const approve = useMutation({
    mutationFn: (items: { key: string; period_index: number | null }[]) => {
      const body = {
        expected_revision: state.data?.scenario_revision ?? 0,
        items,
        ...(rights.needsAdminReason ? { admin_reason: adminReason.trim() } : {}),
      };
      return valuationApi.approve(scenarioId, body, idem.keyFor(body));
    },
    onSuccess: () => {
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["valuation"] });
    },
  });

  if (!scenario) return <Empty>This project has no scenario.</Empty>;
  if (keys.isPending || state.isPending) return <Loading label="Loading assumptions…" />;
  if (keys.isError) return <ErrorMessage error={keys.error} />;
  if (state.isError) return <ErrorMessage error={state.error} />;
  const s = state.data;
  const horizonDraft = drafts[ident("horizon_years", null)]?.value;
  const horizon = Math.min(20, Math.max(0, Number(horizonDraft ?? shown(current(s, "horizon_years", null))) || 0));
  const perYear = specs.filter((k) => k.per_period);
  const single = specs.filter((k) => !k.per_period && k.kind !== "json");
  const lists = specs.filter((k) => k.kind === "json");
  const suggested = s.items.filter((i) => i.status === "suggested");
  const edit = (key: string, period: number | null, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [ident(key, period)]: { ...d[ident(key, period)], ...patch } }));
  const draftCount = Object.keys(drafts).length;

  const submit = () => {
    setLocalError(null);
    const changes: ChangeIn[] = [];
    for (const [k, d] of Object.entries(drafts)) {
      const [key, p] = k.split("|");
      const spec = specs.find((x) => x.key === key);
      if (!spec) continue;
      const change = toChange(spec, p ? Number(p) : null, d, driverSource);
      if (typeof change === "string") return setLocalError(change);
      changes.push(change);
    }
    if (changes.length) save.mutate(changes);
  };

  return (
    <div className="stack">
      <div className="toolbar">
        <label>
          Methodology{" "}
          <select value={method} onChange={(e) => navigate(withQuery(route, { method: e.target.value }))}>
            {METHODOLOGIES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">
          Scenario {scenario.name} · revision {s.scenario_revision} · ratios are fractions (5% = 0.05)
        </span>
      </div>
      {!rights.canEdit && <Notice tone="warn">Read-only: {rights.reason}</Notice>}
      {s.missing_required.length > 0 ? (
        <Notice tone="warn">
          Missing for {method}:{" "}
          {s.missing_required.map((m) => `${m.key}${m.period_index ? `[${m.period_index}]` : ""}`).join(", ")}
        </Notice>
      ) : (
        <Notice tone="success">Every required assumption for {method} has a value.</Notice>
      )}
      {s.unapproved.length > 0 && (
        <Notice tone="warn">
          {s.unapproved.length} suggested value(s) must be approved before a run can be saved.{" "}
          {rights.canEdit && (
            <button
              type="button"
              className="link"
              disabled={approve.isPending || (rights.needsAdminReason && adminReason.trim().length < 10)}
              onClick={() => approve.mutate(suggested.map((i) => ({ key: i.key, period_index: i.period_index })))}
            >
              Approve all suggested values
            </button>
          )}
        </Notice>
      )}
      {approve.isError && <ErrorMessage error={approve.error} />}

      <section className="card" aria-label="Forecast drivers">
        <h2>Forecast drivers (per year)</h2>
        {horizon === 0 ? (
          <p className="muted">Set the forecast horizon below to edit per-year drivers.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Driver</th>
                  {Array.from({ length: horizon }, (_, i) => (
                    <th key={i} scope="col" className="num">
                      Year {i + 1}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {perYear.map((spec) => (
                  <tr key={spec.key}>
                    <th scope="row">
                      {spec.label}{" "}
                      <button type="button" className="link small" onClick={() => setHistoryKey(spec.key)}>
                        history
                      </button>
                    </th>
                    {Array.from({ length: horizon }, (_, i) => {
                      const item = current(s, spec.key, i + 1);
                      const d = drafts[ident(spec.key, i + 1)];
                      const value = d?.value ?? shown(item);
                      return (
                        <td key={i} className={`num${item?.status === "suggested" ? " suggested" : ""}`}>
                          <input
                            aria-label={`${spec.label} year ${i + 1}`}
                            className="cell"
                            value={value}
                            disabled={!rights.canEdit}
                            onChange={(e) => edit(spec.key, i + 1, { value: e.target.value })}
                          />
                          {spec.kind === "ratio" && isDecimalString(value) && (
                            <span className="small muted">{formatPercent(value, 2)}</span>
                          )}
                          {item?.status === "suggested" && <Badge value="suggested" tone="warn" />}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <label>
          Source for changed drivers (analyst reference)
          <input value={driverSource} disabled={!rights.canEdit} onChange={(e) => setDriverSource(e.target.value)} />
        </label>
        {context.data && <HistoricalContext data={context.data} />}
      </section>

      <section className="card" aria-label="Settings and cost of capital">
        <h2>Settings, terminal value and cost of capital</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Assumption</th>
                <th scope="col">Current</th>
                <th scope="col">Status / source</th>
                <th scope="col">New value</th>
              </tr>
            </thead>
            <tbody>
              {single.map((spec) => (
                <SingleRow
                  key={spec.key}
                  spec={spec}
                  item={current(s, spec.key, null)}
                  draft={drafts[ident(spec.key, null)]}
                  disabled={!rights.canEdit}
                  onEdit={(patch) => edit(spec.key, null, patch)}
                  onHistory={() => setHistoryKey(spec.key)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card" aria-label="Dilution and claims">
        <h2>Dilutive instruments, claims and share adjustments</h2>
        <p className="small muted">
          JSON lists. Instruments: {"{id, kind, count, treatment, strike?}"}; claims: {"{id, label, amount}"}; share
          adjustments: {"{date, shares, source}"}. Each instrument appears once with one treatment (D23).
        </p>
        {lists.map((spec) => {
          const item = current(s, spec.key, null);
          const d = drafts[ident(spec.key, null)];
          return (
            <label key={spec.key}>
              {spec.label} {item && <Badge value={item.status} tone={item.status === "approved" ? "good" : "warn"} />}
              <textarea
                rows={3}
                value={d?.json ?? (item ? JSON.stringify(item.json) : "")}
                disabled={!rights.canEdit}
                onChange={(e) => edit(spec.key, null, { json: e.target.value })}
              />
            </label>
          );
        })}
      </section>

      {rights.needsAdminReason && (
        <label>
          Administrator reason — you are not the owner; recorded with each change
          <textarea value={adminReason} rows={2} onChange={(e) => setAdminReason(e.target.value)} />
        </label>
      )}
      {localError && (
        <div className="alert alert-error" role="alert">
          {localError}
        </div>
      )}
      {save.isError && <ErrorMessage error={save.error} />}
      {save.isSuccess && <Notice tone="success">Saved as scenario revision {save.data.result.scenario_revision}.</Notice>}
      <div className="form-actions">
        <button type="button" className="secondary" disabled={draftCount === 0} onClick={() => setDrafts({})}>
          Discard changes
        </button>
        <button
          type="button"
          disabled={!rights.canEdit || draftCount === 0 || save.isPending || (rights.needsAdminReason && adminReason.trim().length < 10)}
          onClick={submit}
        >
          {save.isPending ? "Working…" : `Save ${draftCount} change(s) as a new revision`}
        </button>
      </div>
      {historyKey && <HistoryDialog scenarioId={scenario.id} assumptionKey={historyKey} onClose={() => setHistoryKey(null)} />}
    </div>
  );
}

function SingleRow({
  spec,
  item,
  draft,
  disabled,
  onEdit,
  onHistory,
}: {
  spec: KeySpecOut;
  item: AssumptionItemOut | undefined;
  draft: Draft | undefined;
  disabled: boolean;
  onEdit: (patch: Partial<Draft>) => void;
  onHistory: () => void;
}) {
  const label = `${spec.label} (${spec.key})`;
  const sourceType = draft?.sourceType ?? (spec.source_required ? "analyst" : undefined);
  return (
    <tr>
      <th scope="row">
        {spec.label}
        <div className="small muted">
          {spec.key}
          {spec.kind === "ratio" ? " · fraction" : ""} ·{" "}
          <button type="button" className="link small" onClick={onHistory}>
            history
          </button>
        </div>
      </th>
      <td>
        {item ? (
          spec.kind === "ratio" ? (
            <Pct value={item.value} decimals={3} />
          ) : (
            <code>{shown(item)}</code>
          )
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        {item ? (
          <>
            <Badge value={item.status} tone={item.status === "approved" ? "good" : "warn"} />{" "}
            <span className="small">{sourceSummary(item.source)}</span>
          </>
        ) : (
          <span className="muted small">not set</span>
        )}
      </td>
      <td>
        {spec.kind === "choice" ? (
          <select aria-label={label} value={draft?.choice ?? ""} disabled={disabled} onChange={(e) => onEdit({ choice: e.target.value })}>
            <option value="">(unchanged)</option>
            {spec.choices.map((c) => (
              <option key={c} value={c}>
                {c.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        ) : spec.kind === "text" ? (
          <input aria-label={label} value={draft?.text ?? ""} disabled={disabled} onChange={(e) => onEdit({ text: e.target.value })} />
        ) : (
          <input
            aria-label={label}
            value={draft?.value ?? ""}
            placeholder={spec.low !== null && spec.high !== null ? `${spec.low} … ${spec.high}` : ""}
            disabled={disabled}
            onChange={(e) => onEdit({ value: e.target.value })}
          />
        )}
        {draft && (
          <div className="source-editor small">
            {spec.sources.length > 0 && (spec.source_required || spec.sources.length > 1) && (
              <select
                aria-label={`${label} source type`}
                value={sourceType ?? ""}
                onChange={(e) => onEdit({ sourceType: (e.target.value || undefined) as SourceType | undefined })}
              >
                {!spec.source_required && <option value="">no source</option>}
                {spec.sources.map((t) => (
                  <option key={t} value={t}>
                    {t.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            )}
            {sourceType === "analyst" && (
              <>
                <input aria-label={`${label} source reference`} placeholder="Source reference" value={draft.reference ?? ""} onChange={(e) => onEdit({ reference: e.target.value })} />
                {spec.market && (
                  <>
                    <input aria-label={`${label} as of`} type="date" value={draft.asOf ?? ""} onChange={(e) => onEdit({ asOf: e.target.value })} />
                    <input aria-label={`${label} currency`} placeholder="USD" maxLength={3} value={draft.currency ?? ""} onChange={(e) => onEdit({ currency: e.target.value.toUpperCase() })} />
                  </>
                )}
              </>
            )}
            {sourceType === "rate_observation" && (
              <input aria-label={`${label} observation id`} placeholder="Rate observation id (see Inputs tab)" value={draft.observationId ?? ""} onChange={(e) => onEdit({ observationId: e.target.value })} />
            )}
            {sourceType === "reference_value" && (
              <>
                <input aria-label={`${label} reference dataset id`} placeholder="Reference dataset id" value={draft.referenceDatasetId ?? ""} onChange={(e) => onEdit({ referenceDatasetId: e.target.value })} />
                <input aria-label={`${label} record key`} placeholder="Record key" value={draft.recordKey ?? ""} onChange={(e) => onEdit({ recordKey: e.target.value })} />
                <input aria-label={`${label} field`} placeholder="Field" value={draft.field ?? ""} onChange={(e) => onEdit({ field: e.target.value })} />
              </>
            )}
            <select aria-label={`${label} status`} value={draft.status ?? "approved"} onChange={(e) => onEdit({ status: e.target.value as Draft["status"] })}>
              <option value="approved">approved</option>
              <option value="suggested">suggested</option>
            </select>
            {item && (
              <label className="inline">
                <input type="checkbox" checked={!!draft.clear} onChange={(e) => onEdit({ clear: e.target.checked })} /> clear
              </label>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function HistoricalContext({ data }: { data: NonNullable<Awaited<ReturnType<typeof valuationApi.historicalContext>>> }) {
  if (data.years.length === 0) return null;
  const rows: [string, keyof (typeof data.years)[number]][] = [
    ["Revenue growth", "revenue_growth"],
    ["EBIT margin", "ebit_margin"],
    ["Tax rate", "tax_rate"],
    ["D&A % revenue", "da_pct"],
    ["Capex % revenue", "capex_pct"],
    ["NWC % revenue", "nwc_pct"],
  ];
  return (
    <details>
      <summary>Historical context (dataset v{data.dataset.version_no}; never an input until entered and approved)</summary>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Ratio</th>
              {data.years.map((y) => (
                <th key={y.fiscal_year} scope="col" className="num">
                  FY{y.fiscal_year}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, field]) => (
              <tr key={field}>
                <th scope="row">{label}</th>
                {data.years.map((y) => (
                  <td key={y.fiscal_year} className="num">
                    <Pct value={str(y[field])} decimals={1} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">{data.note}</p>
    </details>
  );
}

function HistoryDialog({ scenarioId, assumptionKey, onClose }: { scenarioId: string; assumptionKey: string; onClose: () => void }) {
  const history = useQuery({
    queryKey: ["valuation", "assumption-history", scenarioId, assumptionKey],
    queryFn: () => valuationApi.history(scenarioId, { key: assumptionKey, limit: 100 }),
  });
  return (
    <Dialog title={`History of ${assumptionKey}`} onClose={onClose}>
      {history.isPending ? (
        <Loading />
      ) : history.isError ? (
        <ErrorMessage error={history.error} />
      ) : history.data.items.length === 0 ? (
        <Empty>No revisions.</Empty>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Revision</th>
              <th scope="col">Year</th>
              <th scope="col">Value</th>
              <th scope="col">Status</th>
              <th scope="col">Source</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {history.data.items.map((h) => (
              <tr key={h.revision_id}>
                <td>{h.scenario_revision_no}</td>
                <td>{h.period_index ?? "—"}</td>
                <td>
                  <code>{shown(h)}</code>
                </td>
                <td>
                  <Badge value={h.status} />
                </td>
                <td className="small">{sourceSummary(h.source)}</td>
                <td>
                  <DateTime value={h.created_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
