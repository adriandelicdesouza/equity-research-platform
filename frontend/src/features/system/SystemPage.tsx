import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import { ErrorMessage, KV, Loading } from "../../components/ui";
import { PENDING_APPROVAL, useSession } from "../../session";

export function SystemPage() {
  const { me } = useSession();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  return (
    <div className="stack">
      <h1>System</h1>
      <section className="card">
        <h2>Service status</h2>
        {health.isPending && <Loading label="Checking…" />}
        {health.isError && <ErrorMessage error={health.error} />}
        {health.data && (
          <KV
            items={[
              ["Status", health.data.status],
              ["Database", health.data.database],
              ["API version", health.data.version],
              ["Engine version", health.data.engine_version],
              ["Environment", health.data.environment],
            ]}
          />
        )}
      </section>
      <section className="card">
        <h2>Your access</h2>
        <KV
          items={[
            ["User", `${me.user.display_name} (${me.user.username})`],
            ["Roles", me.user.roles.join(", ") || "none"],
            ["Permissions", me.permissions.join(", ") || "none"],
          ]}
        />
        <h3>Pending owner decisions</h3>
        <ul>
          {Object.entries(PENDING_APPROVAL).map(([perm, text]) => (
            <li key={perm}>
              <code>{perm}</code> — {me.permissions.includes(perm) ? "granted to you." : text}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
