// Scenarios (Phase 5b): bull/bear/custom with inheritance, archive/restore, probability
// weights and the probability-weighted summary. Every number shown comes from the server: the
// weighted value and the probability sum are computed in Python (D25); the browser only sends
// the decimal strings the analyst typed.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ProjectDetailOut, ScenarioOut } from "../../api/schema.gen";
import { METHODOLOGIES, type Methodology, valuationApi } from "../../api/valuation";
import { useIdempotency } from "../../components/idempotency";
import {
  Badge,
  Dialog,
  DialogButtons,
  ErrorMessage,
  GuardedButton,
  KV,
  Loading,
  Notice,
} from "../../components/ui";
import { isDecimalString } from "../../lib/decimal";
import { href } from "../../lib/router";
import { Money, Pct, PerShare, scenarioName } from "./common";
import { useEditRights } from "./ProjectPage";

type Open =
  | { kind: "create" }
  | { kind: "edit"; scenario: ScenarioOut }
  | { kind: "archive" | "restore"; scenario: ScenarioOut }
  | null;

export function ScenariosPanel({ project }: { project: ProjectDetailOut }) {
  const rights = useEditRights(project);
  const [open, setOpen] = useState<Open>(null);
  const [method, setMethod] = useState<Methodology>("dcf_fcff");
  const summary = useQuery({
    queryKey: ["valuation", "scenario-summary", project.id, method],
    queryFn: () => valuationApi.scenarioSummary(project.id, method),
  });
  const runOf = new Map((summary.data?.scenarios ?? []).map((s) => [s.scenario.id, s.run]));
  return (
    <div className="stack">
      {!rights.canEdit && <Notice tone="warn">Read-only: {rights.reason}</Notice>}
      <section className="card" aria-label="Scenarios">
        <h2>Scenarios</h2>
        <p className="small muted">
          A scenario inherits every assumption of its parent unless it sets or clears the key itself. Runs record the
          inherited values and the parent chain in their input document.
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col">Kind</th>
                <th scope="col">Inherits from</th>
                <th scope="col" className="num">
                  Probability
                </th>
                <th scope="col" className="num">
                  Latest {method} per share
                </th>
                <th scope="col">Revision</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {project.scenarios.map((s) => {
                const run = runOf.get(s.id);
                return (
                  <tr key={s.id}>
                    <th scope="row">
                      {s.name} {s.status !== "active" && <Badge value="archived" tone="neutral" />}
                    </th>
                    <td>{s.kind}</td>
                    <td>{s.parent_scenario_id ? scenarioName(project, s.parent_scenario_id) : <span className="muted">—</span>}</td>
                    <td className="num">{s.probability ? <Pct value={s.probability} /> : <span className="muted">—</span>}</td>
                    <td className="num">
                      {run ? (
                        <a href={href(["valuations", project.id, "runs", run.id])}>
                          <PerShare value={run.per_share_value} currency={run.currency} />
                        </a>
                      ) : (
                        <span className="muted">no run</span>
                      )}
                    </td>
                    <td>{s.revision_no}</td>
                    <td>
                      <a href={href(["valuations", project.id, "assumptions"], { scenario: s.id })}>assumptions</a> ·{" "}
                      <a href={href(["valuations", project.id, "run"], { scenario: s.id })}>run</a>
                      {s.status === "active" && (
                        <>
                          {" · "}
                          <GuardedButton className="link" disabledReason={rights.reason} onClick={() => setOpen({ kind: "edit", scenario: s })}>
                            edit
                          </GuardedButton>
                        </>
                      )}
                      {s.kind !== "base" && (
                        <>
                          {" · "}
                          <GuardedButton
                            className="link"
                            disabledReason={rights.reason}
                            onClick={() => setOpen({ kind: s.status === "active" ? "archive" : "restore", scenario: s })}
                          >
                            {s.status === "active" ? "archive" : "restore"}
                          </GuardedButton>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="form-actions">
          <GuardedButton disabledReason={rights.reason} onClick={() => setOpen({ kind: "create" })}>
            Add scenario…
          </GuardedButton>
        </div>
      </section>
      <Probabilities project={project} disabledReason={rights.reason} needsAdminReason={rights.needsAdminReason} />
      <section className="card" aria-label="Probability-weighted value">
        <h2>Probability-weighted value</h2>
        <label>
          Methodology{" "}
          <select value={method} onChange={(e) => setMethod(e.target.value as Methodology)}>
            {METHODOLOGIES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        {summary.isPending ? (
          <Loading label="Loading summary…" />
        ) : summary.isError ? (
          <ErrorMessage error={summary.error} />
        ) : (
          <>
            {summary.data.complete ? (
              <Notice tone="success">
                Weighted over each weighted scenario's latest {method} run (same valuation date, currency and dataset).
              </Notice>
            ) : (
              <Notice tone="warn">
                Not weighted yet:
                <ul>
                  {summary.data.issues.map((i) => (
                    <li key={i}>{i}</li>
                  ))}
                </ul>
              </Notice>
            )}
            <KV
              items={[
                ["Weighted value per share", <PerShare key="w" value={summary.data.weighted_per_share} />],
                ["Weighted primary value", <Money key="p" value={summary.data.weighted_primary_value} />],
                ["Probabilities sum", summary.data.probabilities_sum ?? "—"],
                [
                  "Range across scenarios (per share)",
                  <span key="r">
                    <PerShare value={summary.data.per_share_low} /> – <PerShare value={summary.data.per_share_high} />
                  </span>,
                ],
              ]}
            />
          </>
        )}
      </section>
      {open && (
        <ScenarioDialog open={open} project={project} needsAdminReason={rights.needsAdminReason} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

function Probabilities({
  project,
  disabledReason,
  needsAdminReason,
}: {
  project: ProjectDetailOut;
  disabledReason: string | null;
  needsAdminReason: boolean;
}) {
  const qc = useQueryClient();
  const idem = useIdempotency();
  const active = project.scenarios.filter((s) => s.status === "active");
  const [weights, setWeights] = useState<Record<string, string>>(
    Object.fromEntries(active.map((s) => [s.id, s.probability ?? ""])),
  );
  const [adminReason, setAdminReason] = useState("");
  const filled = Object.entries(weights).filter(([, v]) => v.trim() !== "");
  const invalid = filled.some(([, v]) => !isDecimalString(v.trim()));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        expected_revision: project.revision_no,
        weights: Object.fromEntries(filled.map(([k, v]) => [k, v.trim()])),
        ...(needsAdminReason ? { admin_reason: adminReason.trim() } : {}),
      };
      return valuationApi.setProbabilities(project.id, body, idem.keyFor(body));
    },
    onSuccess: () => {
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["valuation"] });
    },
  });
  return (
    <section className="card" aria-label="Scenario probabilities">
      <h2>Scenario probabilities</h2>
      <p className="small muted">
        Fractions (0.25 = 25%). Weighted scenarios must sum to exactly 1 — the server checks the exact sum; leave a
        scenario blank to exclude it. Saving with every field blank clears all weights.
      </p>
      <div className="form-grid">
        {active.map((s) => (
          <label key={s.id}>
            {s.name}
            <input
              aria-label={`Probability of ${s.name}`}
              value={weights[s.id] ?? ""}
              disabled={!!disabledReason}
              onChange={(e) => setWeights((w) => ({ ...w, [s.id]: e.target.value }))}
            />
          </label>
        ))}
      </div>
      {needsAdminReason && (
        <label>
          Administrator reason — you are not the owner
          <input value={adminReason} onChange={(e) => setAdminReason(e.target.value)} />
        </label>
      )}
      {invalid && <Notice tone="warn">Enter plain decimals such as 0.25.</Notice>}
      {save.isError && <ErrorMessage error={save.error} />}
      {save.isSuccess && <Notice tone="success">Probabilities saved.</Notice>}
      <div className="form-actions">
        <GuardedButton
          disabledReason={disabledReason ?? (invalid ? "Fix the values first." : null)}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : "Save probabilities"}
        </GuardedButton>
      </div>
    </section>
  );
}

function ScenarioDialog({
  open,
  project,
  needsAdminReason,
  onClose,
}: {
  open: Exclude<Open, null>;
  project: ProjectDetailOut;
  needsAdminReason: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const idem = useIdempotency();
  const editing = open.kind === "create" ? null : open.scenario;
  const base = project.scenarios.find((s) => s.kind === "base");
  const [name, setName] = useState(editing?.name ?? "");
  const [kind, setKind] = useState<"bull" | "bear" | "custom">("bull");
  const [parent, setParent] = useState(editing ? (editing.parent_scenario_id ?? "") : (base?.id ?? ""));
  const [reason, setReason] = useState("");
  const [adminReason, setAdminReason] = useState("");
  const admin = needsAdminReason ? { admin_reason: adminReason.trim() } : {};
  const parents = project.scenarios.filter((s) => s.status === "active" && s.id !== editing?.id);
  const action = useMutation({
    mutationFn: () => {
      if (open.kind === "create") {
        const body = { name: name.trim(), kind, parent_scenario_id: parent || null, ...admin };
        return valuationApi.createScenario(project.id, body, idem.keyFor(body));
      }
      if (open.kind === "edit") {
        const s = open.scenario;
        const body = {
          expected_revision: s.revision_no,
          ...(name.trim() !== s.name ? { name: name.trim() } : {}),
          ...(parent !== (s.parent_scenario_id ?? "")
            ? parent
              ? { parent_scenario_id: parent }
              : { clear_parent: true }
            : {}),
          reason: reason.trim() || null,
          ...admin,
        };
        return valuationApi.updateScenario(s.id, body, idem.keyFor(body));
      }
      const body = { expected_revision: open.scenario.revision_no, reason: reason.trim(), ...admin };
      return valuationApi.scenarioStatus(open.scenario.id, open.kind, body, idem.keyFor(body));
    },
    onSuccess: () => {
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["valuation"] });
      onClose();
    },
  });
  const title =
    open.kind === "create" ? "Add scenario" : open.kind === "edit" ? `Edit ${open.scenario.name}` : `${open.kind === "archive" ? "Archive" : "Restore"} ${open.scenario.name}`;
  const needsReason = open.kind === "archive" || open.kind === "restore";
  const changed = open.kind !== "edit" || name.trim() !== open.scenario.name || parent !== (open.scenario.parent_scenario_id ?? "");
  const ready =
    changed &&
    (open.kind === "archive" || open.kind === "restore" || name.trim().length > 0) &&
    (!needsReason || reason.trim().length >= 10) &&
    (!needsAdminReason || adminReason.trim().length >= 10);
  return (
    <Dialog title={title} onClose={onClose}>
      {(open.kind === "create" || open.kind === "edit") && (
        <>
          <label>
            Name
            <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          </label>
          {open.kind === "create" && (
            <label>
              Kind
              <select value={kind} onChange={(e) => setKind(e.target.value as "bull" | "bear" | "custom")}>
                <option value="bull">bull</option>
                <option value="bear">bear</option>
                <option value="custom">custom</option>
              </select>
            </label>
          )}
          {!(open.kind === "edit" && open.scenario.kind === "base") && (
            <label>
              Inherits from
              <select value={parent} onChange={(e) => setParent(e.target.value)}>
                <option value="">— no parent (all assumptions entered here)</option>
                {parents.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.kind})
                  </option>
                ))}
              </select>
            </label>
          )}
          {open.kind === "edit" && (
            <label>
              Reason (optional; recorded in the audit history)
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
          )}
        </>
      )}
      {needsReason && (
        <>
          {open.kind === "archive" && (
            <p className="small">Archived scenarios keep their runs and history but cannot be changed or run.</p>
          )}
          <label>
            Reason (at least 10 characters)
            <textarea value={reason} rows={2} onChange={(e) => setReason(e.target.value)} />
          </label>
        </>
      )}
      {needsAdminReason && (
        <label>
          Administrator reason — you are not the owner; this is recorded with the change
          <textarea value={adminReason} rows={2} onChange={(e) => setAdminReason(e.target.value)} />
        </label>
      )}
      {action.isError && <ErrorMessage error={action.error} />}
      <DialogButtons
        pending={action.isPending}
        label={title}
        danger={open.kind === "archive"}
        disabled={!ready}
        onCancel={onClose}
        onConfirm={() => action.mutate()}
      />
    </Dialog>
  );
}
