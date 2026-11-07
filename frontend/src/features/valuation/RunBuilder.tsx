import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { CreateRunRequest, PreviewOut, ProjectDetailOut, RunRequestIn } from "../../api/schema.gen";
import { METHODOLOGIES, type Methodology, valuationApi } from "../../api/valuation";
import { useIdempotency } from "../../components/idempotency";
import { Badge, Dialog, DialogButtons, ErrorMessage, GuardedButton, Loading, Notice } from "../../components/ui";
import { formatDecimal } from "../../lib/decimal";
import { href, navigate, type Route } from "../../lib/router";
import { DcfOutputs, type Issue, IssueList, type Json, ValidationList } from "./common";
import { useEditRights } from "./ProjectPage";

interface OverrideDraft {
  input: string;
  line_item: string | null;
  kind: "value" | "not_applicable";
  value: string;
  source: string;
  reason: string;
}

const today = () => new Date().toISOString().slice(0, 10);

/** Input selection → preview (nothing stored) → save an immutable run (design §4–§5). */
export function RunBuilder({ project, route }: { project: ProjectDetailOut; route: Route }) {
  const rights = useEditRights(project);
  const idem = useIdempotency();
  const scenario = project.scenarios.find((s) => s.kind === "base") ?? project.scenarios[0];
  const [method, setMethod] = useState<Methodology>((route.query.get("method") as Methodology | null) ?? "dcf_fcff");
  const [valuationDate, setValuationDate] = useState(today());
  const [periodId, setPeriodId] = useState("");
  const [priceId, setPriceId] = useState("");
  const [fxId, setFxId] = useState("");
  const [overrides, setOverrides] = useState<OverrideDraft[]>([]);
  const [acks, setAcks] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<Record<string, string>>({});
  const [label, setLabel] = useState("");
  const [adminReason, setAdminReason] = useState("");
  const [preview, setPreview] = useState<PreviewOut | null>(null);
  const [confirming, setConfirming] = useState(false);

  const candidates = useQuery({
    queryKey: ["valuation", "candidates", project.id, valuationDate, project.listing_id],
    queryFn: () => valuationApi.candidates(project.id, valuationDate, project.listing_id),
    enabled: /^\d{4}-\d{2}-\d{2}$/.test(valuationDate),
  });
  const periods = candidates.data?.periods ?? [];
  const period = periodId || periods[0]?.id || "";
  const fxChoices = (candidates.data?.fx ?? []).filter(() => !!priceId);

  const body = (): RunRequestIn => ({
    methodology: method,
    scenario_id: scenario?.id ?? "",
    expected_scenario_revision: scenario?.revision_no ?? 0,
    dataset: { id: project.dataset.id, content_sha256: project.dataset.content_sha256 },
    balance_sheet_period_id: period,
    valuation_date: valuationDate,
    listing_id: project.listing_id,
    price_observation_id: priceId ? Number(priceId) : null,
    fx_rate_ids: fxId ? [Number(fxId)] : [],
    input_overrides: overrides.map((o) => ({
      input: o.input,
      line_item: o.line_item,
      kind: o.kind,
      value: o.kind === "value" ? o.value.trim() : null,
      source: o.kind === "value" ? o.source.trim() : null,
      reason: o.reason.trim(),
    })),
    acknowledgements: Object.entries(acks).map(([code, reason]) => ({ code, reason: reason.trim() })),
    check_overrides: Object.entries(checks).map(([k, reason]) => {
      const [check_code = "", subject = ""] = k.split("|");
      return { check_code, subject, reason: reason.trim() };
    }),
    ...(rights.needsAdminReason ? { admin_reason: adminReason.trim() } : {}),
  });

  const runPreview = useMutation({
    mutationFn: () => valuationApi.preview(project.id, body()),
    onSuccess: (data) => {
      setPreview(data);
      // Offer the decisions the issues ask for (never filled in automatically).
      for (const i of data.issues) {
        const d = i.details;
        if (i.code === "acknowledgement_required" && typeof d.ack === "string")
          setAcks((a) => (d.ack as string) in a ? a : { ...a, [d.ack as string]: "" });
        if (i.code === "checks_failed" && typeof d.check_code === "string")
          setChecks((c) => {
            const k = `${String(d.check_code)}|${String(d.subject)}`;
            return k in c ? c : { ...c, [k]: "" };
          });
        if (i.code === "input_missing" && typeof d.line_item === "string")
          setOverrides((o) =>
            o.some((x) => x.input === d.input && x.line_item === d.line_item)
              ? o
              : [...o, { input: String(d.input), line_item: String(d.line_item), kind: "not_applicable", value: "", source: "", reason: "" }],
          );
      }
    },
  });
  const save = useMutation({
    mutationFn: () => {
      const b: CreateRunRequest = { ...body(), label: label.trim() || null };
      return valuationApi.createRun(project.id, b, idem.keyFor(b));
    },
    onSuccess: (data) => {
      idem.reset();
      setConfirming(false);
      navigate(href(["valuations", project.id, "runs", data.result.run.id]));
    },
  });

  if (!scenario) return <Notice tone="warn">This project has no scenario.</Notice>;
  return (
    <div className="stack">
      {!rights.canEdit && <Notice tone="warn">Preview and saving are unavailable: {rights.reason}</Notice>}
      <section className="card" aria-label="Inputs">
        <h2>Inputs</h2>
        <p className="small muted">
          Every input is named explicitly. The server never substitutes another dataset version, a later price or rate,
          or a newer reference edition; candidates below are suggestions with their row ids.
        </p>
        <div className="form-grid">
          <label>
            Methodology
            <select value={method} onChange={(e) => setMethod(e.target.value as Methodology)}>
              {METHODOLOGIES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Valuation date
            <input type="date" value={valuationDate} onChange={(e) => setValuationDate(e.target.value)} />
          </label>
          <label>
            Balance-sheet date (fiscal year of dataset v{project.dataset.version_no})
            <select value={period} onChange={(e) => setPeriodId(e.target.value)}>
              {periods.length === 0 && <option value="">No fiscal year on or before the valuation date</option>}
              {periods.map((p) => (
                <option key={p.id} value={p.id}>
                  FY{p.fiscal_year} · ends {p.end_date} · {p.value_count} values
                </option>
              ))}
            </select>
          </label>
          <label>
            Market price (optional for target weights; required for market weights)
            <select value={priceId} onChange={(e) => { setPriceId(e.target.value); setFxId(""); }}>
              <option value="">No price</option>
              {(candidates.data?.prices ?? []).map((p) => (
                <option key={p.observation_id} value={p.observation_id}>
                  {p.trade_date} · {formatDecimal(p.close)} {p.currency} · {p.provider_code}/{p.close_basis}
                  {p.stale ? " · stale" : ""}
                </option>
              ))}
            </select>
          </label>
          {fxChoices.length > 0 && (
            <label>
              FX rate (price → reporting currency)
              <select value={fxId} onChange={(e) => setFxId(e.target.value)}>
                <option value="">None</option>
                {fxChoices.map((f) => (
                  <option key={f.fx_rate_id} value={f.fx_rate_id}>
                    {f.obs_date} · {f.rate} {f.quote} per 1 {f.base} ({f.conversion === "reciprocal" ? "exact reciprocal used" : "direct"})
                    {f.stale ? " · stale" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Run label (optional)
            <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />
          </label>
        </div>
        {candidates.isError && <ErrorMessage error={candidates.error} />}
        {candidates.data && <Candidates data={candidates.data} />}
      </section>

      {(overrides.length > 0 || Object.keys(acks).length > 0 || Object.keys(checks).length > 0) && (
        <section className="card" aria-label="Decisions">
          <h2>Decisions this run needs</h2>
          <p className="small muted">Each decision is recorded with your name, the time, the reason and the run.</p>
          {Object.keys(acks).map((code) => (
            <label key={code}>
              Acknowledge <code>{code}</code> — reason (≥ 10 characters)
              <input value={acks[code] ?? ""} onChange={(e) => setAcks({ ...acks, [code]: e.target.value })} />
            </label>
          ))}
          {Object.keys(checks).map((k) => (
            <label key={k}>
              Override failed check <code>{k.replace("|", " / ")}</code> — reason
              <input value={checks[k] ?? ""} onChange={(e) => setChecks({ ...checks, [k]: e.target.value })} />
            </label>
          ))}
          {overrides.map((o, i) => (
            <fieldset key={`${o.input}-${o.line_item}`} className="override">
              <legend>
                {o.input} / {o.line_item ?? "whole input"} — missing in the dataset (never treated as zero)
              </legend>
              <select
                aria-label={`Override kind for ${o.line_item}`}
                value={o.kind}
                onChange={(e) => setOverrides(overrides.map((x, j) => (j === i ? { ...x, kind: e.target.value as OverrideDraft["kind"] } : x)))}
              >
                <option value="not_applicable">Not applicable to this company</option>
                <option value="value">Enter a value with a source</option>
              </select>
              {o.kind === "value" && (
                <>
                  <input aria-label={`Value for ${o.line_item}`} placeholder="Value" value={o.value} onChange={(e) => setOverrides(overrides.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
                  <input aria-label={`Source for ${o.line_item}`} placeholder="Source" value={o.source} onChange={(e) => setOverrides(overrides.map((x, j) => (j === i ? { ...x, source: e.target.value } : x)))} />
                </>
              )}
              <input aria-label={`Reason for ${o.line_item}`} placeholder="Reason (≥ 10 characters)" value={o.reason} onChange={(e) => setOverrides(overrides.map((x, j) => (j === i ? { ...x, reason: e.target.value } : x)))} />
              <button type="button" className="link" onClick={() => setOverrides(overrides.filter((_, j) => j !== i))}>
                Remove
              </button>
            </fieldset>
          ))}
        </section>
      )}

      {rights.needsAdminReason && (
        <label>
          Administrator reason — you are not the owner; recorded with the run
          <textarea value={adminReason} rows={2} onChange={(e) => setAdminReason(e.target.value)} />
        </label>
      )}
      <div className="form-actions">
        <GuardedButton
          disabledReason={rights.reason ?? (period ? null : "Choose a balance-sheet date.")}
          onClick={() => runPreview.mutate()}
        >
          {runPreview.isPending ? "Calculating…" : "Preview (nothing is saved)"}
        </GuardedButton>
        <GuardedButton
          disabledReason={
            rights.reason ??
            (!preview ? "Preview first." : !preview.can_save ? "The preview shows issues that block saving." : null)
          }
          onClick={() => setConfirming(true)}
        >
          Save run…
        </GuardedButton>
      </div>
      {runPreview.isError && <ErrorMessage error={runPreview.error} />}
      {runPreview.isPending && <Loading label="Calculating on the server…" />}
      {preview && <PreviewResult preview={preview} />}
      {confirming && (
        <Dialog title="Save an immutable run" onClose={() => setConfirming(false)}>
          <p>
            The server re-checks every input and gate, recalculates and stores the run with its input document, trace and
            hashes. It cannot be changed or deleted afterwards. Identical inputs return the existing run.
          </p>
          {save.isError && <ErrorMessage error={save.error} />}
          <DialogButtons pending={save.isPending} label="Save run" onCancel={() => setConfirming(false)} onConfirm={() => save.mutate()} />
        </Dialog>
      )}
    </div>
  );
}

function Candidates({ data }: { data: Awaited<ReturnType<typeof valuationApi.candidates>> }) {
  return (
    <details>
      <summary>
        Stored candidates on or before {data.valuation_date}: {data.rates.length} rate(s), {data.prices.length} price(s),{" "}
        {data.fx.length} FX rate(s), {data.reference.length} reference dataset(s)
      </summary>
      {data.notes.map((n) => (
        <Notice key={n}>{n}</Notice>
      ))}
      {data.rates.length > 0 && (
        <table className="data small" aria-label="Risk-free candidates">
          <thead>
            <tr>
              <th scope="col">Observation id</th>
              <th scope="col">Series</th>
              <th scope="col">Date</th>
              <th scope="col" className="num">Published (pp)</th>
              <th scope="col" className="num">As fraction</th>
            </tr>
          </thead>
          <tbody>
            {data.rates.map((r) => (
              <tr key={r.observation_id}>
                <td>{r.observation_id}</td>
                <td>{r.series_code}</td>
                <td>
                  {r.obs_date} {r.stale && <Badge value="stale" tone="warn" />}
                </td>
                <td className="num">{r.value_pp}</td>
                <td className="num">{r.value_fraction}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data.reference.length > 0 && (
        <ul className="small">
          {data.reference.map((r) => (
            <li key={r.reference_dataset_id}>
              {r.dataset_kind} · edition {r.edition_date} · v{r.version_no} · <code>{r.reference_dataset_id}</code>
              {r.stale && <> <Badge value="stale" tone="warn" /></>}
            </li>
          ))}
        </ul>
      )}
      <p className="small muted">
        Use a rate observation id or a reference dataset id as the source of the matching assumption (Assumptions tab).
      </p>
    </details>
  );
}

export function PreviewResult({ preview }: { preview: PreviewOut }) {
  const issues = preview.issues as Issue[];
  return (
    <section className="card stack" aria-label="Preview result">
      <h2>
        Preview <Badge value={preview.can_save ? "can be saved" : "cannot be saved yet"} tone={preview.can_save ? "good" : "warn"} />
      </h2>
      <p className="small muted">
        A preview is calculated by the server's engine and is not a saved valuation.
        {preview.dataset_status === "draft" && " The pinned dataset is a draft."}
      </p>
      <IssueList issues={issues} empty={<Notice tone="success">No blocking issues.</Notice>} />
      {preview.engine_refusal && (
        <div className="alert alert-error" role="alert">
          <strong>The engine refused the inputs.</strong> {String(preview.engine_refusal.message)}
        </div>
      )}
      {preview.outputs && <DcfOutputs outputs={preview.outputs as Json} />}
      {preview.outputs && <ValidationList checks={preview.validation as Json[]} />}
    </section>
  );
}
