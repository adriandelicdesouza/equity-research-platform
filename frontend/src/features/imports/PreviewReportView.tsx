import { type ReactNode, useState } from "react";
import { type ImportKind, type PreviewRecord, type PreviewReport, RECORD_STATUSES } from "../../api/imports";
import { Badge, Empty, JsonView, KV, Pager } from "../../components/ui";

const PAGE = 100;

/** What each status means, in the vocabulary of the validators (D55, D57, D58). */
export const STATUS_HELP: Record<string, string> = {
  accepted: "new; would be stored",
  duplicate: "identical record already stored (or repeated in the file); nothing to store",
  conflict: "differs from a stored value for the same key; skipped",
  conflicting: "differs from a stored import or SEC value; stored as a candidate and opens a conflict",
  unmapped: "no standard line item matches the row; skipped",
  ambiguous: "several line items or values match; skipped",
  rejected: "invalid; skipped",
};

/** Records that ingestion would skip (needs allow_partial). Mirrors each validator's rule. */
export function skippedCount(kind: ImportKind, r: PreviewReport): number {
  if (kind === "reference")
    return r.records.filter((x) => !x.storable && x.status !== "duplicate").length;
  const skipped = kind === "statements" ? ["unmapped", "ambiguous", "rejected"] : ["conflict", "rejected"];
  return r.records.filter((x) => skipped.includes(x.status)).length;
}

