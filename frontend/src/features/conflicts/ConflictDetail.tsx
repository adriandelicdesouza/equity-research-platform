import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "../../api/endpoints";
import type { ConflictOut, DatasetOut, ResolutionOut } from "../../api/schema.gen";
import { Badge, DateTime, Empty, ErrorMessage, JsonView, KV, Loading, Notice } from "../../components/ui";
import { formatExact, formatPercent } from "../../lib/decimal";
import { useSession, PERM } from "../../session";
import { periodLabel } from "../financials/common";
import { ResolutionForm } from "./ResolutionForm";

/** Conflicts are read in one page of up to 500 (shared with the statement grid's markers). */
export function useAllConflicts(datasetId: string) {
  return useQuery({
    queryKey: ["conflicts", datasetId, "all"],
    queryFn: () => api.conflicts(datasetId, { limit: 500 }),
  });
}

export function ConflictDetail({
  companyId,
  dataset,
  conflictKey,
  onClose,
  datasetsHref,
}: {
  companyId: string;
  dataset: DatasetOut;
  conflictKey: string;
  onClose: () => void;
  datasetsHref: string;
}) {
  const session = useSession();
  const all = useAllConflicts(dataset.id);
  const history = useQuery({
    queryKey: ["resolutions", companyId, conflictKey],
    queryFn: () => api.resolutions(companyId, { conflict_key: conflictKey, limit: 100 }),
  });
  const conflict = all.data?.items.find((c) => c.conflict_key === conflictKey);

  return (
    <section className="card" aria-labelledby="conflict-title">
      <div className="panel-head">
        <h2 id="conflict-title">
          Conflict {conflict ? `· ${conflict.line_item_code} · ${periodLabel(conflict.period)}` : ""}
        </h2>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
      {all.isPending && <Loading />}
      {all.isError && <ErrorMessage error={all.error} />}
      {all.isSuccess && !conflict && (
        <Notice tone="warn">
          Conflict <code>{conflictKey}</code> is not part of this dataset version. Conflicts are
          detected per build; choose the dataset in which it was detected.
        </Notice>
      )}
      {conflict && (
        <>
          <ConflictSummary c={conflict} currency={dataset.currency} />
          <Candidates c={conflict} />
          <h3>Active decision</h3>
          {conflict.resolution ? (
            <ResolutionSummary r={conflict.resolution} />
          ) : (
            <p className="muted small">
              None.{" "}
              {conflict.status === "auto_resolved_immaterial"
                ? "Immaterial: the selection policy's value is kept automatically."
                : "The selection policy's candidate is used until a decision is recorded."}
            </p>
          )}
          {session.can(PERM.write) ? (
            <ResolutionForm
              dataset={dataset}
              conflict={conflict}
              companyId={companyId}
              datasetsHref={datasetsHref}
            />
          ) : (
            <Notice>
              Read-only: your role can inspect conflicts and their history, but recording a
              decision needs the <code>company:write</code> permission (analyst or administrator).
            </Notice>
          )}
        </>
      )}
      <h3>Decision history</h3>
      {history.isPending && <Loading />}
      {history.isError && <ErrorMessage error={history.error} />}
      {history.data && history.data.items.length === 0 && <Empty>No decisions recorded.</Empty>}
      {history.data && history.data.items.length > 0 && (
        <ol className="plain">
          {history.data.items.map((r) => (
            <li key={r.id} className="card">
              <ResolutionSummary r={r} />
            </li>
          ))}
        </ol>
      )}
      <p className="small muted">
        Each decision is an append-only audit record. The acting user is stored with the audit
        event; this read endpoint does not return it. Your own decisions are listed under Action
        history.
      </p>
    </section>
  );
}

function ConflictSummary({ c, currency }: { c: ConflictOut; currency: string }) {
  return (
    <KV
      items={[
        ["Status", <Badge key="s" value={c.status} />],
        ["Line item", <code key="l">{c.line_item_code}</code>],
        ["Period", `${periodLabel(c.period)} (${c.period.start_date} → ${c.period.end_date})`],
        ["Kind", c.kind.replaceAll("_", " ")],
        ["Material", c.is_material ? "yes" : "no"],
        ["Absolute difference", `${formatExact(c.absolute_difference)} ${currency}`],
        ["Relative difference", formatPercent(c.relative_difference, 3)],
        ["Materiality threshold", `${formatPercent(c.threshold, 3)} (${c.materiality_basis})`],
        ["Conflict key", <code key="k">{c.conflict_key}</code>],
        ...(Object.keys(c.detail).length
          ? [["Detail", <JsonView key="d" value={c.detail} />] as [string, ReactNode]]
          : []),
      ]}
    />
  );
}

function Candidates({ c }: { c: ConflictOut }) {
  return (
    <>
      <h3>Candidates</h3>
      <div className="table-wrap">
        <table className="data compact">
          <thead>
            <tr>
              <th scope="col">Candidate</th>
              <th scope="col" className="num">
                Value
              </th>
              <th scope="col">Role</th>
              <th scope="col">Concept</th>
              <th scope="col">Form · filed</th>
              <th scope="col">Accession / detail</th>
            </tr>
          </thead>
          <tbody>
            {c.candidates.map((x, i) => (
              <tr key={`${x.source}-${x.candidate_id ?? i}`}>
                <th scope="row">
                  {x.source.replaceAll("_", " ")} #{x.candidate_id ?? "—"}
                  {x.candidate_id !== null && x.candidate_id === c.policy_candidate_id && (
                    <div>
                      <Badge value="policy choice" tone="neutral" />
                    </div>
                  )}
                </th>
                <td className="num mono">{x.value !== null ? formatExact(x.value) : "—"}</td>
                <td>{x.role ?? "—"}</td>
                <td>{x.concept ? <code>{x.concept}</code> : "—"}</td>
                <td>
                  {x.form ?? "—"} · {x.filed ?? "—"}
                </td>
                <td>
                  {x.accession ? <code>{x.accession}</code> : null}
                  {Object.keys(x.detail).length > 0 && <JsonView value={x.detail} />}
                  {!x.accession && Object.keys(x.detail).length === 0 && "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function ResolutionSummary({ r }: { r: ResolutionOut }) {
  return (
    <KV
      items={[
        ["Decision", <Badge key="a" value={r.action} tone="neutral" />],
        [
          "Chosen",
          r.action === "select_candidate"
            ? `${r.candidate_source.replaceAll("_", " ")} #${r.candidate_id ?? "?"}`
            : r.action === "enter_value" && r.value !== null
              ? `${formatExact(r.value)} (entered)`
              : "—",
        ],
        ["Source reference", r.source_reference],
        ["Reason", r.reason],
        ["Recorded", <DateTime key="t" value={r.created_at} />],
        ["Decision id", String(r.id)],
      ]}
    />
  );
}
