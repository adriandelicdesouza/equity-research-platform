import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { CompanyDetailOut, DatasetDetailOut } from "../../api/schema.gen";
import {
  Badge,
  DateTime,
  Empty,
  ErrorMessage,
  Hash,
  KV,
  Loading,
} from "../../components/ui";
import { href, type Route } from "../../lib/router";
import { datasetLabel, useSelectedDataset } from "./datasetSelection";

/** "passed 400 · failed 12" from a {status: count} map, in the API's own vocabulary. */
export function formatCounts(obj: Record<string, unknown>): string {
  const parts = Object.entries(obj)
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => `${k.replaceAll("_", " ")} ${String(v)}`);
  return parts.length ? parts.join(" · ") : "none";
}

function importIds(details: DatasetDetailOut[]): string[] {
  const ids = new Set<string>();
  for (const d of details) {
    const raw = d.source_summary["imports"];
    if (Array.isArray(raw)) for (const x of raw) if (typeof x === "string") ids.add(x);
  }
  return [...ids];
}

export function OverviewTab({ company: c, route }: { company: CompanyDetailOut; route: Route }) {
  const { datasets, items, selected } = useSelectedDataset(c.id, route);
  const live = items.filter((d) => d.status !== "discarded");
  const details = useQueries({
    queries: live.map((d) => ({
      queryKey: ["dataset", d.id],
      queryFn: () => api.dataset(d.id),
    })),
  });
  const loaded = details.flatMap((q) => (q.data ? [q.data] : []));
  const integrity = useQuery({
    queryKey: ["integrity", selected?.id],
    queryFn: () => (selected ? api.integrity(selected.id) : Promise.reject(new Error("no dataset"))),
    enabled: selected !== null,
  });
  const imports = importIds(loaded.filter((d) => d.basis === "reported"));
  const importDetails = useQueries({
    queries: imports.map((id) => ({ queryKey: ["import", id], queryFn: () => api.importDetail(id) })),
  });

  const cls = c.classification;
  return (
    <div className="grid-2">
      <section className="card">
        <h2>Company profile</h2>
        <KV
          items={[
            ["Legal name", c.legal_name],
            ["Common name", c.common_name],
            ["Country", c.country],
            ["Reporting currency", c.reporting_currency],
            ["Fiscal year end (MM-DD)", c.fiscal_year_end_mmdd],
            [
              "Accounting standard",
              c.accounting_standard
                ? `${c.accounting_standard}${c.accounting_standard_inferred ? " (inferred)" : ""}`
                : null,
            ],
            ["SIC", c.sic_code ? `${c.sic_code} — ${c.sic_description ?? ""}` : null],
            ["State of incorporation", c.state_of_incorporation],
            ["Interim coverage", c.interim_coverage],
            ["Status", c.status],
            [
              "Identifiers",
              c.identifiers.length ? c.identifiers.map((i) => `${i.scheme} ${i.value}`).join(" · ") : null,
            ],
          ]}
        />
      </section>

      <section className="card">
        <h2>Financial classification</h2>
        <KV
          items={[
            ["Effective", <Badge key="e" value={cls.effective} tone="neutral" />],
            ["Status", <Badge key="s" value={cls.status} />],
            [
              "Latest suggestion",
              cls.latest_suggestion
                ? `${cls.latest_suggestion.classification} (${cls.latest_suggestion.classifier_version ?? "classifier"})`
                : null,
            ],
            [
              "Latest confirmation",
              cls.latest_confirmation
                ? `${cls.latest_confirmation.classification} — ${cls.latest_confirmation.reason ?? ""}`
                : null,
            ],
          ]}
        />
        <p className="small muted">
          Classification is confirmed through the workflow API or CLI by an analyst (D53).
        </p>
      </section>

      <section className="card span-2">
        <h2>Listings</h2>
        {c.listings.length === 0 ? (
          <Empty>No listings are registered for this company.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">Exchange</th>
                  <th scope="col">MIC</th>
                  <th scope="col">Trading currency</th>
                  <th scope="col">Primary</th>
                  <th scope="col">Security</th>
                  <th scope="col">Origin</th>
                </tr>
              </thead>
              <tbody>
                {c.listings.map((l) => (
                  <tr key={l.id}>
                    <th scope="row">{l.ticker}</th>
                    <td>{l.exchange_name}</td>
                    <td>{l.exchange_mic ?? "—"}</td>
                    <td>{l.trading_currency ?? <span className="muted">not declared</span>}</td>
                    <td>{l.is_primary ? "yes" : "no"}</td>
                    <td>{[l.security_type, l.share_class].filter(Boolean).join(" ") || "—"}</td>
                    <td>
                      {l.origin}
                      {l.registration_source && <div className="small muted">{l.registration_source}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card span-2">
        <h2>Financial datasets</h2>
        {datasets.isPending && <Loading />}
        {datasets.isError && <ErrorMessage error={datasets.error} />}
        {datasets.isSuccess && items.length === 0 && (
          <Empty>No financial datasets have been built for this company.</Empty>
        )}
        {items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Dataset</th>
                  <th scope="col">Currency</th>
                  <th scope="col">Facts as of</th>
                  <th scope="col">Values</th>
                  <th scope="col">Checks</th>
                  <th scope="col">Open material conflicts</th>
                  <th scope="col">Created</th>
                  <th scope="col">Open</th>
                </tr>
              </thead>
              <tbody>
                {items.map((d) => {
                  const det = loaded.find((x) => x.id === d.id);
                  return (
                    <tr key={d.id} aria-current={selected?.id === d.id ? "true" : undefined}>
                      <th scope="row">
                        {d.basis} v{d.version_no} <Badge value={d.status} />
                      </th>
                      <td>{d.currency}</td>
                      <td>{d.facts_as_of}</td>
                      <td className="num">{d.value_count}</td>
                      <td className="num">
                        {det
                          ? formatCounts(det.check_counts)
                          : d.status === "discarded"
                            ? "—"
                            : "…"}
                      </td>
                      <td className="num">
                        {det ? (
                          det.open_material_conflicts > 0 ? (
                            <a href={href(["companies", c.id, "conflicts"], { dataset: d.id, status: "open" })}>
                              <Badge value={`${det.open_material_conflicts} open`} tone="bad" />
                            </a>
                          ) : (
                            "0"
                          )
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        <DateTime value={d.created_at} />
                      </td>
                      <td>
                        {d.status !== "discarded" ? (
                          <a href={href(["companies", c.id, "financials"], { dataset: d.id })}>Statements</a>
                        ) : (
                          <a href={href(["companies", c.id, "datasets"], { dataset: d.id })}>Details</a>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <section className="card">
          <h2>Integrity — {datasetLabel(selected)}</h2>
          {integrity.isPending && <Loading label="Re-hashing stored values…" />}
          {integrity.isError && <ErrorMessage error={integrity.error} />}
          {integrity.data && (
            <KV
              items={[
                ["Status", <Badge key="s" value={integrity.data.status} />],
                ["Stored values match fingerprint", yesNo(integrity.data.stored_values_match)],
                ["Stored checks match fingerprint", yesNo(integrity.data.stored_checks_match)],
                ["Content SHA-256", <Hash key="h" value={selected.content_sha256} />],
                ["Note", integrity.data.note],
              ]}
            />
          )}
          <p className="small muted">
            The full rebuild comparison is the <em>Verify</em> action under{" "}
            <a href={href(["companies", c.id, "datasets"], { dataset: selected.id })}>Datasets &amp; workflow</a>.
          </p>
        </section>
      )}

      <section className="card">
        <h2>Statement imports used by this company</h2>
        {imports.length === 0 ? (
          <Empty>
            No reported-basis dataset references an imported file. Standardized datasets are built
            from SEC filings.
          </Empty>
        ) : (
          <ul className="plain">
            {imports.map((id, i) => {
              const q = importDetails[i];
              return (
                <li key={id}>
                  <a href={href(["imports", id])}>{q?.data?.original_filename ?? id}</a>
                  {q?.data && (
                    <span className="small muted">
                      {" "}
                      · {q.data.source_name} · <Badge value={q.data.status} /> ·{" "}
                      <DateTime value={q.data.imported_at} />
                    </span>
                  )}
                  {q?.isError && <ErrorMessage error={q.error} />}
                </li>
              );
            })}
          </ul>
        )}
        <p className="small">
          <a href={href(["imports"])}>All imports</a> (the import list is not company-scoped).
        </p>
      </section>
    </div>
  );
}

function yesNo(v: boolean | null): string {
  return v === null ? "not applicable" : v ? "yes" : "NO — investigate";
}
