import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { ApiError } from "../../api/client";
import { api } from "../../api/endpoints";
import {
  type ImportKind,
  type IngestParams,
  MARKET_TEMPLATES,
  type PreviewParams,
  type PreviewReport,
  REFERENCE_REGIONS,
  REFERENCE_TEMPLATES,
  REFERENCE_VARIANTS,
} from "../../api/imports";
import { useIdempotency } from "../../components/idempotency";
import { Dialog, ErrorMessage, GuardedButton, JsonView, Loading, Notice } from "../../components/ui";
import { href } from "../../lib/router";
import { PERM, useSession } from "../../session";
import { PreviewReportView, skippedCount } from "./PreviewReportView";

export const MAX_BYTES = 20 * 1024 * 1024;

const KINDS: { value: ImportKind; label: string; guide: string }[] = [
  {
    value: "statements",
    label: "Financial statements (financial_statements.v1)",
    guide: "docs/operations/financial-statement-import.md",
  },
  {
    value: "market",
    label: "Market prices or corporate actions",
    guide: "docs/operations/market-data-import.md",
  },
  {
    value: "reference",
    label: "Damodaran reference data",
    guide: "docs/operations/reference-data-import.md",
  },
];

/** Client-side checks before upload; the backend validates everything again. */
export function fileProblem(file: File | null): string | null {
  if (!file) return "Choose a CSV file.";
  if (!/\.csv$/i.test(file.name))
    return /\.xlsx?$/i.test(file.name)
      ? "Spreadsheet (XLS/XLSX) files are not supported; export the sheet as CSV (XLSX support awaits decision P14)."
      : "Only .csv files are supported.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_BYTES) return "Files are limited to 20 MB.";
  return null;
}

