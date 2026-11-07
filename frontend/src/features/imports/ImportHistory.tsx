import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { PreviewRecord } from "../../api/imports";
import {
  Badge,
  DateTime,
  Empty,
  ErrorMessage,
  Hash,
  JsonView,
  KV,
  Loading,
  Pager,
} from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { RecordsTable } from "./PreviewReportView";

const LIMIT = 50;

function counts(obj: Record<string, unknown>): string {
  return (
    Object.entries(obj)
      .filter(([, v]) => typeof v === "number")
      .map(([k, v]) => `${k} ${String(v)}`)
      .join(" · ") || "—"
  );
}

export function ImportHistory({ route }: { route: Route }) {
  const category = route.query.get("category") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const list = useQuery({
    queryKey: ["imports", category, offset],
    queryFn: () => api.imports({ category: category || undefined, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="card">
      <div className="control-row">
        <label>
          Category
          <select
            value={category}
            onChange={(e) => navigate(withQuery(route, { category: e.target.value, offset: null }))}
          >
            <option value="">All</option>
            <option value="statement">Financial statements</option>
            <option value="market">Market prices and corporate actions</option>
            <option value="reference">Reference data (Damodaran)</option>
          </select>
        </label>
      </div>
      {list.isPending && <Loading />}
      {list.isError && <ErrorMessage error={list.error} />}
      {list.data && list.data.items.length === 0 && (
        <Empty>No file has been imported{category ? " in this category" : ""} yet.</Empty>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Template</th>
                <th scope="col">Source</th>
                <th scope="col">Status</th>
                <th scope="col">Records</th>
                <th scope="col">Imported</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((i) => (
                <tr key={i.id}>
                  <th scope="row">
                    <a href={href(["imports", i.id])}>{i.original_filename}</a>
                    <div>
                      <Hash value={i.file_sha256} />
                    </div>
                  </th>
                  <td>{i.template_version}</td>
                  <td>{i.source_name}</td>
                  <td>
                    <Badge value={i.status} />
                  </td>
                  <td className="small">
                    {counts(i.record_counts)}
                    {i.skipped_count > 0 && <div>skipped {i.skipped_count}</div>}
                  </td>
                  <td>
                    <DateTime value={i.imported_at} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
  );
}

export function ImportDetail({ importId }: { importId: string }) {
  const q = useQuery({ queryKey: ["import", importId], queryFn: () => api.importDetail(importId) });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorMessage error={q.error} />;
  const d = q.data;
  return (
    <div className="stack">
      <section className="card">
        <h2>{d.original_filename}</h2>
        <KV
          items={[
            ["Status", <Badge key="s" value={d.status} />],
            ["Category / template", `${d.category} · ${d.template_version}`],
            ["Provider", d.provider_code],
            ["File", `${d.file_kind}, ${d.bytes.toLocaleString("en-US")} bytes`],
            ["File SHA-256", <code key="h">{d.file_sha256}</code>],
            ["Source", d.source_name],
            ["Source reference", d.source_reference],
            ["Imported", <DateTime key="t" value={d.imported_at} />],
            ["Record counts", counts(d.record_counts)],
            ["Import id", <code key="i">{d.id}</code>],
          ]}
        />
      </section>
      <section className="card">
        <h2>Stored objects</h2>
        {Object.keys(d.stored_objects).length ? <JsonView value={d.stored_objects} /> : <Empty>None.</Empty>}
      </section>
      <section className="card">
        <h2>Skipped records ({d.skipped_count})</h2>
        {d.skipped.length === 0 ? (
          <Empty>No record was skipped.</Empty>
        ) : (
          <>
            <RecordsTable records={d.skipped as unknown as PreviewRecord[]} kind="any" />
            {d.skipped_truncated && (
              <p className="small muted">Only the first {d.skipped.length} skipped records are shown.</p>
            )}
          </>
        )}
      </section>
      <section className="card">
        <h2>Import summary</h2>
        <JsonView value={d.summary} />
      </section>
    </div>
  );
}
