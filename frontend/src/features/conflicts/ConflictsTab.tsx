import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { CompanyDetailOut, ConflictOut } from "../../api/schema.gen";
import { Badge, Empty, ErrorMessage, Loading, Notice, Pager } from "../../components/ui";
import { formatExact, formatPercent } from "../../lib/decimal";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { DatasetPicker, useSelectedDataset } from "../company/datasetSelection";
import { periodLabel } from "../financials/common";
import { ConflictDetail } from "./ConflictDetail";

const LIMIT = 50;
export const CONFLICT_STATUSES = ["open", "resolved", "overridden", "auto_resolved_immaterial"];

export function ConflictsTab({ company, route }: { company: CompanyDetailOut; route: Route }) {
  const { datasets, items, selected, isDefault, missing } = useSelectedDataset(company.id, route);
  const status = route.query.get("status") ?? "";
  const material = route.query.get("material") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const conflictKey = route.query.get("conflict");

  const list = useQuery({
    queryKey: ["conflicts", selected?.id, status, material, offset],
    queryFn: () =>
      selected
        ? api.conflicts(selected.id, {
            status: status || undefined,
            material: material === "" ? undefined : material === "true",
            limit: LIMIT,
            offset,
          })
        : Promise.reject(new Error("no dataset")),
    enabled: selected !== null,
    placeholderData: keepPreviousData,
  });

  return (
    <div className="stack">
      {datasets.isPending && <Loading label="Loading datasets…" />}
      {datasets.isError && <ErrorMessage error={datasets.error} />}
      {missing && (
        <Notice tone="warn">The dataset in the link does not belong to this company. Choose one below.</Notice>
      )}
      {datasets.isSuccess && items.length === 0 && <Empty>No datasets, so no conflicts.</Empty>}
      {items.length > 0 && (
        <section className="card controls">
          <DatasetPicker route={route} items={items} selected={selected} isDefault={isDefault} />
          <div className="control-row">
            <label>
              Status
              <select
                value={status}
                onChange={(e) => navigate(withQuery(route, { status: e.target.value, offset: null }))}
              >
                <option value="">All</option>
                {CONFLICT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Materiality
              <select
                value={material}
                onChange={(e) => navigate(withQuery(route, { material: e.target.value, offset: null }))}
              >
                <option value="">All</option>
                <option value="true">Material</option>
                <option value="false">Immaterial</option>
              </select>
            </label>
          </div>
          <p className="small muted">
            Material conflicts need a decision (select a candidate, enter a sourced value, or a
            documented override) before the dataset can be locked. Decisions are appended — never
            edited — and take effect when the draft is rebuilt (Blueprint §10.3).
          </p>
        </section>
      )}
      {selected && (
        <div className={conflictKey ? "split" : undefined}>
          <section className="card">
            <h2>Conflicts</h2>
            {list.isPending && <Loading />}
            {list.isError && <ErrorMessage error={list.error} />}
            {list.data && list.data.items.length === 0 && (
              <Empty>No conflicts match these filters in this dataset.</Empty>
            )}
            {list.data && list.data.items.length > 0 && (
              <ConflictTable
                items={list.data.items}
                current={conflictKey}
                link={(c) => withQuery(route, { conflict: c.conflict_key })}
              />
            )}
            {list.data && (
              <Pager
                total={list.data.total}
                limit={LIMIT}
                offset={offset}
                onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
              />
            )}
          </section>
          {conflictKey && (
            <ConflictDetail
              key={`${selected.id}|${conflictKey}`}
              companyId={company.id}
              dataset={selected}
              conflictKey={conflictKey}
              onClose={() => navigate(withQuery(route, { conflict: null }))}
              datasetsHref={href(["companies", company.id, "datasets"], { dataset: selected.id })}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ConflictTable({
  items,
  current,
  link,
}: {
  items: ConflictOut[];
  current: string | null;
  link: (c: ConflictOut) => string;
}) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Line item</th>
            <th scope="col">Period</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Difference
            </th>
            <th scope="col">Candidates</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.id} aria-current={current === c.conflict_key ? "true" : undefined}>
              <th scope="row">
                <a href={link(c)}>{c.line_item_code}</a>
                <div className="small muted">{c.kind.replaceAll("_", " ")}</div>
              </th>
              <td>{periodLabel(c.period)}</td>
              <td>
                <Badge value={c.status} /> {c.is_material && <Badge value="material" tone="bad" />}
              </td>
              <td className="num">
                {formatExact(c.absolute_difference)}
                <div className="small muted">{formatPercent(c.relative_difference)}</div>
              </td>
              <td>{c.candidates.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
