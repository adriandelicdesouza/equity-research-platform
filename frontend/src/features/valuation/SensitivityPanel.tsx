// Sensitivity grids over a stored run (Phase 5b; design §9.5). The server re-executes the run's
// stored input document for every cell and stores the grid immutably; refused cells (g ≥ WACC
// and other hard checks) are shown as refused, never as numbers. The tornado ranking, swings
// and bar shares are computed by the server (D25); the browser only renders them.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ProjectDetailOut, RunOut, SensitivityOut, SensitivityRequestIn } from "../../api/schema.gen";
import { valuationApi } from "../../api/valuation";
import { useIdempotency } from "../../components/idempotency";
import { Badge, DateTime, ErrorMessage, GuardedButton, Hash, Loading, Notice } from "../../components/ui";
import { isDecimalString, parseDecimal } from "../../lib/decimal";
import { Money, PerShare, str } from "./common";
import { useEditRights } from "./ProjectPage";

type Kind = "one_way" | "two_way" | "tornado";
type Metric = "per_share" | "equity_value" | "enterprise_value";
type Variable = NonNullable<SensitivityRequestIn["x"]>["variable"];

const MAX_CELLS = 100;

/** "0.10" and "0.1" are the same coordinate (parseDecimal is canonical; no float math). */
function same(a: unknown, b: unknown): boolean {
  const pa = typeof a === "string" ? parseDecimal(a) : null;
  const pb = typeof b === "string" ? parseDecimal(b) : null;
  return !!pa && !!pb && pa.negative === pb.negative && pa.digits === pb.digits && pa.exponent === pb.exponent;
}

function values(text: string): string[] {
  return text
    .split(/[,\s]+/)
    .map((v) => v.trim())
    .filter(Boolean);
}

function Value({ value, metric, currency }: { value: unknown; metric: string; currency: string }) {
  return metric === "per_share" ? <PerShare value={value} currency={currency} /> : <Money value={value} currency={currency} />;
}

