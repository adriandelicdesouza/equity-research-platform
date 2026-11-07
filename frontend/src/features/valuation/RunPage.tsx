import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ProjectDetailOut, RunOut } from "../../api/schema.gen";
import { valuationApi } from "../../api/valuation";
import { Badge, DateTime, ErrorMessage, Hash, KV, Loading, Notice } from "../../components/ui";
import { href } from "../../lib/router";
import { DcfOutputs, type Issue, IssueList, type Json, ValidationList } from "./common";
import { SensitivityPanel } from "./SensitivityPanel";

type Section = "outputs" | "inputs" | "trace" | "sensitivity";

/** One immutable run: outputs, decisions, inputs with provenance, trace inspector, reproduction. */
export function RunPage({ runId, project }: { runId: string; project: ProjectDetailOut }) {
  const run = useQuery({ queryKey: ["valuation", "run", runId], queryFn: () => valuationApi.run(runId) });
  const [section, setSection] = useState<Section>("outputs");
  if (run.isPending) return <Loading label="Loading run…" />;
  if (run.isError) return <ErrorMessage error={run.error} />;
  const r = run.data;
  return (
    <div className="stack">
      <nav aria-label="Breadcrumb" className="small">
        <a href={href(["valuations", project.id, "runs"])}>Saved runs</a> / {r.label ?? r.id}
      </nav>
      <section className="card">
        <h2>
          Run {r.label ? `“${r.label}”` : r.id.slice(0, 8)} <Badge value="immutable" tone="good" />
        </h2>
        <KV
          items={[
            ["Methodology", `${r.methodology} (method ${r.method_version}; produces ${r.produces.replaceAll("_", " ")})`],
            ["Dataset", `v${r.dataset_version_no} ${r.dataset_basis}`],
            ["Dataset content hash", <Hash key="d" value={r.dataset_content_sha256} />],
            ["Balance-sheet date → valuation date", `${r.balance_sheet_date} → ${r.valuation_date}`],
            ["Classification", r.classification.replaceAll("_", " ")],
            ["Scenario revision", String(r.scenario_revision_no)],
            ["Engine", `${r.engine_version} · ${Object.entries(r.module_versions).map(([k, v]) => `${k} ${v}`).join(", ")}`],
            ["Input hash", <Hash key="i" value={r.input_sha256} />],
            ["Output hash", <Hash key="o" value={r.output_sha256} />],
            ["Created", <span key="c"><DateTime value={r.created_at} /> by {r.created_by.username}</span>],
          ]}
        />
        <IssueList issues={r.warnings as unknown as Issue[]} />
        <Decisions run={r} />
        <Reproduce runId={r.id} />
      </section>
      <nav aria-label="Run sections" className="tabs">
        {(["outputs", "inputs", "trace", "sensitivity"] as const).map((s) => (
          <button key={s} type="button" className="tab" aria-pressed={section === s} onClick={() => setSection(s)}>
            {s === "outputs" ? "Outputs" : s === "inputs" ? "Inputs & provenance" : s === "trace" ? "Trace inspector" : "Sensitivity"}
          </button>
        ))}
      </nav>
      {section === "outputs" ? (
        <section className="card stack">
          <DcfOutputs outputs={r.outputs as Json} />
          <h3>Validation</h3>
          <ValidationList checks={r.validation as Json[]} />
        </section>
      ) : section === "inputs" ? (
        <RunInputs runId={r.id} />
      ) : section === "trace" ? (
        <TraceInspector runId={r.id} />
      ) : (
        <SensitivityPanel run={r} project={project} />
      )}
    </div>
  );
}

