import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type Statement } from "../../api/endpoints";
import type {
  CompanyDetailOut,
  ConflictOut,
  DatasetOut,
  StatementOut,
  StatementRowOut,
} from "../../api/schema.gen";
import { Badge, Empty, ErrorMessage, Loading, Notice } from "../../components/ui";
import { DISPLAY_SCALES, formatDecimal, formatExact, scaleSuffix } from "../../lib/decimal";
import { navigate, type Route, withQuery } from "../../lib/router";
import { DatasetPicker, useSelectedDataset } from "../company/datasetSelection";
import {
  isScalable,
  PERIOD_TYPES,
  periodKey,
  periodLabel,
  PROVENANCE,
  type SelectedCell,
  STATEMENTS,
} from "./common";
import { ProvenancePanel } from "./ProvenancePanel";

export function FinancialsTab({ company, route }: { company: CompanyDetailOut; route: Route }) {
  const { datasets, items, selected, isDefault, missing } = useSelectedDataset(company.id, route);
  const statement = (route.query.get("statement") as Statement | null) ?? "IS";
  const periodType = route.query.get("period") ?? "FY";
  const from = route.query.get("from");
  const to = route.query.get("to");
  const [scale, setScale] = useState(6);
  const [decimals, setDecimals] = useState<number | "exact">(1);

  return (
    <div className="stack">
      {datasets.isPending && <Loading label="Loading datasets…" />}
      {datasets.isError && <ErrorMessage error={datasets.error} />}
      {datasets.isSuccess && items.length === 0 && (
        <Empty>No financial datasets exist for this company yet.</Empty>
      )}
      {missing && (
        <Notice tone="warn">
          The dataset in the link does not belong to this company. Choose one of its datasets below;
          no other dataset is substituted automatically.
        </Notice>
      )}
      {items.length > 0 && (
        <section className="card controls">
          <DatasetPicker route={route} items={items} selected={selected} isDefault={isDefault} />
          <div className="control-row">
            <div role="group" aria-label="Statement" className="segmented">
              {STATEMENTS.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  aria-pressed={statement === s.key}
                  onClick={() => navigate(withQuery(route, { statement: s.key }))}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <label>
              Period
              <select
                value={periodType}
                onChange={(e) => navigate(withQuery(route, { period: e.target.value }))}
              >
                {PERIOD_TYPES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <YearInput
              label="Fiscal year from"
              value={from}
              onCommit={(v) => navigate(withQuery(route, { from: v }))}
            />
            <YearInput
              label="Fiscal year to"
              value={to}
              onCommit={(v) => navigate(withQuery(route, { to: v }))}
            />
            <label>
              Display scale
              <select value={scale} onChange={(e) => setScale(Number(e.target.value))}>
                {DISPLAY_SCALES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Decimals
              <select
                value={String(decimals)}
                onChange={(e) =>
                  setDecimals(e.target.value === "exact" ? "exact" : Number(e.target.value))
                }
              >
                <option value="0">0</option>
                <option value="1">1</option>
                <option value="2">2</option>
                <option value="exact">Exact</option>
              </select>
            </label>
          </div>
        </section>
      )}
      {selected && (
        <StatementView
          key={`${selected.id}|${statement}|${periodType}|${from}|${to}`}
          companyId={company.id}
          dataset={selected}
          statement={statement}
          periodType={periodType}
          from={from ? Number(from) : undefined}
          to={to ? Number(to) : undefined}
          scale={scale}
          decimals={decimals}
        />
      )}
    </div>
  );
}

/** Applied on blur or Enter, so a half-typed year does not trigger a request. */
function YearInput({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string | null;
  onCommit: (v: string) => void;
}) {
  const commit = (text: string) => {
    const t = text.trim();
    if (t === (value ?? "")) return;
    if (t === "" || /^\d{4}$/.test(t)) onCommit(t);
  };
  return (
    <label>
      {label}
      <input
        key={value ?? ""}
        type="text"
        inputMode="numeric"
        pattern="\d{4}"
        size={6}
        defaultValue={value ?? ""}
        placeholder="YYYY"
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(e.currentTarget.value);
        }}
      />
    </label>
  );
}

function StatementView({
  companyId,
  dataset,
  statement,
  periodType,
  from,
  to,
  scale,
  decimals,
}: {
  companyId: string;
  dataset: DatasetOut;
  statement: Statement;
  periodType: string;
  from?: number;
  to?: number;
  scale: number;
  decimals: number | "exact";
}) {
  const grid = useQuery({
    queryKey: ["statement", dataset.id, statement, periodType, from, to],
    queryFn: () =>
      api.statement(dataset.id, statement, {
        period_type: periodType,
        fiscal_year_from: from,
        fiscal_year_to: to,
      }),
    placeholderData: keepPreviousData,
  });
  // All conflicts of this dataset (any status), to flag the affected cells.
  const conflicts = useQuery({
    queryKey: ["conflicts", dataset.id, "all"],
    queryFn: () => api.conflicts(dataset.id, { limit: 500 }),
  });
  const byCell = useMemo(() => {
    const m = new Map<string, ConflictOut>();
    for (const c of conflicts.data?.items ?? []) m.set(periodKey(c.line_item_code, c.period), c);
    return m;
  }, [conflicts.data]);
  const [cell, setCell] = useState<SelectedCell | null>(null);

  if (grid.isPending) return <Loading label="Loading statement…" />;
  if (grid.isError) return <ErrorMessage error={grid.error} />;
  const g = grid.data;
  return (
    <div className={cell ? "with-panel" : undefined}>
      <section className="card">
        <StatementHeader g={g} scale={scale} />
        {g.rows.length === 0 || g.periods.length === 0 ? (
          <Empty>
            This dataset has no {statement} values for {periodType} periods
            {from || to ? " in the chosen fiscal-year range" : ""}.
          </Empty>
        ) : (
          <StatementGrid
            g={g}
            byCell={byCell}
            scale={scale}
            decimals={decimals}
            selected={cell}
            onSelect={setCell}
          />
        )}
        <Legend />
        {conflicts.isError && (
          <p className="small error">Conflict markers are unavailable: {conflicts.error.message}</p>
        )}
      </section>
      {cell && (
        <ProvenancePanel
          companyId={companyId}
          dataset={dataset}
          cell={cell}
          conflict={byCell.get(periodKey(cell.row.line_item_code, cell.period)) ?? null}
          onClose={() => setCell(null)}
        />
      )}
    </div>
  );
}

function StatementHeader({ g, scale }: { g: StatementOut; scale: number }) {
  const sfx = scaleSuffix(scale);
  return (
    <div className="grid-head">
      <h2>{STATEMENTS.find((s) => s.key === g.statement)?.label ?? g.statement}</h2>
      <p className="small muted">
        {g.basis} basis · version {g.version_no} · <Badge value={g.status} /> · {g.currency}
        {sfx ? ` amounts and share counts in ${sfx === "k" ? "thousands" : sfx === "m" ? "millions" : "billions"}` : " amounts in units"}
        ; per-share values unscaled. Exact stored values are in each cell's tooltip and provenance
        panel.
      </p>
    </div>
  );
}

export function StatementGrid({
  g,
  byCell,
  scale,
  decimals,
  selected,
  onSelect,
}: {
  g: StatementOut;
  byCell: Map<string, ConflictOut>;
  scale: number;
  decimals: number | "exact";
  selected: SelectedCell | null;
  onSelect: (c: SelectedCell) => void;
}) {
  return (
    <div className="table-wrap grid-scroll">
      <table className="data statement">
        <caption className="sr-only">
          {g.statement} for {g.basis} dataset version {g.version_no}
        </caption>
        <thead>
          <tr>
            <th scope="col" className="sticky">
              Line item
            </th>
            <th scope="col">Unit</th>
            {g.periods.map((p) => (
              <th scope="col" key={`${p.start_date}|${p.end_date}`} className="num">
                {periodLabel(p)}
                <div className="small muted">{p.end_date}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {g.rows.map((row) => {
            const scaled = isScalable(row.unit, g.currency);
            const unitLabel = scaled && scale ? `${row.unit} ${scaleSuffix(scale)}` : (row.unit ?? "—");
            return (
              <tr key={row.line_item_code} className={row.is_subtotal ? "subtotal" : undefined}>
                <th scope="row" className="sticky">
                  {row.label ?? row.line_item_code}
                  <div className="small muted code">{row.line_item_code}</div>
                </th>
                <td className="small muted">{unitLabel}</td>
                {row.cells.map((c, i) => {
                  const period = g.periods[i];
                  if (!period) return null;
                  const conflict = byCell.get(periodKey(row.line_item_code, period));
                  const isSel =
                    selected?.row.line_item_code === row.line_item_code &&
                    selected.period.end_date === period.end_date &&
                    selected.period.start_date === period.start_date;
                  return (
                    <td key={i} className="num">
                      <Cell
                        cell={c}
                        conflict={conflict}
                        text={
                          c?.value != null
                            ? formatDecimal(c.value, {
                                scale: scaled ? scale : 0,
                                decimals: decimals === "exact" ? undefined : scaled ? decimals : Math.max(decimals, 2),
                                parentheses: true,
                              })
                            : null
                        }
                        label={`${row.label ?? row.line_item_code}, ${periodLabel(period)}`}
                        selected={isSel}
                        onClick={() => onSelect({ row, period })}
                      />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Cell({
  cell,
  conflict,
  text,
  label,
  selected,
  onClick,
}: {
  cell: StatementRowOut["cells"][number];
  conflict: ConflictOut | undefined;
  text: string | null;
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  if (!cell) {
    return (
      <span className="cell-missing" title="No value stored for this period">
        <span aria-hidden="true">—</span>
        <span className="sr-only">missing</span>
      </span>
    );
  }
  const prov = PROVENANCE[cell.provenance_kind];
  const openConflict = conflict && conflict.status === "open";
  const flags = [
    prov?.label ?? cell.provenance_kind,
    cell.status !== "ok" ? `status ${cell.status}` : null,
    conflict ? `conflict ${conflict.status.replaceAll("_", " ")}` : null,
  ].filter(Boolean);
  const exact = cell.value != null ? `${formatExact(cell.value)}` : "no value";
  return (
    <button
      type="button"
      className={`cell prov-${cell.provenance_kind} ${cell.status !== "ok" ? "cell-flag" : ""} ${openConflict ? "cell-conflict" : ""}`}
      aria-pressed={selected}
      aria-label={`${label}: ${text ?? cell.status}. ${flags.join("; ")}. Show provenance.`}
      title={`${exact} — ${flags.join("; ")}`}
      onClick={onClick}
    >
      {text ?? <em>{cell.status}</em>}
      <span className="prov-tag" aria-hidden="true">
        {prov?.short ?? "?"}
      </span>
      {conflict && (
        <span className={`conflict-tag ${openConflict ? "open" : ""}`} aria-hidden="true">
          {openConflict ? "!" : "✓"}
        </span>
      )}
    </button>
  );
}

function Legend() {
  return (
    <details className="legend small">
      <summary>Legend</summary>
      <ul className="plain">
        {Object.entries(PROVENANCE).map(([k, v]) => (
          <li key={k}>
            <span className={`prov-tag inline prov-${k}`}>{v.short}</span> {v.label}
          </li>
        ))}
        <li>
          <span className="conflict-tag open inline">!</span> Open conflict between candidate values
          (see the Conflicts tab) · <span className="conflict-tag inline">✓</span> conflict resolved,
          overridden or immaterial
        </li>
        <li>
          <em>ambiguous</em> — several candidate values and no rule to choose; no value is shown
        </li>
        <li>— No value stored for the period (missing)</li>
        <li>Negative values are shown in parentheses. Unmapped records exist only in import previews.</li>
      </ul>
    </details>
  );
}
