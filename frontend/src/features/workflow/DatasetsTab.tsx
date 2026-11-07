import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { api } from "../../api/endpoints";
import type { ActionOut, CompanyDetailOut, DatasetDetailOut } from "../../api/schema.gen";
import { useIdempotency } from "../../components/idempotency";
import {
  Badge,
  DateTime,
  Dialog,
  Empty,
  ErrorMessage,
  GuardedButton,
  Hash,
  JsonView,
  KV,
  Loading,
  Notice,
} from "../../components/ui";
import { formatCounts } from "../../lib/format";
import { href, type Route } from "../../lib/router";
import { PERM, useSession } from "../../session";
import { DatasetPicker, datasetLabel, useSelectedDataset } from "../company/datasetSelection";

const MIN_REASON = 10;

export function DatasetsTab({ company, route }: { company: CompanyDetailOut; route: Route }) {
  const { datasets, items, selected, isDefault, missing } = useSelectedDataset(company.id, route);
  const detail = useQuery({
    queryKey: ["dataset", selected?.id],
    queryFn: () => (selected ? api.dataset(selected.id) : Promise.reject(new Error("no dataset"))),
    enabled: selected !== null,
  });
  return (
    <div className="stack">
      {datasets.isPending && <Loading label="Loading datasets…" />}
      {datasets.isError && <ErrorMessage error={datasets.error} />}
      {missing && <Notice tone="warn">The dataset in the link does not belong to this company.</Notice>}
      <BuildControls company={company} />
      {datasets.isSuccess && items.length === 0 && <Empty>No dataset has been built yet.</Empty>}
      {items.length > 0 && (
        <section className="card controls">
          <DatasetPicker route={route} items={items} selected={selected} isDefault={isDefault} />
        </section>
      )}
      {selected && detail.isPending && <Loading />}
      {detail.isError && <ErrorMessage error={detail.error} />}
      {detail.data && (
        <div className="grid-2">
          <DatasetSummary d={detail.data} />
          <LifecycleControls d={detail.data} companyId={company.id} />
        </div>
      )}
    </div>
  );
}

function DatasetSummary({ d }: { d: DatasetDetailOut }) {
  return (
    <section className="card">
      <h2>{datasetLabel(d)}</h2>
      <KV
        items={[
          ["Status", <Badge key="s" value={d.status} />],
          ["Status reason", d.status_reason],
          ["Basis", d.basis === "standardized" ? "standardized (SEC filings)" : "reported (imported statements)"],
          ["Version", String(d.version_no)],
          ["Currency", d.currency],
          ["Mapping / taxonomy", `${d.mapping_version} · ${d.taxonomy}`],
          ["Selection policy", d.selection_policy],
          ["Facts as of", d.facts_as_of],
          ["Interim coverage", d.interim_coverage],
          ["Values", String(d.value_count)],
          ["Checks", formatCounts(d.check_counts)],
          ["Conflicts", formatCounts(d.conflict_counts)],
          [
            "Open material conflicts",
            d.open_material_conflicts > 0 ? <Badge key="c" value={String(d.open_material_conflicts)} tone="bad" /> : "0",
          ],
          ["Content SHA-256", <Hash key="h" value={d.content_sha256} length={16} />],
          ["Input SHA-256", <Hash key="i" value={d.input_sha256} length={16} />],
          ["Created", <DateTime key="c" value={d.created_at} />],
          ["Locked", <DateTime key="l" value={d.locked_at} />],
          ["Discarded", <DateTime key="x" value={d.discarded_at} />],
        ]}
      />
      <details>
        <summary>Build summary (source_summary)</summary>
        <JsonView value={d.source_summary} />
      </details>
    </section>
  );
}

// ------------------------------------------------------------------ lifecycle (selected dataset)

type Pending = "verify" | "discard" | "lock" | null;

