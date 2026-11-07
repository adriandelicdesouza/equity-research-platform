import { ActionHistory } from "./features/actions/ActionHistory";
import { CompanySearch } from "./features/company/CompanySearch";
import { CompanyWorkspace } from "./features/company/CompanyWorkspace";
import { ListingsPage } from "./features/listings/ListingsPage";
import { ImportsPage } from "./features/imports/ImportsPage";
import { RatesPage } from "./features/rates/RatesPage";
import { ReferencePage } from "./features/reference/ReferencePage";
import { SystemPage } from "./features/system/SystemPage";
import { ProjectPage } from "./features/valuation/ProjectPage";
import { ProjectsPage } from "./features/valuation/ProjectsPage";
import { href, useRoute } from "./lib/router";
import { useSession } from "./session";

const NAV = [
  { key: "companies", label: "Companies" },
  { key: "valuations", label: "Valuations" },
  { key: "listings", label: "Listings" },
  { key: "imports", label: "Imports" },
  { key: "reference", label: "Reference data" },
  { key: "rates", label: "Rates & FX" },
  { key: "actions", label: "Action history" },
  { key: "system", label: "System" },
] as const;

export function Shell({ onLogout }: { onLogout: () => void }) {
  const { me } = useSession();
  const route = useRoute();
  const [section = "companies", ...rest] = route.segments;

  let page;
  if (section === "companies" && rest[0]) page = <CompanyWorkspace companyId={rest[0]} tab={rest[1] ?? "overview"} route={route} />;
  else if (section === "valuations" && rest[0])
    page = <ProjectPage projectId={rest[0]} tab={rest[1] ?? "overview"} rest={rest.slice(2)} route={route} />;
  else if (section === "valuations") page = <ProjectsPage route={route} />;
  else if (section === "listings") page = <ListingsPage route={route} />;
  else if (section === "imports") page = <ImportsPage route={route} />;
  else if (section === "rates") page = <RatesPage route={route} />;
  else if (section === "reference") page = <ReferencePage route={route} />;
  else if (section === "actions") page = <ActionHistory actionId={rest[0] ?? null} route={route} />;
  else if (section === "system") page = <SystemPage />;
  else page = <CompanySearch route={route} />;

  return (
    <div className="shell">
      <button
        type="button"
        className="skip-link"
        onClick={() => document.getElementById("main")?.focus()}
      >
        Skip to content
      </button>
      <header className="topbar">
        <a className="brand" href={href(["companies"])}>
          Equity Research Platform
        </a>
        <nav aria-label="Main" className="mainnav">
          {NAV.map((n) => (
            <a
              key={n.key}
              href={href([n.key])}
              aria-current={section === n.key ? "page" : undefined}
            >
              {n.label}
            </a>
          ))}
        </nav>
        <span className="who">
          <span title={me.permissions.join(", ")}>
            {me.user.display_name} · {me.user.roles.join(", ") || "no role"}
          </span>
          <button type="button" className="link" onClick={onLogout}>
            Sign out
          </button>
        </span>
      </header>
      <main id="main" className="content" tabIndex={-1}>
        {page}
      </main>
    </div>
  );
}
