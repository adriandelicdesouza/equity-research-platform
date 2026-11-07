import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { ReferenceDatasetDetailOut, ReferenceRecordOut, ReferenceValueOut } from "../../api/schema.gen";
import { Badge, DateTime, Empty, ErrorMessage, Hash, KV, Loading, Pager } from "../../components/ui";
import { unitLabel } from "../../lib/format";
import { href, navigate, type Route, withQuery } from "../../lib/router";

const LIMIT = 50;

export const REFERENCE_KINDS: { value: string; label: string }[] = [
  { value: "implied_erp", label: "Implied equity risk premium" },
  { value: "country_risk", label: "Country risk premiums" },
  { value: "industry_betas", label: "Industry betas" },
  { value: "rating_spreads", label: "Rating spreads" },
];

function kindLabel(kind: string): string {
  return REFERENCE_KINDS.find((k) => k.value === kind)?.label ?? kind.replaceAll("_", " ");
}

/** Reference data (Damodaran datasets, D57): versions per lineage and their records. */
export function ReferencePage({ route }: { route: Route }) {
  const sub = route.segments[1];
  return (
    <div className="stack">
      {sub && (
        <nav aria-label="Breadcrumb" className="small">
          <a href={href(["reference"])}>All reference datasets</a> / dataset
        </nav>
      )}
      <h1>Reference data</h1>
      {sub ? <ReferenceDetail datasetId={sub} route={route} /> : <ReferenceIndex route={route} />}
    </div>
  );
}

