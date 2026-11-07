import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/endpoints";
import type {
  ActionOut,
  CorporateActionOut,
  CurrencyRequest,
  ListingOut,
  OriginOut,
  PriceOut,
} from "../../api/schema.gen";
import { useIdempotency } from "../../components/idempotency";
import {
  Badge,
  Dialog,
  DialogButtons,
  Empty,
  ErrorMessage,
  GuardedButton,
  KV,
  Loading,
  Notice,
  Pager,
} from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { PERM, useSession } from "../../session";

const MIN_REASON = 10;
const LIMIT = 50;

/** Listing workspace: identity, trading-currency declaration, prices and corporate actions. */
export function ListingPage({ listingId, route }: { listingId: string; route: Route }) {
  const listing = useQuery({ queryKey: ["listing", listingId], queryFn: () => api.listing(listingId) });
  const companyId = listing.data?.company_id;
  const company = useQuery({
    queryKey: ["company", companyId],
    queryFn: () => (companyId ? api.company(companyId) : Promise.reject(new Error("no company"))),
    enabled: companyId !== undefined,
  });
  if (listing.isPending) return <Loading label="Loading listing…" />;
  if (listing.isError) return <ErrorMessage error={listing.error} />;
  const l = listing.data;
  const companyName = company.data ? (company.data.common_name ?? company.data.legal_name) : null;
  const view = route.query.get("view") === "actions" ? "actions" : "prices";

  return (
    <div className="stack">
      <nav aria-label="Breadcrumb" className="small">
        <a href={href(["listings"])}>Listings</a> /{" "}
        <a href={href(["companies", l.company_id])}>{companyName ?? "Company"}</a> / {l.ticker}
      </nav>
      <header className="company-head">
        <div>
          <h1>
            {l.ticker} · {l.exchange_name}
          </h1>
          <p className="muted small">
            {companyName && <>{companyName} · </>}
            {l.exchange_mic ?? "no MIC"} ·{" "}
            {l.trading_currency ? `trades in ${l.trading_currency}` : "trading currency not declared"}
          </p>
        </div>
      </header>
      <div className="grid-2">
        <ListingSummary l={l} />
        <CurrencyDeclaration l={l} />
      </div>
      <nav aria-label="Market data" className="tabs">
        <a href={withQuery(route, { view: null, offset: null })} aria-current={view === "prices" ? "page" : undefined}>
          Prices
        </a>
        <a
          href={withQuery(route, { view: "actions", offset: null })}
          aria-current={view === "actions" ? "page" : undefined}
        >
          Corporate actions
        </a>
      </nav>
      {view === "prices" ? <Prices l={l} route={route} /> : <CorporateActions l={l} route={route} />}
    </div>
  );
}

function ListingSummary({ l }: { l: ListingOut }) {
  const symbols = Object.entries(l.provider_symbols);
  return (
    <section className="card">
      <h2>Listing</h2>
      <KV
        items={[
          ["Ticker", l.ticker],
          ["Exchange", l.exchange_name],
          ["MIC", l.exchange_mic],
          [
            "Trading currency",
            l.trading_currency ?? <span className="muted">not declared — prices cannot be converted</span>,
          ],
          ["Primary listing", l.is_primary ? "yes" : "no"],
          ["Security type", l.security_type?.replaceAll("_", " ") ?? null],
          ["Share class", l.share_class],
          ["ISIN", l.isin],
          [
            "Provider symbols",
            symbols.length ? symbols.map(([k, v]) => `${k}: ${v}`).join(" · ") : null,
          ],
          ["Origin", l.origin === "sec" ? "SEC filings" : l.origin.replaceAll("_", " ")],
          ["Registration source", l.registration_source],
        ]}
      />
    </section>
  );
}

// ------------------------------------------------------------------ trading currency

