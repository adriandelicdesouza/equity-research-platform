import { useQuery } from "@tanstack/react-query";
import { api, type Me } from "./api";

interface Props {
  me: Me;
  onLogout: () => void;
}

export function HomePage({ me, onLogout }: Props) {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Equity Research Platform</span>
        <span className="who">
          {me.user.display_name} · {me.user.roles.join(", ")}
          <button type="button" className="link" onClick={onLogout}>
            Sign out
          </button>
        </span>
      </header>
      <main className="content">
        <section className="card">
          <h2>System</h2>
          {health.isPending && <p>Checking…</p>}
          {health.isError && <p className="error">Health check failed.</p>}
          {health.data && (
            <dl className="kv">
              <dt>Status</dt>
              <dd>{health.data.status}</dd>
              <dt>Database</dt>
              <dd>{health.data.database}</dd>
              <dt>API version</dt>
              <dd>{health.data.version}</dd>
              <dt>Engine version</dt>
              <dd>{health.data.engine_version}</dd>
              <dt>Environment</dt>
              <dd>{health.data.environment}</dd>
            </dl>
          )}
          <p className="muted">
            Foundation milestone (M0). Company workspaces and valuation features arrive in later
            milestones.
          </p>
        </section>
      </main>
    </div>
  );
}
