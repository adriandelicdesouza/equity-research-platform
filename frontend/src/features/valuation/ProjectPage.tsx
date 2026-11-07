import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ProjectDetailOut } from "../../api/schema.gen";
import { valuationApi } from "../../api/valuation";
import { useIdempotency } from "../../components/idempotency";
import {
  Badge,
  DateTime,
  Dialog,
  DialogButtons,
  ErrorMessage,
  GuardedButton,
  Hash,
  KV,
  Loading,
  Notice,
} from "../../components/ui";
import { href, type Route } from "../../lib/router";
import { PERM, useSession } from "../../session";
import { AssumptionsPanel } from "./AssumptionsPanel";
import { RunBuilder } from "./RunBuilder";
import { RunPage } from "./RunPage";
import { RunsList } from "./RunsList";

export const PROJECT_TABS = [
  { key: "overview", label: "Overview" },
  { key: "assumptions", label: "Assumptions" },
  { key: "run", label: "Inputs, preview & run" },
  { key: "runs", label: "Saved runs" },
] as const;

/** Who may change a project, as the UI explains it (the backend enforces it, Q3). */
export function useEditRights(project: ProjectDetailOut | undefined) {
  const { me, can, whyNot } = useSession();
  const isOwner = project ? project.owner.id === me.user.id : false;
  const isAdmin = me.user.roles.includes("admin");
  let reason: string | null = null;
  if (!can(PERM.valuationWrite)) reason = whyNot(PERM.valuationWrite);
  else if (project && project.status !== "active") reason = "The project is archived; restore it first.";
  else if (project && !isOwner && !isAdmin) reason = `Only the owner (${project.owner.username}) may change this project.`;
  return { canEdit: reason === null, reason, needsAdminReason: !!project && !isOwner && isAdmin, isOwner, isAdmin };
}

export function ProjectPage({ projectId, tab, rest, route }: { projectId: string; tab: string; rest: string[]; route: Route }) {
  const project = useQuery({ queryKey: ["valuation", "project", projectId], queryFn: () => valuationApi.project(projectId) });
  if (project.isPending) return <Loading label="Loading project…" />;
  if (project.isError) return <ErrorMessage error={project.error} />;
  const p = project.data;
  const runId = tab === "runs" ? rest[0] : undefined;
  return (
    <div className="stack">
      <nav aria-label="Breadcrumb" className="small">
        <a href={href(["valuations"])}>Valuations</a> /{" "}
        <a href={href(["companies", p.company_id, "valuation"])}>{p.company_name}</a> / {p.name}
      </nav>
      <header className="company-head">
        <div>
          <h1>{p.name}</h1>
          <p className="muted small">
            {p.company_name} · owner {p.owner.username} · dataset v{p.dataset.version_no} {p.dataset.basis} (
            {p.dataset.status}) · classification {p.classification.replaceAll("_", " ")}{" "}
            <Badge value={p.status} tone={p.status === "active" ? "good" : "neutral"} />
          </p>
        </div>
      </header>
      <nav aria-label="Project sections" className="tabs">
        {PROJECT_TABS.map((t) => (
          <a key={t.key} href={href(["valuations", p.id, t.key])} aria-current={tab === t.key ? "page" : undefined}>
            {t.label}
          </a>
        ))}
      </nav>
      {tab === "assumptions" ? (
        <AssumptionsPanel project={p} route={route} />
      ) : tab === "run" ? (
        <RunBuilder project={p} route={route} />
      ) : tab === "runs" && runId ? (
        <RunPage runId={runId} project={p} />
      ) : tab === "runs" ? (
        <RunsList project={p} />
      ) : (
        <ProjectOverview project={p} />
      )}
    </div>
  );
}

type DialogKind = "edit" | "rebase" | "archive" | "restore" | null;