function CurrencyDeclaration({ l }: { l: ListingOut }) {
  const session = useSession();
  const qc = useQueryClient();
  const idem = useIdempotency();
  const [currency, setCurrency] = useState(l.trading_currency ?? "");
  const [reason, setReason] = useState("");
  const [open, setOpen] = useState(false);

  const run = useMutation({
    mutationFn: () => {
      const body: CurrencyRequest = { currency: currency.trim().toUpperCase(), reason: reason.trim() };
      return api.declareTradingCurrency(l.id, body, idem.keyFor(["currency", l.id, body]));
    },
    onSuccess: () => {
      setOpen(false);
      setReason("");
      idem.reset();
      void qc.invalidateQueries({ queryKey: ["listing", l.id] });
      void qc.invalidateQueries({ queryKey: ["listings"] });
      void qc.invalidateQueries({ queryKey: ["company", l.company_id] });
      void qc.invalidateQueries({ queryKey: ["actions"] });
    },
    onError: () => setOpen(false),
  });

  const code = currency.trim().toUpperCase();
  const why = !session.can(PERM.write)
    ? session.whyNot(PERM.write)
    : l.trading_currency
      ? `The trading currency is declared once (${l.trading_currency}); a different value is refused.`
      : !/^[A-Z]{3}$/.test(code)
        ? "Enter an ISO 4217 code of three letters."
        : reason.trim().length < MIN_REASON
          ? `Enter a reason of at least ${MIN_REASON} characters.`
          : null;

  return (
    <section className="card stack">
      <h2>Trading currency</h2>
      <p className="small muted">
        The currency prices are quoted in. It is never inferred: it is declared once, with a reason, and
        recorded in the audit trail.
      </p>
      <div className="form-row">
        <label>
          Currency (ISO 4217)
          <input
            value={currency}
            maxLength={3}
            onChange={(e) => setCurrency(e.target.value)}
            disabled={!session.can(PERM.write) || l.trading_currency !== null}
          />
        </label>
      </div>
      {l.trading_currency === null && (
        <label>
          Reason (at least {MIN_REASON} characters)
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} disabled={!session.can(PERM.write)} />
        </label>
      )}
      <div>
        <GuardedButton disabledReason={why} onClick={() => setOpen(true)}>
          Declare…
        </GuardedButton>
      </div>
      {run.isError && <ErrorMessage error={run.error} />}
      {run.isSuccess && <CurrencyResult out={run.data} />}
      {open && (
        <Dialog title="Declare the trading currency" onClose={() => setOpen(false)}>
          <p>
            Declare <strong>{code}</strong> as the trading currency of {l.ticker} on {l.exchange_name}. It
            cannot be changed afterwards through the UI or API.
          </p>
          <DialogButtons
            pending={run.isPending}
            label="Declare currency"
            onCancel={() => setOpen(false)}
            onConfirm={() => run.mutate()}
          />
        </Dialog>
      )}
    </section>
  );
}

function CurrencyResult({ out }: { out: ActionOut }) {
  const r = (out.result ?? {}) as { changed?: boolean; listing?: ListingOut };
  return (
    <Notice tone="success">
      {r.changed === false
        ? `${r.listing?.trading_currency ?? "This currency"} was already declared; nothing changed.`
        : `Declared ${r.listing?.trading_currency ?? ""}.`}{" "}
      <span className="small">
        (action <a href={href(["actions", out.action_id])}>{out.action_id}</a>)
      </span>
    </Notice>
  );
}

// ------------------------------------------------------------------ prices

export function originLink(o: OriginOut) {
  if (o.kind === "file_import" && o.import_id) {
    return (
      <a href={href(["imports", o.import_id])}>
        import{o.import_row != null ? `, row ${o.import_row}` : ""}
      </a>
    );
  }
  return <span title={o.fetch_id ?? undefined}>{o.kind.replaceAll("_", " ")}</span>;
}

