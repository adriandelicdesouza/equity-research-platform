import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import { ErrorMessage, Loading } from "../../components/ui";
import { href, type Route } from "../../lib/router";
import { ConflictsTab } from "../conflicts/ConflictsTab";
import { DatasetsTab } from "../datasets/DatasetsTab";
import { FinancialsTab } from "../financials/FinancialsTab";
import { OverviewTab } from "./OverviewTab";

export const TABS = [
  { key: "overview", label: "Overview" },
  { key: "financials", label: "Financial statements" },
  { key: "conflicts", label: "Conflicts" },
  { key: "datasets", label: "Datasets & workflow" },
] as const;

export function CompanyWorkspace({
  companyId,
  tab,
  route,
}: {
  companyId: string;
  tab: string;
  route: Route;
}) {
  const company = useQuery({ queryKey: ["company", companyId], queryFn: () => api.company(companyId) });
  if (company.isPending) return <Loading label="Loading company…" />;
  if (company.isError) return <ErrorMessage error={company.error} />;
  const c = company.data;
  // Keep the dataset choice when switching tabs.
  const dataset = route.query.get("dataset");

  return (
    <div className="stack">
      <nav aria-label="Breadcrumb" className="small">
        <a href={href(["companies"])}>Companies</a> / {c.common_name ?? c.legal_name}
      </nav>
      <header className="company-head">
        <div>
          <h1>{c.common_name ?? c.legal_name}</h1>
          <p className="muted small">
            {c.legal_name}
            {c.tickers.length > 0 && <> · {c.tickers.join(", ")}</>}
            {c.country && <> · {c.country}</>}
            {c.reporting_currency && <> · reports in {c.reporting_currency}</>}
            {c.fiscal_year_end_mmdd && <> · fiscal year ends {c.fiscal_year_end_mmdd}</>}
          </p>
        </div>
      </header>
      <nav aria-label="Company sections" className="tabs">
        {TABS.map((t) => (
          <a
            key={t.key}
            href={href(["companies", companyId, t.key], { dataset })}
            aria-current={tab === t.key ? "page" : undefined}
          >
            {t.label}
          </a>
        ))}
        <a href={href(["actions"])}>Action history</a>
      </nav>
      {tab === "financials" ? (
        <FinancialsTab company={c} route={route} />
      ) : tab === "conflicts" ? (
        <ConflictsTab company={c} route={route} />
      ) : tab === "datasets" ? (
        <DatasetsTab company={c} route={route} />
      ) : (
        <OverviewTab company={c} route={route} />
      )}
    </div>
  );
}
