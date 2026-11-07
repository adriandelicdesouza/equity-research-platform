import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ProjectDetailOut } from "../../api/schema.gen";
import { valuationApi } from "../../api/valuation";
import { Badge, DateTime, Empty, ErrorMessage, Hash, Loading, Pager } from "../../components/ui";
import { href } from "../../lib/router";
import { Money, PerShare } from "./common";

const LIMIT = 50;

export function RunsList({ project }: { project: ProjectDetailOut }) {
  const [offset, setOffset] = useState(0);
  const runs = useQuery({
    queryKey: ["valuation", "runs", project.id, offset],
    queryFn: () => valuationApi.runs(project.id, { limit: LIMIT, offset }),
  });
  if (runs.isPending) return <Loading label="Loading runs…" />;
  if (runs.isError) return <ErrorMessage error={runs.error} />;
  if (runs.data.items.length === 0)
    return <Empty>No saved runs yet. Preview and save one on the “Inputs, preview & run” tab.</Empty>;
  return (
    <div className="stack">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Method</th>
              <th scope="col">Valuation date</th>
              <th scope="col">Dataset</th>
              <th scope="col" className="num">
                Primary value
              </th>
              <th scope="col" className="num">
                Per share
              </th>
              <th scope="col">Warnings</th>
              <th scope="col">Output hash</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.items.map((r) => (
              <tr key={r.id}>
                <th scope="row">
                  <a href={href(["valuations", project.id, "runs", r.id])}>{r.label ?? r.id.slice(0, 8)}</a>
                </th>
                <td>{r.methodology}</td>
                <td>{r.valuation_date}</td>
                <td>
                  v{r.dataset_version_no} {r.dataset_basis}
                </td>
                <td className="num">
                  <Money value={r.primary_value} currency={r.currency} />
                </td>
                <td className="num">
                  <PerShare value={r.per_share_value} currency={r.currency} />
                </td>
                <td>{r.warnings.length > 0 ? <Badge value={`${r.warnings.length} warning(s)`} tone="warn" /> : "—"}</td>
                <td>
                  <Hash value={r.output_sha256} />
                </td>
                <td>
                  <DateTime value={r.created_at} /> · {r.created_by.username}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager total={runs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
    </div>
  );
}
