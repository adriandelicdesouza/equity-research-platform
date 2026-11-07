import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/endpoints";
import type {
  ActionOut,
  ClassificationOut,
  CompanyDetailOut,
  ConfirmClassificationRequest,
} from "../../api/schema.gen";
import { useIdempotency } from "../../components/idempotency";
import {
  Badge,
  DateTime,
  Dialog,
  DialogButtons,
  Empty,
  ErrorMessage,
  GuardedButton,
  JsonView,
  KV,
  Loading,
  Notice,
  Pager,
} from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { PERM, useSession } from "../../session";

const MIN_REASON = 10;
const LIMIT = 25;

type Classification = ConfirmClassificationRequest["classification"];

/** The D53 classes, in the order the backend lists them. */
export const CLASSIFICATIONS: { value: Classification; label: string }[] = [
  { value: "non_financial", label: "Non-financial" },
  { value: "bank", label: "Bank" },
  { value: "insurer", label: "Insurer" },
  { value: "specialty_lender", label: "Specialty lender" },
  { value: "reit", label: "REIT" },
  { value: "asset_manager_broker", label: "Asset manager / broker" },
  { value: "investment_holding", label: "Investment holding" },
];

export function classificationLabel(value: string): string {
  return CLASSIFICATIONS.find((c) => c.value === value)?.label ?? value.replaceAll("_", " ");
}

