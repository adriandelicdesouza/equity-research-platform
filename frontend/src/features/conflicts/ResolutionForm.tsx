import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/endpoints";
import type { ConflictOut, DatasetOut, ResolutionRequest } from "../../api/schema.gen";
import { useIdempotency } from "../../components/idempotency";
import { Dialog, ErrorMessage, Notice } from "../../components/ui";
import { formatExact, isDecimalString } from "../../lib/decimal";
import { href } from "../../lib/router";

type Action = ResolutionRequest["action"];
const SOURCES = new Set(["reported_fact", "import_value"]);

const ACTIONS: { value: Action; label: string; help: string }[] = [
  {
    value: "select_candidate",
    label: "Select a candidate",
    help: "Use one of the candidate values above.",
  },
  {
    value: "enter_value",
    label: "Enter a value with its source",
    help: "An exact amount from a cited source (filing, page, note).",
  },
  {
    value: "override",
    label: "Documented override",
    help: "Proceed without resolving; the override and its reason are shown wherever the dataset is used.",
  },
  {
    value: "revoke",
    label: "Revoke the active decision",
    help: "Appends a revocation; the conflict returns to its undecided state at the next rebuild.",
  },
];

export const MIN_REASON = 10;

export function ResolutionForm({
  dataset,
  conflict,
  companyId,
  datasetsHref,
}: {
  dataset: DatasetOut;
  conflict: ConflictOut;
  companyId: string;
  datasetsHref: string;
}) {
  const qc = useQueryClient();
  const [action, setAction] = useState<Action>("select_candidate");
  const [candidate, setCandidate] = useState<string>("");
  const [value, setValue] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const idem = useIdempotency();

  const cand = conflict.candidates.find((c, i) => `${c.source}:${c.candidate_id ?? i}` === candidate);
  const hasActive = conflict.resolution !== null;

  const problems: string[] = [];
  if (action === "select_candidate" && (!cand || cand.candidate_id === null))
    problems.push("Choose a candidate.");
  if (action === "enter_value") {
    if (!isDecimalString(value.trim())) problems.push("Enter the value as a plain decimal number (e.g. -1234.5).");
    if (!sourceRef.trim()) problems.push("Cite the source of the entered value.");
  }
  if (action === "revoke" && !hasActive) problems.push("There is no active decision to revoke.");
  if (reason.trim().length < MIN_REASON) problems.push(`Give a reason of at least ${MIN_REASON} characters.`);

  const body: ResolutionRequest = { action, reason: reason.trim() };
  if (action === "select_candidate" && cand?.candidate_id != null) {
    body.candidate_id = cand.candidate_id;
    if (SOURCES.has(cand.source)) body.candidate_source = cand.source as ResolutionRequest["candidate_source"];
  }
  if (action === "enter_value") {
    body.value = value.trim(); // sent as a string: exact decimal, never a float
    body.source_reference = sourceRef.trim();
  }

  const submit = useMutation({
    mutationFn: () =>
      api.resolveConflict(
        dataset.id,
        conflict.conflict_key,
        body,
        idem.keyFor([dataset.id, conflict.conflict_key, body]),
      ),
    onSuccess: () => {
      setConfirming(false);
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["conflicts", dataset.id] });
      void qc.invalidateQueries({ queryKey: ["resolutions", companyId] });
      void qc.invalidateQueries({ queryKey: ["actions"] });
    },
    onError: () => setConfirming(false),
  });

  return (
    <form
      className="form"
      aria-labelledby="resolve-title"
      onSubmit={(e) => {
        e.preventDefault();
        if (problems.length === 0) {
          submit.reset();
          setConfirming(true);
        }
      }}
    >
      <h3 id="resolve-title">Record a decision</h3>
      <fieldset>
        <legend>Decision</legend>
        {ACTIONS.map((a) => (
          <label key={a.value} className="checkbox">
            <input
              type="radio"
              name="action"
              value={a.value}
              checked={action === a.value}
              disabled={a.value === "revoke" && !hasActive}
              onChange={() => setAction(a.value)}
            />
            <span>
              {a.label}
              <span className="small muted"> — {a.value === "revoke" && !hasActive ? "nothing active to revoke" : a.help}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {action === "select_candidate" && (
        <fieldset>
          <legend>Candidate</legend>
          {conflict.candidates.map((c, i) => {
            const id = `${c.source}:${c.candidate_id ?? i}`;
            return (
              <label key={id} className="checkbox">
                <input
                  type="radio"
                  name="candidate"
                  value={id}
                  checked={candidate === id}
                  disabled={c.candidate_id === null}
                  onChange={() => setCandidate(id)}
                />
                <span>
                  #{c.candidate_id ?? "—"} · <span className="mono">{c.value !== null ? formatExact(c.value) : "no value"}</span>{" "}
                  <span className="small muted">
                    {[c.role, c.form, c.filed, c.accession].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </label>
            );
          })}
        </fieldset>
      )}

      {action === "enter_value" && (
        <div className="form-row">
          <label className="grow">
            Exact value ({dataset.currency}, in units)
            <input
              inputMode="decimal"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. 162000000"
            />
          </label>
          <label className="grow">
            Source reference
            <input
              value={sourceRef}
              onChange={(e) => setSourceRef(e.target.value)}
              placeholder="e.g. 10-K/A 2025, p. 54, note 3"
            />
          </label>
        </div>
      )}

      <label>
        Reason (required, recorded in the audit trail)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>

      {problems.length > 0 && (reason || candidate || value) && (
        <ul className="small muted">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="form-actions">
        <button type="submit" disabled={problems.length > 0 || submit.isPending}>
          Review decision…
        </button>
      </div>

      {submit.isError && <ErrorMessage error={submit.error} />}
      {submit.isSuccess && (
        <Notice tone="success">
          Decision recorded (action{" "}
          <a href={href(["actions", submit.data.action_id])}>{submit.data.action_id}</a>).{" "}
          {String((submit.data.result as { note?: string }).note ?? "")}{" "}
          <a href={datasetsHref}>Rebuild the draft</a> to apply it.
        </Notice>
      )}

      {confirming && (
        <Dialog title="Confirm the decision" onClose={() => setConfirming(false)}>
          <p>
            This appends a <strong>{ACTIONS.find((a) => a.value === action)?.label.toLowerCase()}</strong> to
            conflict <code>{conflict.conflict_key}</code> ({conflict.line_item_code}). Earlier decisions
            and the dataset are not changed; the decision applies at the next rebuild of the draft.
          </p>
          {action === "select_candidate" && cand && (
            <p>
              Candidate #{cand.candidate_id}: <span className="mono">{cand.value !== null ? formatExact(cand.value) : "no value"}</span>
            </p>
          )}
          {action === "enter_value" && (
            <p>
              Value <span className="mono">{formatExact(value.trim())}</span> — source: {sourceRef.trim()}
            </p>
          )}
          <p className="small">Reason: {reason.trim()}</p>
          <div className="form-actions">
            <button type="button" className="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </button>
            <button type="button" disabled={submit.isPending} onClick={() => submit.mutate()}>
              {submit.isPending ? "Recording…" : "Record decision"}
            </button>
          </div>
        </Dialog>
      )}
    </form>
  );
}