export function PreviewReportView({ kind, report: r }: { kind: ImportKind; report: PreviewReport }) {
  const present = RECORD_STATUSES.filter((s) => s in r.summary);
  return (
    <div className="stack">
      <section className="card">
        <h2>Validation result</h2>
        <KV
          items={[
            ["File", `${r.filename} · ${r.bytes.toLocaleString("en-US")} bytes`],
            ["Template", r.template],
            ["File SHA-256", <code key="h">{r.file_sha256}</code>],
            ...(r.issuer !== undefined
              ? [["Issuer", r.issuer ? `${r.issuer.name ?? ""} (${r.issuer.company_id})` : "not resolved"] as [string, string]]
              : []),
            ...(r.mapping_version ? [["Mapping version", r.mapping_version] as [string, string]] : []),
            ...(r.sec_compared !== undefined
              ? [["Compared with SEC values", r.sec_compared ? "yes" : "no"] as [string, string]]
              : []),
            ...(r.lineage
              ? [
                  [
                    "Lineage",
                    `${r.lineage.kind} · edition ${r.lineage.edition_date}${r.lineage.region ? ` · ${r.lineage.region}` : ""}${r.lineage.variant ? ` · ${r.lineage.variant}` : ""}`,
                  ] as [string, string],
                ]
              : []),
            ...(r.outcome ? [["Outcome", <Badge key="o" value={r.outcome} tone="neutral" />] as [string, ReactNode]] : []),
          ]}
        />
        {r.file_errors.length > 0 && (
          <div className="alert alert-error" role="alert">
            <strong>File errors — the file cannot be ingested:</strong>
            <ul className="details">
              {r.file_errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        )}
        {r.file_warnings && r.file_warnings.length > 0 && (
          <div className="alert alert-warn">
            <strong>File warnings:</strong>
            <ul className="details">
              {r.file_warnings.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        )}
        <h3>Records by status</h3>
        <ul className="plain" aria-label="Record counts">
          <li>
            <strong>{r.summary["records"] ?? r.records.length}</strong> records ·{" "}
            {r.summary["warnings"] ?? 0} warnings
            {r.summary["storable"] !== undefined && <> · {r.summary["storable"]} storable</>}
            {r.summary["removed_from_current"] !== undefined && (
              <> · {r.summary["removed_from_current"]} removed from the current version</>
            )}
          </li>
          {present.map((s) => (
            <li key={s}>
              <Badge value={s} /> <strong>{r.summary[s]}</strong>{" "}
              <span className="small muted">— {STATUS_HELP[s]}</span>
            </li>
          ))}
        </ul>
        {r.periods && r.periods.length > 0 && (
          <>
            <h3>Periods in the file</h3>
            <ul className="plain small">
              {r.periods.map((p) => (
                <li key={p.label}>
                  {p.label}: {p.period_type} FY{p.fiscal_year} ({p.start} → {p.end}) · {p.source_document}
                </li>
              ))}
            </ul>
          </>
        )}
        {r.metadata && (
          <details>
            <summary>File metadata</summary>
            <JsonView value={r.metadata} />
          </details>
        )}
        {r.current_version && (
          <details>
            <summary>Current version of this lineage</summary>
            <JsonView value={r.current_version} />
          </details>
        )}
        {r.removed_keys && r.removed_keys.length > 0 && (
          <p className="small">Removed from the current version: {r.removed_keys.join(", ")}</p>
        )}
      </section>
      <section className="card">
        <h2>Records</h2>
        <RecordsTable records={r.records} kind={kind} />
      </section>
    </div>
  );
}

export function RecordsTable({ records, kind }: { records: PreviewRecord[]; kind: ImportKind | "any" }) {
  const [status, setStatus] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [offset, setOffset] = useState(0);
  const shown = records.filter(
    (r) => (!status || r.status === status) && (!onlyIssues || r.issues.length > 0),
  );
  const statuses = [...new Set(records.map((r) => r.status))];
  const showStatements = kind === "statements" || (kind === "any" && records.some((r) => r.key !== undefined));
  const showMarket = kind === "market" || (kind === "any" && records.some((r) => r.listing !== undefined));
  const showReference = kind === "reference" || (kind === "any" && records.some((r) => r.record_key !== undefined));
  return (
    <>
      <div className="control-row">
        <label>
          Status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All ({records.length})</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s} ({records.filter((r) => r.status === s).length})
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={onlyIssues}
            onChange={(e) => {
              setOnlyIssues(e.target.checked);
              setOffset(0);
            }}
          />
          Only rows with errors or warnings
        </label>
      </div>
      {shown.length === 0 ? (
        <Empty>No records match.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="data compact">
            <thead>
              <tr>
                <th scope="col">Row</th>
                <th scope="col">Cell</th>
                <th scope="col">Status</th>
                {showStatements && (
                  <>
                    <th scope="col">Row key → line item</th>
                    <th scope="col">Period</th>
                    <th scope="col" className="num">
                      Value as written
                    </th>
                    <th scope="col" className="num">
                      Normalized
                    </th>
                  </>
                )}
                {showMarket && (
                  <>
                    <th scope="col">Listing</th>
                    <th scope="col">Date</th>
                  </>
                )}
                {showReference && (
                  <>
                    <th scope="col">Record key</th>
                    <th scope="col">Storable</th>
                  </>
                )}
                <th scope="col">Errors and warnings</th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(offset, offset + PAGE).map((r, i) => (
                <tr key={`${r.row}-${r.locator}-${i}`}>
                  <td className="num">{r.row}</td>
                  <td>
                    <code>{r.locator}</code>
                  </td>
                  <td>
                    <Badge value={r.status} />
                  </td>
                  {showStatements && (
                    <>
                      <td>
                        {r.key ?? "—"} → {r.line_item ? <code>{r.line_item}</code> : <em>unmapped</em>}
                      </td>
                      <td>{r.period ?? "—"}</td>
                      <td className="num mono">{r.value ?? "—"}</td>
                      <td className="num mono">
                        {r.normalized_value ?? "—"} {r.unit ?? ""}
                      </td>
                    </>
                  )}
                  {showMarket && (
                    <>
                      <td>{r.listing ?? "—"}</td>
                      <td>{r.date ?? "—"}</td>
                    </>
                  )}
                  {showReference && (
                    <>
                      <td>{r.record_key ?? "—"}</td>
                      <td>{r.storable === undefined ? "—" : r.storable ? "yes" : "no"}</td>
                    </>
                  )}
                  <td>
                    {r.issues.length === 0 ? (
                      <span className="muted">—</span>
                    ) : (
                      <ul className="plain">
                        {r.issues.map((x, j) => (
                          <li key={j} className={x.level === "error" ? "error" : undefined}>
                            <Badge value={x.level} tone={x.level === "error" ? "bad" : "warn"} />{" "}
                            <code>{x.code}</code> {x.message}
                            {x.column ? <span className="muted"> ({x.column})</span> : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager total={shown.length} limit={PAGE} offset={offset} onChange={setOffset} />
    </>
  );
}