function Decisions({ run }: { run: RunOut }) {
  if (run.decisions.length === 0) return <p className="small muted">No overrides, acknowledgements or administrator actions.</p>;
  return (
    <div>
      <h3>Recorded decisions</h3>
      <ul className="small" aria-label="Recorded decisions">
        {run.decisions.map((d, i) => (
          <li key={i}>
            <Badge value={d.kind} tone="warn" /> <code>{d.code}</code> — “{d.reason}” · {d.user.username} ·{" "}
            <DateTime value={d.created_at} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Reproduce({ runId }: { runId: string }) {
  const rep = useMutation({ mutationFn: () => valuationApi.reproduce(runId) });
  return (
    <div className="stack">
      <div className="form-actions">
        <button type="button" className="secondary" disabled={rep.isPending} onClick={() => rep.mutate()}>
          {rep.isPending ? "Re-executing…" : "Reproduce from stored inputs"}
        </button>
      </div>
      {rep.isError && <ErrorMessage error={rep.error} />}
      {rep.data &&
        (rep.data.matches ? (
          <Notice tone="success">
            Reproduced: the recalculated output hash equals the stored one (<Hash value={rep.data.stored_output_sha256} />).
            Only the stored input document was used — no current market or reference data.
          </Notice>
        ) : (
          <div className="alert alert-error" role="alert">
            <strong>Not reproduced.</strong>{" "}
            {rep.data.same_engine
              ? "Same engine version but a different output: this is a defect to investigate."
              : `Engine changed (${String(rep.data.stored_engine.version)} → ${String(rep.data.current_engine.version)}); differences:`}
            <ul className="small">
              {rep.data.differences.map((d, i) => (
                <li key={i}>
                  <code>{String(d.path)}</code>: {JSON.stringify(d.stored)} → {JSON.stringify(d.recomputed)}
                </li>
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}

function RunInputs({ runId }: { runId: string }) {
  const inputs = useQuery({ queryKey: ["valuation", "run-inputs", runId], queryFn: () => valuationApi.runInputs(runId) });
  if (inputs.isPending) return <Loading label="Loading inputs…" />;
  if (inputs.isError) return <ErrorMessage error={inputs.error} />;
  const doc = inputs.data.input_document;
  return (
    <section className="card stack" aria-label="Inputs and provenance">
      <p className="small muted">
        The stored input document (hash <Hash value={inputs.data.input_sha256} />) is what the run used and what
        reproduction re-executes. Values are exact; ratios are fractions.
      </p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Input</th>
              <th scope="col" className="num">
                Value
              </th>
              <th scope="col">Unit</th>
              <th scope="col">Provenance</th>
              <th scope="col">Source</th>
              <th scope="col">Published → conversion</th>
              <th scope="col">As of</th>
            </tr>
          </thead>
          <tbody>
            {inputs.data.lineage.map((l) => (
              <tr key={l.input_key}>
                <th scope="row">
                  <code>{l.input_key}</code>
                </th>
                <td className="num">{l.value ?? (l.json !== null && l.json !== undefined ? JSON.stringify(l.json) : "—")}</td>
                <td>{l.unit}</td>
                <td>{l.provenance_kind.replaceAll("_", " ")}</td>
                <td className="small">
                  {l.source_type} {l.source_ref && <code>{l.source_ref}</code>}
                </td>
                <td className="small">
                  {l.source_value !== null ? `${l.source_value} ${l.source_unit ?? ""}` : "—"}
                  {l.conversion ? ` → ${l.conversion}` : ""}
                </td>
                <td>
                  {l.as_of_date ?? "—"} {l.warnings.length > 0 && <Badge value={l.warnings.join(", ")} tone="warn" />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Full input document (JSON)</summary>
        <pre className="json">{JSON.stringify(doc, null, 2)}</pre>
      </details>
    </section>
  );
}

function TraceInspector({ runId }: { runId: string }) {
  const [node, setNode] = useState<string | null>(null);
  const trace = useQuery({
    queryKey: ["valuation", "trace", runId, node],
    queryFn: () => valuationApi.trace(runId, node ? { node, depth: 1 } : {}),
  });
  if (trace.isPending) return <Loading label="Loading trace…" />;
  if (trace.isError) return <ErrorMessage error={trace.error} />;
  const nodes = trace.data.nodes;
  const ids = Object.keys(nodes).sort();
  const focus = node ? (nodes[node] as Json | undefined) : undefined;
  return (
    <section className="card stack" aria-label="Trace inspector">
      <p className="small muted">
        Every important number is a trace node with its rule and inputs (engine and backend steps). Select a node to
        walk to its inputs; inputs that start with <code>assumption:</code>, <code>dataset:</code> or{" "}
        <code>market:</code> are listed on the Inputs tab with their sources.
      </p>
      {node && (
        <div>
          <button type="button" className="link" onClick={() => setNode(null)}>
            ← All nodes
          </button>
          {focus && (
            <div className="card" aria-label={`Trace node ${node}`}>
              <h3>
                {String(focus.label)} <code>{node}</code>
              </h3>
              <KV
                items={[
                  ["Value", <code key="v">{JSON.stringify(focus.value)}</code>],
                  ["Unit", String(focus.unit)],
                  ["Rule", String(focus.rule)],
                  ["Module", String(focus.module)],
                ]}
              />
              <h4>Inputs</h4>
              <ul>
                {((focus.inputs ?? []) as string[]).map((i) => (
                  <li key={i}>
                    {i in nodes ? (
                      <button type="button" className="link" onClick={() => setNode(i)}>
                        {i}
                      </button>
                    ) : (
                      <code>{i}</code>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {!node && (
        <ul className="trace-list">
          {ids.map((id) => {
            const n = nodes[id] as Json;
            return (
              <li key={id}>
                <button type="button" className="link" onClick={() => setNode(id)}>
                  {id}
                </button>{" "}
                <span className="small muted">
                  {String(n.label)} = {JSON.stringify(n.value)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