function LifecycleControls({ d, companyId }: { d: DatasetDetailOut; companyId: string }) {
  const session = useSession();
  const qc = useQueryClient();
  const idem = useIdempotency();
  const [dialog, setDialog] = useState<Pending>(null);
  const [reason, setReason] = useState("");

  const run = useMutation({
    mutationFn: (kind: Exclude<Pending, null>) => {
      const key = idem.keyFor([kind, d.id, kind === "verify" ? null : reason.trim()]);
      if (kind === "verify") return api.verifyDataset(d.id, key);
      if (kind === "discard") return api.discardDataset(d.id, reason.trim(), key);
      return api.lockDataset(d.id, reason.trim(), key);
    },
    onSuccess: () => {
      setDialog(null);
      setReason("");
      idem.reset();
      invalidateCompany(qc, companyId, d.id);
    },
    onError: () => setDialog(null),
  });

  const isDraft = d.status === "draft";
  const discardWhy = !session.can(PERM.write)
    ? session.whyNot(PERM.write)
    : !isDraft
      ? `Only drafts can be discarded; this version is ${d.status}.`
      : null;
  const lockWhy = !session.can(PERM.lock)
    ? session.whyNot(PERM.lock)
    : !isDraft
      ? `Only drafts can be locked; this version is ${d.status}.`
      : d.open_material_conflicts > 0
        ? `${d.open_material_conflicts} open material conflict(s) must be decided and the draft rebuilt first.`
        : null;
  const verifyWhy = !session.can(PERM.read) ? session.whyNot(PERM.read) : null;

  return (
    <section className="card stack">
      <h2>Workflow actions</h2>
      <div>
        <GuardedButton disabledReason={verifyWhy} className="secondary" onClick={() => setDialog("verify")}>
          Verify…
        </GuardedButton>
        <p className="small muted">
          Re-serializes the stored rows and rebuilds from the original sources; both must reproduce the
          stored hashes. Writes nothing to the dataset (the action is logged). Takes a few seconds.
        </p>
      </div>
      <div>
        <GuardedButton disabledReason={discardWhy} className="danger" onClick={() => setDialog("discard")}>
          Discard draft…
        </GuardedButton>
        <p className="small muted">Marks this draft discarded (it stays stored and auditable).</p>
      </div>
      <div>
        <GuardedButton disabledReason={lockWhy} onClick={() => setDialog("lock")}>
          Lock…
        </GuardedButton>
        <p className="small muted">
          Locking makes a verified draft the immutable version valuations use. The backend refuses it
          while material conflicts are open, values are ambiguous or verification fails.
        </p>
      </div>
      {run.isError && <ErrorMessage error={run.error} />}
      {run.isSuccess && <ActionResult out={run.data} />}

      {dialog === "verify" && (
        <Dialog title="Verify this dataset" onClose={() => setDialog(null)}>
          <p>
            Verify <strong>{datasetLabel(d)}</strong>. Nothing in the dataset changes; the check is
            recorded in your action history.
          </p>
          <DialogButtons pending={run.isPending} label="Verify" onCancel={() => setDialog(null)} onConfirm={() => run.mutate("verify")} />
        </Dialog>
      )}
      {(dialog === "discard" || dialog === "lock") && (
        <Dialog title={dialog === "discard" ? "Discard this draft" : "Lock this dataset"} onClose={() => setDialog(null)}>
          <p>
            {dialog === "discard" ? (
              <>
                Discard <strong>{datasetLabel(d)}</strong>. It can no longer be locked or used; the next
                build creates a new version.
              </>
            ) : (
              <>
                Lock <strong>{datasetLabel(d)}</strong>. A locked version cannot be changed.
              </>
            )}
          </p>
          <label>
            Reason (at least {MIN_REASON} characters; recorded in the audit trail)
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <DialogButtons
            pending={run.isPending}
            label={dialog === "discard" ? "Discard draft" : "Lock"}
            danger={dialog === "discard"}
            disabled={reason.trim().length < MIN_REASON}
            onCancel={() => setDialog(null)}
            onConfirm={() => run.mutate(dialog)}
          />
        </Dialog>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ build / rebuild (company)

function BuildControls({ company }: { company: CompanyDetailOut }) {
  const session = useSession();
  const qc = useQueryClient();
  const idem = useIdempotency();
  const [dialog, setDialog] = useState<"build" | "rebuild" | null>(null);
  const [basis, setBasis] = useState<"standardized" | "reported">("standardized");
  const [policy, setPolicy] = useState<"latest_reported" | "first_reported">("latest_reported");
  const [factsAsOf, setFactsAsOf] = useState("");
  const [reason, setReason] = useState("");

  const run = useMutation({
    mutationFn: (kind: "build" | "rebuild") => {
      if (kind === "build") {
        const body = {
          basis,
          policy,
          facts_as_of: basis === "standardized" && factsAsOf ? factsAsOf : null,
        };
        return api.buildDataset(company.id, body, idem.keyFor(["build", company.id, body]));
      }
      const body = { basis, reason: reason.trim() };
      return api.rebuildDraft(company.id, body, idem.keyFor(["rebuild", company.id, body]));
    },
    onSuccess: () => {
      setDialog(null);
      setReason("");
      idem.reset();
      invalidateCompany(qc, company.id);
    },
    onError: () => setDialog(null),
  });
  const why = session.can(PERM.write) ? null : session.whyNot(PERM.write);

  return (
    <section className="card stack">
      <h2>Build a dataset version</h2>
      <div className="form-row">
        <label>
          Basis
          <select value={basis} onChange={(e) => setBasis(e.target.value as typeof basis)} disabled={why !== null}>
            <option value="standardized">standardized (SEC filings)</option>
            <option value="reported">reported (imported statements)</option>
          </select>
        </label>
        <label>
          Selection policy (build)
          <select value={policy} onChange={(e) => setPolicy(e.target.value as typeof policy)} disabled={why !== null}>
            <option value="latest_reported">latest reported</option>
            <option value="first_reported">first reported</option>
          </select>
        </label>
        {basis === "standardized" && (
          <label>
            SEC facts as of (build; optional)
            <input type="date" value={factsAsOf} onChange={(e) => setFactsAsOf(e.target.value)} disabled={why !== null} />
          </label>
        )}
      </div>
      <div className="form-row">
        <GuardedButton disabledReason={why} onClick={() => setDialog("build")}>
          Build…
        </GuardedButton>
        <GuardedButton disabledReason={why} className="secondary" onClick={() => setDialog("rebuild")}>
          Rebuild draft…
        </GuardedButton>
      </div>
      <p className="small muted">
        <strong>Build</strong> creates the next version from the stored sources (nothing is stored if
        it would be identical to the current version). <strong>Rebuild draft</strong> discards the open
        draft of the chosen basis and builds the next one, applying recorded conflict decisions; locked
        versions are never touched.
      </p>
      {run.isError && <ErrorMessage error={run.error} />}
      {run.isSuccess && <ActionResult out={run.data} companyId={company.id} />}
      {dialog === "build" && (
        <Dialog title="Build a dataset version" onClose={() => setDialog(null)}>
          <p>
            Build a <strong>{basis}</strong> dataset for {company.common_name ?? company.legal_name} with
            the {policy.replace("_", " ")} policy
            {basis === "standardized" && factsAsOf ? `, SEC facts as of ${factsAsOf}` : ""}.
          </p>
          <DialogButtons pending={run.isPending} label="Build" onCancel={() => setDialog(null)} onConfirm={() => run.mutate("build")} />
        </Dialog>
      )}
      {dialog === "rebuild" && (
        <Dialog title="Rebuild the draft" onClose={() => setDialog(null)}>
          <p>
            Discard the open <strong>{basis}</strong> draft and build the next version with the recorded
            decisions.
          </p>
          <label>
            Reason (at least {MIN_REASON} characters)
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <DialogButtons
            pending={run.isPending}
            label="Rebuild draft"
            disabled={reason.trim().length < MIN_REASON}
            onCancel={() => setDialog(null)}
            onConfirm={() => run.mutate("rebuild")}
          />
        </Dialog>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ shared

function invalidateCompany(qc: ReturnType<typeof useQueryClient>, companyId: string, datasetId?: string) {
  void qc.invalidateQueries({ queryKey: ["datasets", companyId] });
  if (datasetId) void qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
  void qc.invalidateQueries({ queryKey: ["dataset"] });
  void qc.invalidateQueries({ queryKey: ["integrity"] });
  void qc.invalidateQueries({ queryKey: ["actions"] });
}

function DialogButtons({
  pending,
  label,
  danger,
  disabled,
  onCancel,
  onConfirm,
}: {
  pending: boolean;
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="form-actions">
      <button type="button" className="secondary" onClick={onCancel}>
        Cancel
      </button>
      <button type="button" className={danger ? "danger" : undefined} disabled={pending || disabled} onClick={onConfirm}>
        {pending ? "Working…" : label}
      </button>
    </div>
  );
}

/** The outcome of a dataset action, with a link to its record and to any new version. */
export function ActionResult({ out, companyId }: { out: ActionOut; companyId?: string }) {
  const r = out.result as Record<string, unknown>;
  const ds = r["dataset"] as { id: string; version_no: number; status: string; basis: string; company_id: string } | undefined;
  const lines: ReactNode[] = [];
  if (out.action === "dataset.verify") {
    lines.push(
      <span key="v">
        Verification <strong>{r["ok"] ? "passed" : "FAILED"}</strong> — stored rows match:{" "}
        {String(r["stored_matches"])}, validation matches: {String(r["validation_matches"])}, rebuild
        matches: {String(r["rebuild_matches"])}, inputs match: {String(r["inputs_match"])}, mapping
        matches: {String(r["mapping_matches"])}.
      </span>,
    );
  }
  if (ds) {
    lines.push(
      <span key="d">
        {r["created"] === false ? "Identical to the current version; nothing was stored. " : ""}
        {ds.basis} v{ds.version_no} is now <strong>{ds.status}</strong>.{" "}
        <a href={href(["companies", companyId ?? ds.company_id, "datasets"], { dataset: ds.id })}>Open it</a>
      </span>,
    );
  }
  return (
    <Notice tone={out.action === "dataset.verify" && !r["ok"] ? "warn" : "success"}>
      {lines}{" "}
      <span className="small">
        (action <a href={href(["actions", out.action_id])}>{out.action_id}</a>)
      </span>
    </Notice>
  );
}