export function SensitivityPanel({ run, project }: { run: RunOut; project: ProjectDetailOut }) {
  const rights = useEditRights(project);
  const list = useQuery({
    queryKey: ["valuation", "sensitivities", run.id],
    queryFn: () => valuationApi.sensitivities(run.id),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? list.data?.items[0]?.id ?? null;
  return (
    <div className="stack">
      <SensitivityForm run={run} disabledReason={rights.reason} needsAdminReason={rights.needsAdminReason} onCreated={setSelected} />
      <section className="card" aria-label="Saved sensitivities">
        <h3>Saved sensitivities</h3>
        {list.isPending ? (
          <Loading label="Loading sensitivities…" />
        ) : list.isError ? (
          <ErrorMessage error={list.error} />
        ) : list.data.items.length === 0 ? (
          <p className="muted small">None yet for this run.</p>
        ) : (
          <ul className="small">
            {list.data.items.map((s) => (
              <li key={s.id}>
                <button type="button" className="link" aria-pressed={s.id === current} onClick={() => setSelected(s.id)}>
                  {s.label ?? `${s.kind.replace("_", "-")} · ${s.metric}`}
                </button>{" "}
                · {s.cell_count} cells{s.refused_count ? ` (${s.refused_count} refused)` : ""} · <DateTime value={s.created_at} />{" "}
                by {s.created_by.username}
              </li>
            ))}
          </ul>
        )}
      </section>
      {current && <SensitivityView sensitivityId={current} currency={run.currency} />}
    </div>
  );
}

function SensitivityForm({
  run,
  disabledReason,
  needsAdminReason,
  onCreated,
}: {
  run: RunOut;
  disabledReason: string | null;
  needsAdminReason: boolean;
  onCreated: (id: string) => void;
}) {
  const qc = useQueryClient();
  const idem = useIdempotency();
  const vars = useQuery({ queryKey: ["valuation", "sensitivity-variables"], queryFn: valuationApi.sensitivityVariables, staleTime: Infinity });
  const fcff = run.methodology === "dcf_fcff";
  const rate: Variable = fcff ? "wacc" : "cost_of_equity";
  const [kind, setKind] = useState<Kind>("two_way");
  const [metric, setMetric] = useState<Metric>("per_share");
  const [xVar, setXVar] = useState<Variable>(rate);
  const [xVals, setXVals] = useState("");
  const [yVar, setYVar] = useState<Variable>("terminal_g");
  const [yVals, setYVals] = useState("");
  const [tornado, setTornado] = useState<{ variable: Variable; low: string; high: string }[]>([
    { variable: rate, low: "", high: "" },
    { variable: "terminal_g", low: "", high: "" },
    { variable: "ebit_margin_shift", low: "", high: "" },
  ]);
  const [label, setLabel] = useState("");
  const [adminReason, setAdminReason] = useState("");
  const options = (vars.data ?? []).filter((v) => v.methodologies.includes(run.methodology));
  const build = (): SensitivityRequestIn | string => {
    const admin = needsAdminReason ? { admin_reason: adminReason.trim() } : {};
    const common = { kind, metric, label: label.trim() || null, ...admin };
    if (kind === "tornado") {
      const rows = tornado.filter((t) => t.low.trim() || t.high.trim());
      if (!rows.length) return "Enter low and high values for at least one variable.";
      if (rows.some((t) => !isDecimalString(t.low.trim()) || !isDecimalString(t.high.trim()))) return "Low and high must be plain decimals.";
      return { ...common, tornado: rows.map((t) => ({ variable: t.variable, low: t.low.trim(), high: t.high.trim() })) };
    }
    const xs = values(xVals);
    const ys = kind === "two_way" ? values(yVals) : [];
    if (!xs.length || (kind === "two_way" && !ys.length)) return "Enter the values of each axis, separated by commas.";
    if ([...xs, ...ys].some((v) => !isDecimalString(v))) return "Values must be plain decimals (fractions: 9% is 0.09).";
    if (xs.length * Math.max(1, ys.length) > MAX_CELLS) return `At most ${MAX_CELLS} cells.`;
    return {
      ...common,
      x: { variable: xVar, values: xs },
      ...(kind === "two_way" ? { y: { variable: yVar, values: ys } } : {}),
    };
  };
  const built = build();
  const create = useMutation({
    mutationFn: () => {
      if (typeof built === "string") throw new Error(built);
      return valuationApi.createSensitivity(run.id, built, idem.keyFor(built));
    },
    onSuccess: (res) => {
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["valuation", "sensitivities", run.id] });
      onCreated(res.result.sensitivity.id);
    },
  });
  const cost = run.outputs.cost_of_capital as Record<string, unknown> | undefined;
  const select = (value: Variable, set: (v: Variable) => void, aria: string) => (
    <select aria-label={aria} value={value} onChange={(e) => set(e.target.value as Variable)}>
      {options.map((o) => (
        <option key={o.variable} value={o.variable}>
          {o.label}
        </option>
      ))}
    </select>
  );
  return (
    <section className="card" aria-label="New sensitivity">
      <h3>New sensitivity</h3>
      <p className="small muted">
        Each cell re-executes this run's stored inputs with the chosen values (fractions: 9% is 0.09; shifts are added to
        every forecast year). Run values: {fcff ? "WACC" : "cost of equity"}{" "}
        <code>{str(cost?.[fcff ? "wacc" : "cost_of_equity"]) ?? "—"}</code>. At most {MAX_CELLS} cells.
      </p>
      <div className="form-grid">
        <label>
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            <option value="two_way">Two-way grid</option>
            <option value="one_way">One-way</option>
            <option value="tornado">Tornado</option>
          </select>
        </label>
        <label>
          Metric
          <select value={metric} onChange={(e) => setMetric(e.target.value as Metric)}>
            <option value="per_share">Value per share</option>
            <option value="equity_value">Equity value</option>
            {fcff && <option value="enterprise_value">Enterprise value</option>}
          </select>
        </label>
        <label>
          Label (optional)
          <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />
        </label>
      </div>
      {kind !== "tornado" ? (
        <div className="form-grid">
          <label>
            X variable {select(xVar, setXVar, "X variable")}
          </label>
          <label>
            X values
            <input aria-label="X values" placeholder="e.g. 0.09, 0.10, 0.11" value={xVals} onChange={(e) => setXVals(e.target.value)} />
          </label>
          {kind === "two_way" && (
            <>
              <label>
                Y variable {select(yVar, setYVar, "Y variable")}
              </label>
              <label>
                Y values
                <input aria-label="Y values" placeholder="e.g. 0.015, 0.02, 0.025" value={yVals} onChange={(e) => setYVals(e.target.value)} />
              </label>
            </>
          )}
        </div>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Variable</th>
              <th scope="col">Low</th>
              <th scope="col">High</th>
            </tr>
          </thead>
          <tbody>
            {tornado.map((t, i) => (
              <tr key={i}>
                <td>{select(t.variable, (v) => setTornado((rows) => rows.map((r, j) => (j === i ? { ...r, variable: v } : r))), `Tornado variable ${i + 1}`)}</td>
                <td>
                  <input aria-label={`Low ${i + 1}`} value={t.low} onChange={(e) => setTornado((rows) => rows.map((r, j) => (j === i ? { ...r, low: e.target.value } : r)))} />
                </td>
                <td>
                  <input aria-label={`High ${i + 1}`} value={t.high} onChange={(e) => setTornado((rows) => rows.map((r, j) => (j === i ? { ...r, high: e.target.value } : r)))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {needsAdminReason && (
        <label>
          Administrator reason — you are not the owner
          <input value={adminReason} onChange={(e) => setAdminReason(e.target.value)} />
        </label>
      )}
      {typeof built === "string" && (xVals || yVals || tornado.some((t) => t.low || t.high)) && <Notice tone="warn">{built}</Notice>}
      {create.isError && <ErrorMessage error={create.error} />}
      {create.isSuccess && (
        <Notice tone="success">{create.data.result.existing ? "An identical sensitivity already existed; showing it." : "Sensitivity computed and stored."}</Notice>
      )}
      <div className="form-actions">
        <GuardedButton
          disabledReason={disabledReason ?? (typeof built === "string" ? built : null)}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "Computing…" : "Compute and store"}
        </GuardedButton>
      </div>
    </section>
  );
}

function Cell({ cell, metric, currency, isBase }: { cell: Record<string, unknown>; metric: string; currency: string; isBase: boolean }) {
  if (cell.value === null || cell.value === undefined) {
    return (
      <td className="num" title={`${String(cell.message ?? "")} (${String(cell.refused_code ?? "")})`}>
        <Badge value="refused" tone="warn" /> <span className="small muted">{String(cell.refused_code ?? "")}</span>
      </td>
    );
  }
  return (
    <td className={`num${isBase ? " base-cell" : ""}`} aria-current={isBase ? "true" : undefined}>
      <Value value={cell.value} metric={metric} currency={currency} />
      {isBase && <span className="small muted"> (run)</span>}
    </td>
  );
}

function SensitivityView({ sensitivityId, currency }: { sensitivityId: string; currency: string }) {
  const q = useQuery({ queryKey: ["valuation", "sensitivity", sensitivityId], queryFn: () => valuationApi.sensitivity(sensitivityId) });
  if (q.isPending) return <Loading label="Loading sensitivity…" />;
  if (q.isError) return <ErrorMessage error={q.error} />;
  const s: SensitivityOut = q.data;
  const cfg = s.config as { x?: { variable: string; values: string[] }; y?: { variable: string; values: string[] } };
  const base = s.base as { value?: string; x?: string; y?: string; ranking?: Record<string, unknown>[] };
  const cells = s.cells;
  const xs = cfg.x?.values ?? [];
  return (
    <section className="card" aria-label="Sensitivity result">
      <h3>
        {s.label ?? `${s.kind.replace("_", "-")} sensitivity`} <Badge value="immutable" tone="good" />
      </h3>
      <p className="small muted">
        Metric {s.metric.replace("_", " ")} · run value <Value value={base.value} metric={s.metric} currency={currency} /> · engine{" "}
        {s.engine_version} · result <Hash value={s.result_sha256} />
        {s.refused_count > 0 && ` · ${s.refused_count} refused cell(s)`}
      </p>
      {s.kind === "two_way" && xs.length > 0 && cfg.y ? (
        <div className="table-wrap">
          <table className="data" aria-label="Two-way sensitivity grid">
            <thead>
              <tr>
                <th scope="col">
                  {cfg.y.variable} ↓ / {cfg.x?.variable} →
                </th>
                {xs.map((x) => (
                  <th key={x} scope="col" className="num">
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cfg.y.values.map((y) => (
                <tr key={y}>
                  <th scope="row">{y}</th>
                  {xs.map((x) => {
                    const cell = cells.find((c) => c.x === x && c.y === y) ?? {};
                    return <Cell key={x} cell={cell} metric={s.metric} currency={currency} isBase={same(x, base.x) && same(y, base.y)} />;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : s.kind === "one_way" && cfg.x ? (
        <table className="data" aria-label="One-way sensitivity">
          <thead>
            <tr>
              <th scope="col">{cfg.x.variable}</th>
              <th scope="col" className="num">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {cells.map((c) => (
              <tr key={String(c.x)}>
                <th scope="row">{String(c.x)}</th>
                <Cell cell={c} metric={s.metric} currency={currency} isBase={same(c.x, base.x)} />
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <table className="data" aria-label="Tornado">
          <thead>
            <tr>
              <th scope="col">Variable (largest swing first)</th>
              <th scope="col" className="num">
                Low
              </th>
              <th scope="col" className="num">
                High
              </th>
              <th scope="col">Swing</th>
            </tr>
          </thead>
          <tbody>
            {(base.ranking ?? []).map((r) => {
              const low = cells.find((c) => c.variable === r.variable && c.side === "low") ?? {};
              const high = cells.find((c) => c.variable === r.variable && c.side === "high") ?? {};
              const share = typeof r.share === "string" ? Math.min(1, Math.max(0, Number(r.share))) : 0;
              return (
                <tr key={String(r.variable)}>
                  <th scope="row">
                    {String(r.variable)}{" "}
                    <span className="small muted">
                      ({String(low.x)} → {String(high.x)})
                    </span>
                  </th>
                  <Cell cell={low} metric={s.metric} currency={currency} isBase={false} />
                  <Cell cell={high} metric={s.metric} currency={currency} isBase={false} />
                  <td>
                    {r.swing === null ? (
                      <span className="muted small">n/a (refused)</span>
                    ) : (
                      <span className="tornado-bar" role="img" aria-label={`swing ${String(r.swing)}`}>
                        <span style={{ width: `${(share * 100).toFixed(1)}%` }} />
                      </span>
                    )}{" "}
                    <Value value={r.swing} metric={s.metric} currency={currency} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
