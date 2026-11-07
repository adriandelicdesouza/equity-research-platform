import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { api } from "../../api/endpoints";
import type { ActionRecordOut } from "../../api/schema.gen";
import {
  Badge,
  DateTime,
  Empty,
  ErrorMessage,
  JsonView,
  KV,
  Loading,
  Pager,
} from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { PERM, useSession } from "../../session";

const LIMIT = 50;

/** Workflow actions recorded in audit.api_actions (D60) and what their target refers to. */
export const ACTIONS: Record<string, { label: string; target: string }> = {
  "dataset.build": { label: "Build dataset", target: "company" },
  "dataset.rebuild": { label: "Rebuild draft", target: "company" },
  "dataset.verify": { label: "Verify dataset", target: "dataset" },
  "dataset.discard": { label: "Discard draft", target: "dataset" },
  "dataset.lock": { label: "Lock dataset", target: "dataset" },
  "conflict.resolve": { label: "Conflict decision", target: "conflict key" },
  "classification.confirm": { label: "Confirm classification", target: "company" },
  "listing.register": { label: "Register listing", target: "listing" },
  "listing.currency": { label: "Declare trading currency", target: "listing" },
  "import.statements": { label: "Ingest statement file", target: "file SHA-256" },
  "import.market": { label: "Ingest market-data file", target: "file SHA-256" },
  "import.reference": { label: "Ingest reference file", target: "file SHA-256" },
};

export function ActionHistory({ actionId, route }: { actionId: string | null; route: Route }) {
  return (
    <div className="stack">
      <h1>Action history</h1>
      {actionId ? <ActionDetail actionId={actionId} /> : <ActionList route={route} />}
    </div>
  );
}

function ActionList({ route }: { route: Route }) {
  const session = useSession();
  const action = route.query.get("action") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const list = useQuery({
    queryKey: ["actions", action, offset],
    queryFn: () => api.actions({ action: action || undefined, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <p className="muted">
        Your own workflow actions, newest first — successes and refusals. Read-only previews are not
        recorded. {session.can(PERM.userManage) ? "As an administrator you can also open any action by its id." : ""}
      </p>
      {session.can(PERM.userManage) && <LookupById />}
      <section className="card">
        <div className="control-row">
          <label>
            Action
            <select
              value={action}
              onChange={(e) => navigate(withQuery(route, { action: e.target.value, offset: null }))}
            >
              <option value="">All</option>
              {Object.entries(ACTIONS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {list.isPending && <Loading />}
        {list.isError && <ErrorMessage error={list.error} />}
        {list.data && list.data.items.length === 0 && (
          <Empty>You have not performed any {action ? "such " : ""}workflow action yet.</Empty>
        )}
        {list.data && list.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Action</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Target</th>
                  <th scope="col">Detail</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <DateTime value={a.created_at} />
                    </td>
                    <th scope="row">
                      <a href={href(["actions", a.id])}>{ACTIONS[a.action]?.label ?? a.action}</a>
                      <div className="small muted code">{a.action}</div>
                    </th>
                    <td>
                      <Badge value={a.outcome} /> <span className="small muted">HTTP {a.status_code}</span>
                    </td>
                    <td className="small">
                      <TargetText a={a} />
                    </td>
                    <td className="small">{errorOf(a)?.message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list.data && (
          <Pager
            total={list.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        )}
      </section>
    </>
  );
}

function LookupById() {
  const [id, setId] = useState("");
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id.trim());
  return (
    <form
      className="card searchbar"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) navigate(href(["actions", id.trim()]));
      }}
    >
      <label className="grow">
        Open an action by id (administrators)
        <input value={id} onChange={(e) => setId(e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" />
      </label>
      <button type="submit" disabled={!valid}>
        Open
      </button>
    </form>
  );
}

function errorOf(a: ActionRecordOut): { code?: string; message?: string } | null {
  const e = a.response["error"];
  return e && typeof e === "object" ? (e as { code?: string; message?: string }) : null;
}

function TargetText({ a }: { a: ActionRecordOut }) {
  const kind = ACTIONS[a.action]?.target ?? "target";
  if (!a.target) return <span className="muted">—</span>;
  if (kind === "company") {
    return (
      <>
        company <a href={href(["companies", a.target])}>{a.target.slice(0, 8)}…</a>
      </>
    );
  }
  return (
    <>
      {kind} <code title={a.target}>{a.target.length > 16 ? `${a.target.slice(0, 16)}…` : a.target}</code>
    </>
  );
}

function ActionDetail({ actionId }: { actionId: string }) {
  const session = useSession();
  const q = useQuery({ queryKey: ["action", actionId], queryFn: () => api.action(actionId) });
  if (q.isPending) return <Loading />;
  if (q.isError)
    return (
      <>
        <ErrorMessage error={q.error} />
        <p className="small muted">
          Actions are visible to the user who performed them
          {session.can(PERM.userManage) ? " and to administrators" : " (and to administrators)"}.
        </p>
        <a href={href(["actions"])}>Back to your actions</a>
      </>
    );
  const a = q.data;
  const err = errorOf(a);
  const result = a.response["result"] as Record<string, unknown> | undefined;
  const ds = (result?.["dataset"] ?? null) as { id?: string; company_id?: string } | null;
  return (
    <section className="card">
      <nav className="small">
        <a href={href(["actions"])}>← Your actions</a>
      </nav>
      <h2>{ACTIONS[a.action]?.label ?? a.action}</h2>
      <KV
        items={[
          ["Action", <code key="a">{a.action}</code>],
          ["Outcome", <span key="o"><Badge value={a.outcome} /> HTTP {a.status_code}</span>],
          ["When", <DateTime key="t" value={a.created_at} />],
          ["Target", <TargetText key="g" a={a} />],
          [
            "Acting user",
            "Stored with the action but not returned by the API. Your action list contains only your own actions; administrators can open other users' actions by id.",
          ],
          ["Idempotency key", a.idempotency_key ? <code key="k">{a.idempotency_key}</code> : null],
          ["Action id", <code key="i">{a.id}</code>],
          ...(err
            ? [
                ["Refusal code", <code key="c">{err.code}</code>] as [string, ReactNode],
                ["Reason given", err.message ?? ""] as [string, ReactNode],
              ]
            : []),
          ...(ds?.id && ds.company_id
            ? [
                [
                  "Dataset",
                  <a key="d" href={href(["companies", ds.company_id, "datasets"], { dataset: ds.id })}>
                    Open the dataset
                  </a>,
                ] as [string, ReactNode],
              ]
            : []),
        ]}
      />
      <h3>Stored response</h3>
      <JsonView value={a.response} />
    </section>
  );
}