export function ImportPreview() {
  const session = useSession();
  const [kind, setKind] = useState<ImportKind>("statements");
  const [template, setTemplate] = useState<string>(MARKET_TEMPLATES[0]);
  const [refTemplate, setRefTemplate] = useState<string>(REFERENCE_TEMPLATES[0]);
  const [editionDate, setEditionDate] = useState("");
  const [region, setRegion] = useState("");
  const [variant, setVariant] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [touched, setTouched] = useState(false);

  const params: PreviewParams = {
    kind,
    filename: file?.name ?? "",
    template: kind === "market" ? template : kind === "reference" ? refTemplate : undefined,
    edition_date: kind === "reference" ? editionDate : undefined,
    region: kind === "reference" ? region || undefined : undefined,
    variant: kind === "reference" ? variant || undefined : undefined,
  };
  const problem = fileProblem(file) ?? (kind === "reference" && !editionDate ? "Enter the edition date." : null);

  const preview = useMutation({
    mutationFn: (p: { params: PreviewParams; file: File }) => api.previewImport(p.params, p.file),
  });
  const result = preview.data && preview.variables ? { report: preview.data, ...preview.variables } : null;

  if (!session.can(PERM.write)) {
    return (
      <Notice>
        Previewing a file needs the <code>company:write</code> permission (analyst or administrator).{" "}
        {session.whyNot(PERM.write)} The <a href={href(["imports"])}>import history</a> is available to
        every role.
      </Notice>
    );
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (problem || !file) return;
    preview.mutate({ params: { ...params }, file });
  }

  return (
    <div className="stack">
      <Notice>
        <strong>Preview only.</strong> The file is validated on a read-only database session: nothing
        is stored, ingested or archived, and the preview is not recorded as an action. CSV is the only
        supported format.
      </Notice>
      <form className="card form" onSubmit={submit} aria-labelledby="preview-title">
        <h2 id="preview-title">Preview a file</h2>
        <fieldset>
          <legend>File type</legend>
          {KINDS.map((k) => (
            <label key={k.value} className="checkbox">
              <input
                type="radio"
                name="kind"
                value={k.value}
                checked={kind === k.value}
                onChange={() => {
                  setKind(k.value);
                  preview.reset();
                }}
              />
              {k.label}
            </label>
          ))}
        </fieldset>
        <div className="form-row">
          {kind === "market" && (
            <label>
              Template
              <select value={template} onChange={(e) => setTemplate(e.target.value)}>
                {MARKET_TEMPLATES.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
          )}
          {kind === "reference" && (
            <>
              <label>
                Template
                <select value={refTemplate} onChange={(e) => setRefTemplate(e.target.value)}>
                  {REFERENCE_TEMPLATES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label>
                Edition date
                <input type="date" value={editionDate} onChange={(e) => setEditionDate(e.target.value)} />
              </label>
              <label>
                Region (betas/spreads)
                <select value={region} onChange={(e) => setRegion(e.target.value)}>
                  <option value="">—</option>
                  {REFERENCE_REGIONS.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label>
                Variant
                <select value={variant} onChange={(e) => setVariant(e.target.value)}>
                  <option value="">—</option>
                  {REFERENCE_VARIANTS.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label className="grow">
            CSV file (at most 20 MB)
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                preview.reset();
              }}
            />
          </label>
        </div>
        {touched && problem && (
          <p className="error small" role="alert">
            {problem}
          </p>
        )}
        <div className="form-actions">
          <button type="submit" disabled={preview.isPending}>
            {preview.isPending ? "Validating…" : "Validate (preview)"}
          </button>
        </div>
        <p className="small muted">
          Template and column rules: <code>{KINDS.find((k) => k.value === kind)?.guide}</code>.
        </p>
      </form>
      {preview.isPending && <Loading label="Validating the file…" />}
      {preview.isError && <ErrorMessage error={preview.error} />}
      {result && (
        <>
          <PreviewReportView kind={result.params.kind} report={result.report} />
          <IngestPanel
            key={result.report.file_sha256}
            params={result.params}
            file={result.file}
            report={result.report}
          />
        </>
      )}
    </div>
  );
}

/** The ingestion step, with every argument the backend requires. Pending P15 for every role. */
function IngestPanel({ params, file, report }: { params: PreviewParams; file: File; report: PreviewReport }) {
  const session = useSession();
  const qc = useQueryClient();
  const idem = useIdempotency();
  const [source, setSource] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [allowPartial, setAllowPartial] = useState(false);
  const [allowConflicts, setAllowConflicts] = useState(false);
  const [supersede, setSupersede] = useState(false);
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);

  const skipped = skippedCount(params.kind, report);
  const conflicting = params.kind === "statements" ? (report.summary["conflicting"] ?? 0) : 0;
  const needsSupersede = report.outcome === "new_version_requires_supersede";
  const canImport = session.can(PERM.importData);

  const blockers: string[] = [];
  if (report.file_errors.length) blockers.push("The file has file-level errors.");
  if (report.outcome === "nothing_to_store" || report.outcome === "identical_to_current")
    blockers.push("Nothing new would be stored.");
  if (!source.trim()) blockers.push("Name the source of the data.");
  if (skipped > 0 && !allowPartial) blockers.push(`${skipped} record(s) would be skipped: allow a partial import or fix the file.`);
  if (conflicting > 0 && !allowConflicts)
    blockers.push(`${conflicting} record(s) conflict with stored values: allow conflicts (each opens a conflict for review) or fix the file.`);
  if (needsSupersede && (!supersede || reason.trim().length < 10))
    blockers.push("This changes the current reference version: supersede it with a reason (at least 10 characters).");

  const ingestParams: IngestParams = {
    ...params,
    expect_sha256: report.file_sha256,
    source: source.trim(),
    source_reference: sourceRef.trim() || undefined,
    allow_partial: allowPartial,
    allow_conflicts: allowConflicts,
    supersede: needsSupersede ? supersede : undefined,
    reason: needsSupersede && supersede ? reason.trim() : undefined,
  };
  const ingest = useMutation({
    mutationFn: () => api.ingestImport(ingestParams, file, idem.keyFor(ingestParams)),
    onSuccess: () => {
      setConfirming(false);
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["imports"] });
      void qc.invalidateQueries({ queryKey: ["actions"] });
      void qc.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: () => setConfirming(false),
  });
  const archiveDown = ingest.error instanceof ApiError && ingest.error.status === 503;

  return (
    <section className="card form" aria-labelledby="ingest-title">
      <h2 id="ingest-title">Next step: ingest this file</h2>
      <p className="small">
        Ingestion re-validates the same bytes and must match the previewed hash{" "}
        <code>{report.file_sha256}</code> (sent as <code>expect_sha256</code>). It archives the file,
        appends the records and is recorded as an audited action.
      </p>
      {!canImport && <Notice tone="warn">{session.whyNot(PERM.importData)}</Notice>}
      <fieldset disabled={!canImport}>
        <legend>Ingestion arguments</legend>
        <div className="form-row">
          <label className="grow">
            Source (required)
            <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="e.g. Annual report 2025" />
          </label>
          <label className="grow">
            Source reference (optional)
            <input value={sourceRef} onChange={(e) => setSourceRef(e.target.value)} placeholder="URL, page or document id" />
          </label>
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={allowPartial} onChange={(e) => setAllowPartial(e.target.checked)} />
          Allow a partial import ({skipped} record(s) would be skipped)
        </label>
        {params.kind === "statements" && (
          <label className="checkbox">
            <input type="checkbox" checked={allowConflicts} onChange={(e) => setAllowConflicts(e.target.checked)} />
            Allow conflicting values ({conflicting}); each opens a conflict for review
          </label>
        )}
        {needsSupersede && (
          <>
            <label className="checkbox">
              <input type="checkbox" checked={supersede} onChange={(e) => setSupersede(e.target.checked)} />
              Supersede the current version of this lineage
            </label>
            <label>
              Reason for superseding
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
          </>
        )}
      </fieldset>
      {canImport && blockers.length > 0 && (
        <ul className="small muted">
          {blockers.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}
      <div className="form-actions">
        <GuardedButton
          disabledReason={
            !canImport
              ? "Unavailable: pending owner approval (P15)."
              : blockers.length
                ? "Resolve the items above first."
                : null
          }
          onClick={() => {
            ingest.reset();
            setConfirming(true);
          }}
        >
          Ingest…
        </GuardedButton>
      </div>
      {ingest.isError && <ErrorMessage error={ingest.error} />}
      {archiveDown && (
        <Notice tone="warn">
          The API container cannot write the raw-file archive, so ingestion is not available through
          the API on this server. Nothing was stored. Use the CLI described in the import guide.
        </Notice>
      )}
      {ingest.isSuccess && (
        <Notice tone="success">
          Ingested (action <a href={href(["actions", ingest.data.action_id])}>{ingest.data.action_id}</a>).
          <JsonView value={ingest.data.result} />
        </Notice>
      )}
      {confirming && (
        <Dialog title="Confirm ingestion" onClose={() => setConfirming(false)}>
          <p>
            Ingest <strong>{params.filename}</strong> ({params.kind}) from source “{source.trim()}”. The
            records are appended permanently; a later correction needs a new import
            {params.kind === "reference" ? " superseding this version" : ""}.
          </p>
          <p className="small">
            SHA-256 <code>{report.file_sha256}</code>
            {allowPartial ? ` · partial (${skipped} skipped)` : ""}
            {allowConflicts ? ` · ${conflicting} conflicting` : ""}
          </p>
          <div className="form-actions">
            <button type="button" className="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </button>
            <button type="button" disabled={ingest.isPending} onClick={() => ingest.mutate()}>
              {ingest.isPending ? "Ingesting…" : "Ingest"}
            </button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