function ProjectOverview({ project }: { project: ProjectDetailOut }) {
  const rights = useEditRights(project);
  const { me, can, whyNot } = useSession();
  const [open, setOpen] = useState<DialogKind>(null);
  const statusReason =
    !can(PERM.valuationWrite) ? whyNot(PERM.valuationWrite)
    : project.owner.id !== me.user.id && !rights.isAdmin ? `Only the owner (${project.owner.username}) may change this project.`
    : null; // prettier-ignore
  return (
    <div className="stack">
      <section className="card">
        <h2>Project</h2>
        <KV
          items={[
            ["Thesis", project.thesis ?? <span className="muted">none</span>],
            ["Owner", project.owner.username],
            ["Created", <DateTime key="c" value={project.created_at} />],
            ["Revision", String(project.revision_no)],
            ["Status reason", project.status_reason],
          ]}
        />
        <div className="form-actions">
          <GuardedButton disabledReason={rights.reason} onClick={() => setOpen("edit")}>
            Edit name or thesis
          </GuardedButton>
          {project.status === "active" ? (
            <GuardedButton className="secondary" disabledReason={statusReason} onClick={() => setOpen("archive")}>
              Archive
            </GuardedButton>
          ) : (
            <GuardedButton className="secondary" disabledReason={statusReason} onClick={() => setOpen("restore")}>
              Restore
            </GuardedButton>
          )}
        </div>
      </section>
      <section className="card">
        <h2>Pinned dataset</h2>
        <KV
          items={[
            ["Version", `v${project.dataset.version_no} (${project.dataset.basis})`],
            ["Status", <Badge key="s" value={project.dataset.status} />],
            ["Currency", project.dataset.currency],
            ["Content hash", <Hash key="h" value={project.dataset.content_sha256} />],
          ]}
        />
        {project.dataset.status === "draft" && (
          <Notice tone="warn">
            Draft dataset: previews work; saved runs need a locked dataset. Runs never substitute another version —
            rebase explicitly once one is locked.
          </Notice>
        )}
        {project.newer_locked_dataset && (
          <Notice>
            A newer locked version exists (v{project.newer_locked_dataset.version_no}). Nothing changes until you
            rebase; existing runs keep the dataset they used.
          </Notice>
        )}
        <GuardedButton disabledReason={rights.reason} onClick={() => setOpen("rebase")}>
          Rebase to another version…
        </GuardedButton>
      </section>
      {open && <ProjectDialog kind={open} project={project} needsAdminReason={rights.needsAdminReason} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ProjectDialog({
  kind,
  project,
  needsAdminReason,
  onClose,
}: {
  kind: Exclude<DialogKind, null>;
  project: ProjectDetailOut;
  needsAdminReason: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const idem = useIdempotency();
  const [name, setName] = useState(project.name);
  const [thesis, setThesis] = useState(project.thesis ?? "");
  const [reason, setReason] = useState("");
  const [adminReason, setAdminReason] = useState("");
  const [datasetId, setDatasetId] = useState(project.newer_locked_dataset?.id ?? "");
  const [sha, setSha] = useState(project.newer_locked_dataset?.content_sha256 ?? "");
  const admin = needsAdminReason ? { admin_reason: adminReason.trim() } : {};
  const action = useMutation({
    mutationFn: () => {
      if (kind === "edit") {
        const body = { expected_revision: project.revision_no, name: name.trim(), thesis: thesis.trim() || null, clear_thesis: !thesis.trim(), ...admin };
        return valuationApi.updateProject(project.id, body, idem.keyFor(body));
      }
      if (kind === "rebase") {
        const body = { expected_revision: project.revision_no, dataset_id: datasetId.trim(), content_sha256: sha.trim(), reason: reason.trim(), ...admin };
        return valuationApi.rebase(project.id, body, idem.keyFor(body));
      }
      const body = { expected_revision: project.revision_no, reason: reason.trim(), ...admin };
      return valuationApi.setStatus(project.id, kind, body, idem.keyFor(body));
    },
    onSuccess: () => {
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["valuation"] });
      onClose();
    },
  });
  const title = { edit: "Edit project", rebase: "Rebase to another dataset version", archive: "Archive project", restore: "Restore project" }[kind];
  const needsReason = kind !== "edit";
  const ready =
    (!needsReason || reason.trim().length >= 10) &&
    (!needsAdminReason || adminReason.trim().length >= 10) &&
    (kind !== "rebase" || (datasetId.trim() && /^[0-9a-f]{64}$/.test(sha.trim()))) &&
    (kind !== "edit" || name.trim().length > 0);
  return (
    <Dialog title={title} onClose={onClose}>
      {kind === "edit" && (
        <>
          <label>
            Name
            <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Thesis
            <textarea value={thesis} rows={4} maxLength={20000} onChange={(e) => setThesis(e.target.value)} />
          </label>
        </>
      )}
      {kind === "rebase" && (
        <>
          <p className="small">
            Pin another version of this company's dataset. Confirm its content hash (shown on the company's Datasets tab):
            the server refuses a version whose hash differs. Existing runs are unchanged.
          </p>
          <label>
            Dataset ID
            <input value={datasetId} onChange={(e) => setDatasetId(e.target.value)} />
          </label>
          <label>
            Content SHA-256
            <input value={sha} onChange={(e) => setSha(e.target.value)} />
          </label>
        </>
      )}
      {needsReason && (
        <label>
          Reason (at least 10 characters)
          <textarea value={reason} rows={2} onChange={(e) => setReason(e.target.value)} />
        </label>
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
        danger={kind === "archive"}
        disabled={!ready}
        onCancel={onClose}
        onConfirm={() => action.mutate()}
      />
    </Dialog>
  );
}