export function ClassificationTab({ company: c, route }: { company: CompanyDetailOut; route: Route }) {
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const history = useQuery({
    queryKey: ["classifications", c.id, offset],
    queryFn: () => api.classifications(c.id, { limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  const cls = c.classification;
  return (
    <div className="grid-2">
      <section className="card">
        <h2>Current classification</h2>
        <KV
          items={[
            ["Effective", <strong key="e">{classificationLabel(cls.effective)}</strong>],
            ["Status", <Badge key="s" value={cls.status} />],
            [
              "Latest suggestion",
              cls.latest_suggestion
                ? `${classificationLabel(cls.latest_suggestion.classification)} (${cls.latest_suggestion.classifier_version ?? "classifier"})`
                : null,
            ],
            [
              "Latest confirmation",
              cls.latest_confirmation ? (
                <span key="c">
                  {classificationLabel(cls.latest_confirmation.classification)} —{" "}
                  {cls.latest_confirmation.reason ?? ""} (
                  <DateTime value={cls.latest_confirmation.created_at} />)
                </span>
              ) : null,
            ],
            ["SIC", c.sic_code ? `${c.sic_code} — ${c.sic_description ?? ""}` : null],
          ]}
        />
        <p className="small muted">
          The classification decides which valuation methods apply (D53). A suggestion comes from the
          SIC-based classifier; an analyst confirmation is authoritative and may differ from it.
        </p>
      </section>

      <ConfirmForm company={c} />

      <section className="card span-2">
        <h2>History</h2>
        <p className="small muted">Append-only, newest first. Each entry is a suggestion or a confirmation.</p>
        {history.isPending && <Loading />}
        {history.isError && <ErrorMessage error={history.error} />}
        {history.data && history.data.items.length === 0 && (
          <Empty>No classification has been recorded for this company.</Empty>
        )}
        {history.data && history.data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Recorded</th>
                    <th scope="col">Kind</th>
                    <th scope="col">Classification</th>
                    <th scope="col">Classifier</th>
                    <th scope="col">Reason</th>
                    <th scope="col">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.items.map((h) => (
                    <HistoryRow key={h.id} h={h} />
                  ))}
                </tbody>
              </table>
            </div>
            <Pager
              total={history.data.total}
              limit={LIMIT}
              offset={offset}
              onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
            />
          </>
        )}
      </section>
    </div>
  );
}

function HistoryRow({ h }: { h: ClassificationOut }) {
  const hasEvidence = Object.keys(h.evidence).length > 0;
  return (
    <tr>
      <td>
        <DateTime value={h.created_at} />
      </td>
      <td>
        <Badge value={h.kind} tone={h.kind === "confirmed" ? "good" : "neutral"} />
      </td>
      <th scope="row">{classificationLabel(h.classification)}</th>
      <td>{h.classifier_version ?? "—"}</td>
      <td>{h.reason ?? "—"}</td>
      <td>
        {hasEvidence ? (
          <details>
            <summary>Show</summary>
            <JsonView value={h.evidence} />
          </details>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}

function ConfirmForm({ company: c }: { company: CompanyDetailOut }) {
  const session = useSession();
  const qc = useQueryClient();
  const idem = useIdempotency();
  const initial = (c.classification.latest_confirmation?.classification ??
    c.classification.latest_suggestion?.classification ??
    c.classification.effective) as Classification;
  const [value, setValue] = useState<Classification>(
    CLASSIFICATIONS.some((x) => x.value === initial) ? initial : "non_financial",
  );
  const [reason, setReason] = useState("");
  const [open, setOpen] = useState(false);

  const run = useMutation({
    mutationFn: () => {
      const body: ConfirmClassificationRequest = { classification: value, reason: reason.trim() };
      return api.confirmClassification(c.id, body, idem.keyFor(["classification", c.id, body]));
    },
    onSuccess: () => {
      setOpen(false);
      setReason("");
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["company", c.id] });
      void qc.invalidateQueries({ queryKey: ["classifications", c.id] });
      void qc.invalidateQueries({ queryKey: ["actions"] });
    },
    onError: () => setOpen(false),
  });

  const why = session.can(PERM.write) ? null : session.whyNot(PERM.write);
  const suggestion = c.classification.latest_suggestion?.classification;
  const differs = suggestion !== undefined && suggestion !== value;
  const tooShort = reason.trim().length < MIN_REASON;

  return (
    <section className="card stack">
      <h2>Confirm the classification</h2>
      <label>
        Classification
        <select value={value} onChange={(e) => setValue(e.target.value as Classification)} disabled={why !== null}>
          {CLASSIFICATIONS.map((x) => (
            <option key={x.value} value={x.value}>
              {x.label}
            </option>
          ))}
        </select>
      </label>
      {differs && (
        <Notice tone="warn">
          This differs from the classifier's suggestion ({classificationLabel(suggestion)}). Explain why in
          the reason.
        </Notice>
      )}
      <label>
        Reason (at least {MIN_REASON} characters; recorded in the audit trail)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} disabled={why !== null} />
      </label>
      <div>
        <GuardedButton
          disabledReason={why ?? (tooShort ? `Enter a reason of at least ${MIN_REASON} characters.` : null)}
          onClick={() => setOpen(true)}
        >
          Confirm…
        </GuardedButton>
      </div>
      {run.isError && <ErrorMessage error={run.error} />}
      {run.isSuccess && <ClassificationResult out={run.data} />}
      {open && (
        <Dialog title="Confirm the classification" onClose={() => setOpen(false)}>
          <p>
            Record <strong>{classificationLabel(value)}</strong> as the confirmed classification of{" "}
            {c.common_name ?? c.legal_name}. The history is append-only: a later confirmation supersedes
            this one but never removes it.
          </p>
          <p className="small">Reason: {reason.trim()}</p>
          <DialogButtons
            pending={run.isPending}
            label="Confirm classification"
            onCancel={() => setOpen(false)}
            onConfirm={() => run.mutate()}
          />
        </Dialog>
      )}
    </section>
  );
}

function ClassificationResult({ out }: { out: ActionOut }) {
  const r = (out.result ?? {}) as { effective?: string };
  return (
    <Notice tone="success">
      Confirmed. The effective classification is now <strong>{classificationLabel(r.effective ?? "")}</strong>.{" "}
      <span className="small">
        (action <a href={href(["actions", out.action_id])}>{out.action_id}</a>)
      </span>
    </Notice>
  );
}