function ReferenceIndex({ route }: { route: Route }) {
  const kind = route.query.get("kind") ?? "";
  // Current versions only by default; the full lineage history on request.
  const all = route.query.get("versions") === "all";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const list = useQuery({
    queryKey: ["reference-datasets", kind, all, offset],
    queryFn: () => api.referenceDatasets({ kind: kind || undefined, current_only: !all, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="card stack">
      <div className="control-row">
        <label>
          Kind
          <select value={kind} onChange={(e) => navigate(withQuery(route, { kind: e.target.value, offset: null }))}>
            <option value="">All</option>
            {REFERENCE_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Versions
          <select
            value={all ? "all" : "current"}
            onChange={(e) => navigate(withQuery(route, { versions: e.target.value === "all" ? "all" : null, offset: null }))}
          >
            <option value="current">current version per lineage</option>
            <option value="all">every version (superseded included)</option>
          </select>
        </label>
      </div>
      <p className="small muted">
        Reference datasets are imported from files (Damodaran templates). A correction is a new version that
        supersedes the previous one; every version stays readable.
      </p>
      {list.isPending && <Loading />}
      {list.isError && <ErrorMessage error={list.error} />}
      {list.data && list.data.items.length === 0 && (
        <Empty>
          No reference dataset has been imported{kind ? " of this kind" : ""}. Preview a file under{" "}
          <a href={href(["imports", "preview"])}>Imports</a>.
        </Empty>
      )}
      {list.data && list.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Kind</th>
                  <th scope="col">Edition</th>
                  <th scope="col">Region / variant</th>
                  <th scope="col">Version</th>
                  <th scope="col">Records</th>
                  <th scope="col">Template</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((d) => (
                  <tr key={d.id}>
                    <th scope="row">
                      <a href={href(["reference", d.id])}>{kindLabel(d.kind)}</a>
                    </th>
                    <td>{d.edition_date}</td>
                    <td>{[d.region, d.variant].filter(Boolean).join(" · ") || "—"}</td>
                    <td>
                      v{d.version_no} {d.is_current ? <Badge value="current" tone="good" /> : <Badge value="superseded" />}
                    </td>
                    <td className="num">{d.record_count}</td>
                    <td>{d.template_version}</td>
                    <td>
                      <DateTime value={d.created_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={list.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        </>
      )}
    </section>
  );
}

function ReferenceDetail({ datasetId, route }: { datasetId: string; route: Route }) {
  const detail = useQuery({
    queryKey: ["reference-dataset", datasetId],
    queryFn: () => api.referenceDataset(datasetId),
  });
  return (
    <div className="stack">
      {detail.isPending && <Loading />}
      {detail.isError && <ErrorMessage error={detail.error} />}
      {detail.data && <ReferenceSummary d={detail.data} />}
      {detail.data && <ReferenceRecords d={detail.data} route={route} />}
    </div>
  );
}

function ReferenceSummary({ d }: { d: ReferenceDatasetDetailOut }) {
  return (
    <section className="card">
      <h2>
        {kindLabel(d.kind)} — edition {d.edition_date}, v{d.version_no}
      </h2>
      <KV
        items={[
          ["Status", d.is_current ? <Badge key="s" value="current" tone="good" /> : <Badge key="s" value="superseded" />],
          ["Region / variant", [d.region, d.variant].filter(Boolean).join(" · ") || null],
          ["Lineage", <code key="l">{d.lineage_key}</code>],
          [
            "Supersedes",
            d.supersedes_id ? <a key="p" href={href(["reference", d.supersedes_id])}>previous version</a> : null,
          ],
          [
            "Superseded by",
            d.superseded_by_id ? <a key="n" href={href(["reference", d.superseded_by_id])}>newer version</a> : null,
          ],
          ["Correction reason", d.reason],
          ["Records", String(d.record_count)],
          ["Provider / template", `${d.provider_code} · ${d.template_version}`],
          ["Source", d.source_name],
          ["Source reference", d.source_reference],
          [
            "Imported file",
            <span key="f">
              <a href={href(["imports", d.import_id])}>{d.original_filename}</a> <Badge value={d.import_status} />
            </span>,
          ],
          ["File SHA-256", <Hash key="fh" value={d.file_sha256} length={16} />],
          ["Content SHA-256", <Hash key="ch" value={d.content_sha256} length={16} />],
          ["Created", <DateTime key="c" value={d.created_at} />],
        ]}
      />
    </section>
  );
}

/** Field names in first-seen order across the page (records of one template share them). */
export function fieldColumns(records: ReferenceRecordOut[]): { field: string; unit: string }[] {
  const seen = new Map<string, string>();
  for (const r of records) for (const [f, v] of Object.entries(r.fields)) if (!seen.has(f)) seen.set(f, v.unit);
  return [...seen].map(([field, unit]) => ({ field, unit }));
}

function cellText(v: ReferenceValueOut | undefined): string {
  if (!v) return "—";
  return v.value ?? v.value_text ?? "—";
}

function ReferenceRecords({ d, route }: { d: ReferenceDatasetDetailOut; route: Route }) {
  const key = route.query.get("record") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const records = useQuery({
    queryKey: ["reference-records", d.id, key, offset],
    queryFn: () => api.referenceRecords(d.id, { record_key: key || undefined, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  const columns = records.data ? fieldColumns(records.data.items) : [];
  return (
    <section className="card stack">
      <h2>Records</h2>
      <form
        className="control-row"
        onSubmit={(e) => {
          e.preventDefault();
          const v = String(new FormData(e.currentTarget).get("record") ?? "").trim();
          navigate(withQuery(route, { record: v, offset: null }));
        }}
      >
        <label>
          Record key
          <input name="record" defaultValue={key} key={key} />
        </label>
        <button type="submit" className="secondary">
          Find
        </button>
      </form>
      <p className="small muted">
        In file order, exactly as stored for this version. Hover a value for its file location.
      </p>
      {records.isPending && <Loading label="Loading records…" />}
      {records.isError && <ErrorMessage error={records.error} />}
      {records.data && records.data.items.length === 0 && (
        <Empty>{key ? `No record has the key "${key}".` : "This version has no records."}</Empty>
      )}
      {records.data && records.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Records of this reference dataset</caption>
              <thead>
                <tr>
                  <th scope="col">Record</th>
                  <th scope="col">Row</th>
                  {columns.map((c) => (
                    <th key={c.field} scope="col">
                      {c.field.replaceAll("_", " ")}
                      {unitLabel(c.unit) && <div className="small muted">{unitLabel(c.unit)}</div>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {records.data.items.map((r) => (
                  <tr key={r.record_key}>
                    <th scope="row" title={r.locator}>
                      {r.record_key}
                    </th>
                    <td className="num">{r.import_row}</td>
                    {columns.map((c) => {
                      const v = r.fields[c.field];
                      return (
                        <td key={c.field} className={v?.value != null ? "num" : undefined} title={v?.locator}>
                          {cellText(v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={records.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        </>
      )}
    </section>
  );
}