function Prices({ l, route }: { l: ListingOut; route: Route }) {
  const q = route.query;
  const filters = {
    provider: q.get("provider") ?? "",
    close_basis: q.get("close_basis") ?? "",
    start: q.get("start") ?? "",
    end: q.get("end") ?? "",
    revisions: (q.get("revisions") === "all" ? "all" : "latest") as "latest" | "all",
    sort: (q.get("sort") === "date" ? "date" : "-date") as "date" | "-date",
  };
  const offset = Number(q.get("offset") ?? 0) || 0;
  const prices = useQuery({
    queryKey: ["prices", l.id, filters, offset],
    queryFn: () =>
      api.prices(l.id, {
        provider: filters.provider || undefined,
        close_basis: filters.close_basis || undefined,
        start: filters.start || undefined,
        end: filters.end || undefined,
        revisions: filters.revisions,
        sort: filters.sort,
        limit: LIMIT,
        offset,
      }),
    placeholderData: keepPreviousData,
  });
  const set = (changes: Record<string, string | null>) => navigate(withQuery(route, { ...changes, offset: null }));

  return (
    <section className="card stack">
      <div className="control-row">
        <label>
          Provider
          <input
            defaultValue={filters.provider}
            key={filters.provider}
            placeholder="any"
            onBlur={(e) => e.target.value.trim() !== filters.provider && set({ provider: e.target.value.trim() })}
            onKeyDown={(e) => {
              if (e.key === "Enter") set({ provider: e.currentTarget.value.trim() });
            }}
          />
        </label>
        <label>
          Close basis
          <select value={filters.close_basis} onChange={(e) => set({ close_basis: e.target.value })}>
            <option value="">any (refused if several)</option>
            <option value="raw">raw</option>
            <option value="split_adjusted">split adjusted</option>
            <option value="unverified">unverified</option>
          </select>
        </label>
        <label>
          From
          <input type="date" value={filters.start} onChange={(e) => set({ start: e.target.value })} />
        </label>
        <label>
          To
          <input type="date" value={filters.end} onChange={(e) => set({ end: e.target.value })} />
        </label>
        <label>
          Revisions
          <select value={filters.revisions} onChange={(e) => set({ revisions: e.target.value === "all" ? "all" : null })}>
            <option value="latest">latest per date</option>
            <option value="all">every stored revision</option>
          </select>
        </label>
        <label>
          Order
          <select value={filters.sort} onChange={(e) => set({ sort: e.target.value === "date" ? "date" : null })}>
            <option value="-date">newest first</option>
            <option value="date">oldest first</option>
          </select>
        </label>
      </div>
      <p className="small muted">
        One provider and one close basis at a time: series are never mixed. Values are shown exactly as
        stored.
      </p>
      {prices.isPending && <Loading label="Loading prices…" />}
      {prices.isError && <ErrorMessage error={prices.error} />}
      {prices.data && prices.data.items.length === 0 && (
        <Empty>No price is stored for this listing{filters.start || filters.end ? " in this range" : ""}.</Empty>
      )}
      {prices.data && prices.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Prices of {l.ticker}</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Open</th>
                  <th scope="col">High</th>
                  <th scope="col">Low</th>
                  <th scope="col">Close</th>
                  <th scope="col">Adj. close</th>
                  <th scope="col">Volume</th>
                  <th scope="col">Currency</th>
                  <th scope="col">Basis</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Origin</th>
                  <th scope="col">Revisions</th>
                </tr>
              </thead>
              <tbody>
                {prices.data.items.map((p) => (
                  <PriceRow key={p.observation_id} p={p} />
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={prices.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        </>
      )}
    </section>
  );
}

function PriceRow({ p }: { p: PriceOut }) {
  return (
    <tr>
      <th scope="row">{p.trade_date}</th>
      <td className="num">{p.open ?? "—"}</td>
      <td className="num">{p.high ?? "—"}</td>
      <td className="num">{p.low ?? "—"}</td>
      <td className="num">{p.close}</td>
      <td className="num" title={p.adj_close_basis}>
        {p.adj_close ?? "—"}
      </td>
      <td className="num">{p.volume ?? "—"}</td>
      <td>{p.currency}</td>
      <td>
        <Badge value={p.close_basis} tone={p.close_basis === "unverified" ? "warn" : "neutral"} />
      </td>
      <td>{p.provider_code}</td>
      <td>{originLink(p.origin)}</td>
      <td className="num">{p.revisions}</td>
    </tr>
  );
}

// ------------------------------------------------------------------ corporate actions

function ratio(a: CorporateActionOut): string {
  if (a.ratio_numerator === null || a.ratio_denominator === null) return "—";
  return `${a.ratio_numerator} for ${a.ratio_denominator}`;
}

function CorporateActions({ l, route }: { l: ListingOut; route: Route }) {
  const kind = route.query.get("kind") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const actions = useQuery({
    queryKey: ["corporate-actions", l.id, kind, offset],
    queryFn: () => api.corporateActions(l.id, { kind: kind || undefined, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="card stack">
      <div className="control-row">
        <label>
          Kind
          <select value={kind} onChange={(e) => navigate(withQuery(route, { kind: e.target.value, offset: null }))}>
            <option value="">All</option>
            <option value="split">Splits</option>
            <option value="cash_dividend">Cash dividends</option>
          </select>
        </label>
      </div>
      {actions.isPending && <Loading label="Loading corporate actions…" />}
      {actions.isError && <ErrorMessage error={actions.error} />}
      {actions.data && actions.data.items.length === 0 && (
        <Empty>No corporate action is stored for this listing.</Empty>
      )}
      {actions.data && actions.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Corporate actions of {l.ticker}</caption>
              <thead>
                <tr>
                  <th scope="col">Ex-date</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Split ratio</th>
                  <th scope="col">Amount per share</th>
                  <th scope="col">Currency</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Origin</th>
                </tr>
              </thead>
              <tbody>
                {actions.data.items.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">{a.ex_date}</th>
                    <td>{a.kind.replaceAll("_", " ")}</td>
                    <td className="num">{ratio(a)}</td>
                    <td className="num">{a.amount ?? "—"}</td>
                    <td>
                      {a.currency ?? "—"}
                      {a.currency_source && <div className="small muted">{a.currency_source.replaceAll("_", " ")}</div>}
                    </td>
                    <td>{a.provider_code}</td>
                    <td>{originLink(a.origin)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={actions.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        </>
      )}
    </section>
  );
}
