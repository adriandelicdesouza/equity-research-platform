import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/endpoints";
import type { CompanyDetailOut } from "../../api/schema.gen";
import { valuationApi } from "../../api/valuation";
import { useIdempotency } from "../../components/idempotency";
import { Badge, DateTime, Empty, ErrorMessage, GuardedButton, Loading, Notice, Pager } from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { PERM, useSession } from "../../session";

const LIMIT = 50;

/** Valuation projects: all (top-level page) or one company's (company workspace tab). */
export function ProjectsPage({ route, company }: { route: Route; company?: CompanyDetailOut }) {
  const { can, whyNot } = useSession();
  const status = route.query.get("status") ?? "active";
  const mine = route.query.get("owner") === "me";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const [creating, setCreating] = useState(false);
  const projects = useQuery({
    queryKey: ["valuation", "projects", company?.id ?? null, status, mine, offset],
    queryFn: () =>
      valuationApi.projects({
        company_id: company?.id,
        status: status === "all" ? undefined : status,
        owner: mine ? "me" : undefined,
        limit: LIMIT,
        offset,
      }),
  });

  if (!can(PERM.valuationRead)) return <Notice tone="warn">{whyNot(PERM.valuationRead)}</Notice>;

  return (
    <div className="stack">
      {!company && <h1>Valuation projects</h1>}
      <div className="toolbar">
        <label>
          Status{" "}
          <select value={status} onChange={(e) => navigate(withQuery(route, { status: e.target.value, offset: null }))}>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
            <option value="all">All</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={mine}
            onChange={(e) => navigate(withQuery(route, { owner: e.target.checked ? "me" : null, offset: null }))}
          />{" "}
          Only my projects
        </label>
        {company && (
          <GuardedButton
            disabledReason={can(PERM.valuationWrite) ? null : whyNot(PERM.valuationWrite)}
            onClick={() => setCreating(true)}
          >
            New valuation project
          </GuardedButton>
        )}
      </div>
      {!company && (
        <p className="small muted">Create a project from a company's Valuation tab, where its datasets are listed.</p>
      )}
      {creating && company && <CreateProject company={company} onCancel={() => setCreating(false)} />}
      {projects.isPending ? (
        <Loading label="Loading projects…" />
      ) : projects.isError ? (
        <ErrorMessage error={projects.error} />
      ) : projects.data.items.length === 0 ? (
        <Empty>No valuation projects{company ? " for this company" : ""} yet.</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Project</th>
                  {!company && <th scope="col">Company</th>}
                  <th scope="col">Status</th>
                  <th scope="col">Pinned dataset</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {projects.data.items.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">
                      <a href={href(["valuations", p.id])}>{p.name}</a>
                    </th>
                    {!company && (
                      <td>
                        <a href={href(["companies", p.company_id, "valuation"])}>{p.company_name}</a>
                      </td>
                    )}
                    <td>
                      <Badge value={p.status} tone={p.status === "active" ? "good" : "neutral"} />
                    </td>
                    <td>
                      v{p.dataset.version_no} {p.dataset.basis} <Badge value={p.dataset.status} />
                    </td>
                    <td>{p.owner.username}</td>
                    <td>
                      <DateTime value={p.created_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={projects.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        </>
      )}
    </div>
  );
}

function CreateProject({ company, onCancel }: { company: CompanyDetailOut; onCancel: () => void }) {
  const qc = useQueryClient();
  const idem = useIdempotency();
  const datasets = useQuery({
    queryKey: ["datasets", company.id, "valuation-pin"],
    queryFn: () => api.datasets(company.id, { limit: 100 }),
  });
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [listingId, setListingId] = useState(company.listings[0]?.id ?? "");
  const [thesis, setThesis] = useState("");
  const create = useMutation({
    mutationFn: () => {
      const body = {
        company_id: company.id,
        name: name.trim(),
        dataset_id: datasetId,
        listing_id: listingId || null,
        thesis: thesis.trim() || null,
      };
      return valuationApi.createProject(body, idem.keyFor(body));
    },
    onSuccess: (data) => {
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["valuation", "projects"] });
      navigate(href(["valuations", data.result.id]));
    },
  });
  const pinnable = (datasets.data?.items ?? []).filter((d) => d.status !== "discarded");
  const chosen = pinnable.find((d) => d.id === datasetId);
  return (
    <form
      className="card stack"
      aria-label="New valuation project"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h2>New valuation project</h2>
      <label>
        Name
        <input value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        Dataset version (pinned; runs never use another version)
        <select value={datasetId} required onChange={(e) => setDatasetId(e.target.value)}>
          <option value="">Choose a dataset…</option>
          {pinnable.map((d) => (
            <option key={d.id} value={d.id}>
              v{d.version_no} · {d.basis} · {d.status} · {d.currency}
            </option>
          ))}
        </select>
      </label>
      {chosen?.status === "draft" && (
        <Notice tone="warn">
          This dataset is a draft: previews work, but saved runs need a locked dataset (locking awaits P15 in the UI;
          the CLI is the owner's route). Rebase the project once a locked version exists.
        </Notice>
      )}
      <label>
        Default listing (for the market price)
        <select value={listingId} onChange={(e) => setListingId(e.target.value)}>
          <option value="">None</option>
          {company.listings.map((l) => (
            <option key={l.id} value={l.id}>
              {l.ticker} · {l.exchange_mic ?? l.exchange_name} · {l.trading_currency ?? "currency not declared"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Thesis (optional)
        <textarea value={thesis} maxLength={20000} rows={3} onChange={(e) => setThesis(e.target.value)} />
      </label>
      {datasets.isError && <ErrorMessage error={datasets.error} />}
      {create.isError && <ErrorMessage error={create.error} />}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={create.isPending || !name.trim() || !datasetId}>
          {create.isPending ? "Working…" : "Create project"}
        </button>
      </div>
    </form>
  );
}
