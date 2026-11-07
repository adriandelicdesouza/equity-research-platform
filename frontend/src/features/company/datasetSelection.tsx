import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { DatasetOut } from "../../api/schema.gen";
import { navigate, type Route, withQuery } from "../../lib/router";

/**
 * Which dataset version a view reads. Values are always read from exactly one dataset named
 * in the URL (`?dataset=`). Without one, the newest locked version is proposed, else the
 * newest draft; the choice is always displayed with its basis, version and status, and
 * nothing is ever merged across datasets or bases.
 */
export function useDatasets(companyId: string) {
  return useQuery({
    queryKey: ["datasets", companyId],
    queryFn: () => api.datasets(companyId, { limit: 200 }),
  });
}

export function defaultDataset(items: DatasetOut[]): DatasetOut | null {
  return (
    items.find((d) => d.status === "locked") ??
    items.find((d) => d.status === "draft") ??
    null
  );
}

export function datasetLabel(d: DatasetOut): string {
  return `${d.basis} v${d.version_no} · ${d.status}`;
}

export function useSelectedDataset(companyId: string, route: Route) {
  const datasets = useDatasets(companyId);
  const requested = route.query.get("dataset");
  const items = datasets.data?.items ?? [];
  const selected = requested ? (items.find((d) => d.id === requested) ?? null) : defaultDataset(items);
  return {
    datasets,
    items,
    selected,
    isDefault: !requested && selected !== null,
    missing: requested !== null && datasets.isSuccess && selected === null,
  };
}

export function DatasetPicker({
  route,
  items,
  selected,
  isDefault,
}: {
  route: Route;
  items: DatasetOut[];
  selected: DatasetOut | null;
  isDefault: boolean;
}) {
  return (
    <div className="dataset-picker">
      <label>
        Dataset version
        <select
          value={selected?.id ?? ""}
          onChange={(e) => navigate(withQuery(route, { dataset: e.target.value, conflict: null, cell: null }))}
        >
          {!selected && <option value="">— choose a dataset —</option>}
          {items.map((d) => (
            <option key={d.id} value={d.id}>
              {datasetLabel(d)} · {d.currency} · facts as of {d.facts_as_of}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <p className="small muted" aria-live="polite">
          Reading <strong>{selected.basis}</strong> basis (
          {selected.basis === "standardized" ? "SEC filings, standard taxonomy" : "imported statements"}
          ), version {selected.version_no}, status <strong>{selected.status}</strong>, mapping{" "}
          {selected.mapping_version}.
          {isDefault && " Proposed by default (newest locked, else newest draft) — change it above."}
        </p>
      )}
    </div>
  );
}
