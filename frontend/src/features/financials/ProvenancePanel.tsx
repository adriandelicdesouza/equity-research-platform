import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { ConflictOut, DatasetOut, FinancialValueOut } from "../../api/schema.gen";
import { Badge, ErrorMessage, Hash, JsonView, KV, Loading, Notice } from "../../components/ui";
import { formatExact } from "../../lib/decimal";
import { href } from "../../lib/router";
import { datasetLabel } from "../company/datasetSelection";
import { PROVENANCE, periodLabel, type SelectedCell } from "./common";

/**
 * One value's source chain (Blueprint §10.2): the stored value, its provenance kind, the SEC
 * facts or imported cell it came from (with the original reported value), its derivation
 * rule and any conflict on the same line item and period.
 */
export function ProvenancePanel({
  companyId,
  dataset,
  cell,
  conflict,
  onClose,
}: {
  companyId: string;
  dataset: DatasetOut;
  cell: SelectedCell;
  conflict: ConflictOut | null;
  onClose: () => void;
}) {
  const { row, period } = cell;
  const values = useQuery({
    queryKey: ["value", dataset.id, row.line_item_code, period.period_type, period.fiscal_year],
    queryFn: () =>
      api.values(dataset.id, {
        line_item: [row.line_item_code],
        period_type: period.period_type,
        fiscal_year_from: period.fiscal_year,
        fiscal_year_to: period.fiscal_year,
        limit: 50,
      }),
  });
  const value = values.data?.items.find(
    (v) => v.period.end_date === period.end_date && v.period.start_date === period.start_date,
  );

  return (
    <aside className="card panel" aria-labelledby="prov-title">
      <div className="panel-head">
        <h2 id="prov-title">
          {row.label ?? row.line_item_code} · {periodLabel(period)}
        </h2>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="small muted">
        {datasetLabel(dataset)} · period {period.start_date} → {period.end_date}
        {period.weeks ? ` (${period.weeks} weeks)` : ""}
      </p>
      {values.isPending && <Loading label="Loading provenance…" />}
      {values.isError && <ErrorMessage error={values.error} />}
      {values.isSuccess && !value && (
        <Notice tone="warn">
          No value is stored for this line item and period in this dataset (missing). Nothing is
          taken from another dataset or basis.
        </Notice>
      )}
      {value && <ValueDetail v={value} />}
      {conflict && (
        <section>
          <h3>Conflict on this value</h3>
          <p>
            <Badge value={conflict.status} /> {conflict.is_material ? "material" : "immaterial"} ·{" "}
            {conflict.candidates.length} candidates · kind {conflict.kind}
          </p>
          <a
            href={href(["companies", companyId, "conflicts"], {
              dataset: dataset.id,
              conflict: conflict.conflict_key,
            })}
          >
            Review the conflict
          </a>
        </section>
      )}
    </aside>
  );
}

function ValueDetail({ v }: { v: FinancialValueOut }) {
  const prov = PROVENANCE[v.provenance_kind];
  const { derivation } = v;
  return (
    <>
      <KV
        items={[
          [
            "Stored value (exact)",
            v.value != null ? (
              <span className="mono">
                {formatExact(v.value)} {v.unit}
              </span>
            ) : (
              <em>none ({v.status})</em>
            ),
          ],
          ["Unit / scale", `${v.unit}${v.scale ? ` × 10^${v.scale}` : ""}`],
          ["Status", <Badge key="s" value={v.status} />],
          ["Provenance", <span key="p">{prov?.label ?? v.provenance_kind}</span>],
          ["Concept", <code key="c">{v.concept}</code>],
          [
            "Sign",
            v.sign_multiplier === -1
              ? "Reported sign reversed (× −1) to the standard taxonomy's convention"
              : "As reported",
          ],
        ]}
      />

      {v.fact_sources.length > 0 && (
        <section>
          <h3>SEC filing facts</h3>
          <div className="table-wrap">
            <table className="data compact">
              <thead>
                <tr>
                  <th scope="col">Concept</th>
                  <th scope="col" className="num">
                    Original reported value
                  </th>
                  <th scope="col">Form</th>
                  <th scope="col">Accession</th>
                  <th scope="col">Filed</th>
                  <th scope="col">Fact period</th>
                </tr>
              </thead>
              <tbody>
                {v.fact_sources.map((f) => (
                  <tr key={f.fact_id}>
                    <td>
                      <code>
                        {f.taxonomy}:{f.concept}
                      </code>
                    </td>
                    <td className="num mono">
                      {formatExact(f.value)} {f.unit}
                    </td>
                    <td>{f.form}</td>
                    <td>
                      <code>{f.accession_no}</code>
                    </td>
                    <td>{f.filed_date}</td>
                    <td className="small">
                      {f.period_start ?? "instant"} → {f.period_end}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {v.import_source && (
        <section>
          <h3>Imported file</h3>
          <KV
            items={[
              [
                "Import",
                <a key="i" href={href(["imports", v.import_source.import_id])}>
                  {v.import_source.original_filename ?? v.import_source.import_id}
                </a>,
              ],
              ["File SHA-256", <Hash key="h" value={v.import_source.file_sha256} length={16} />],
              ["Cell", v.import_source.locator],
              ["Source document", v.import_source.source_document],
              ["Source page", v.import_source.source_page],
              ["Import value id", v.import_source.import_value_id?.toString() ?? null],
            ]}
          />
        </section>
      )}

      {Object.keys(derivation).length > 0 && (
        <section>
          <h3>{v.provenance_kind === "derived" ? "Derivation" : "Selection and audit detail"}</h3>
          <KV
            items={Object.entries(derivation).map(([k, val]) => [
              k.replaceAll("_", " "),
              typeof val === "object" && val !== null ? (
                <JsonView value={val} />
              ) : (
                String(val)
              ),
            ])}
          />
        </section>
      )}

      {v.provenance_kind === "reported" && v.fact_sources.length === 0 && !v.import_source && (
        <p className="small muted">No source facts are linked to this value.</p>
      )}
    </>
  );
}
