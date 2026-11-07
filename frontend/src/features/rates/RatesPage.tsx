import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import type { RateObservationOut, RateSeriesOut } from "../../api/schema.gen";
import { Badge, Empty, ErrorMessage, Hash, KV, Loading, Notice, Pager } from "../../components/ui";
import { unitLabel } from "../../lib/format";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { FxView } from "./FxView";
import { HistoryFilters, PROVIDERS, providerLabel, readHistoryFilters, revisionMarks, validRange } from "./history";

const LIMIT = 50;

/** Interest-rate series and FX rates (D58): read-only views of stored observations. */
export function RatesPage({ route }: { route: Route }) {
  const sub = route.segments[1];
  const isFx = sub === "fx";
  const seriesCode = sub === "series" ? route.segments[2] : undefined;
  return (
    <div className="stack">
      <h1>Rates &amp; FX</h1>
      <nav aria-label="Rates sections" className="tabs">
        <a href={href(["rates"])} aria-current={!isFx ? "page" : undefined}>
          Interest-rate series
        </a>
        <a href={href(["rates", "fx"])} aria-current={isFx ? "page" : undefined}>
          FX rates
        </a>
      </nav>
      <p className="small muted">
        Read-only. Values are shown exactly as stored from the provider; nothing is converted, interpolated,
        carried forward, inverted or cross-calculated. Dates the provider did not publish are not stored.
      </p>
      {isFx ? (
        <FxView route={route} />
      ) : seriesCode ? (
        <SeriesDetail code={seriesCode} route={route} />
      ) : (
        <SeriesList route={route} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ series list

function SeriesList({ route }: { route: Route }) {
  const q = route.query;
  const filters = {
    provider: q.get("provider") ?? "",
    currency: q.get("currency") ?? "",
    rate_type: q.get("rate_type") ?? "",
  };
  const offset = Number(q.get("offset") ?? 0) || 0;
  const list = useQuery({
    queryKey: ["rate-series", filters, offset],
    queryFn: () =>
      api.rateSeries({
        provider: filters.provider || undefined,
        currency: filters.currency || undefined,
        rate_type: filters.rate_type || undefined,
        limit: LIMIT,
        offset,
      }),
    placeholderData: keepPreviousData,
  });
  const set = (changes: Record<string, string>) => navigate(withQuery(route, { ...changes, offset: null }));
  return (
    <section className="card stack">
      <div className="control-row">
        <label>
          Provider
          <select value={filters.provider} onChange={(e) => set({ provider: e.target.value })}>
            <option value="">All</option>
            {Object.entries(PROVIDERS).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Currency
          <select value={filters.currency} onChange={(e) => set({ currency: e.target.value })}>
            <option value="">All</option>
            <option value="USD">USD</option>
            <option value="CAD">CAD</option>
          </select>
        </label>
        <label>
          Rate type
          <select value={filters.rate_type} onChange={(e) => set({ rate_type: e.target.value })}>
            <option value="">All</option>
            <option value="par_yield">Par yield</option>
            <option value="benchmark_bond_yield">Benchmark bond yield</option>
          </select>
        </label>
      </div>
      {list.isPending && <Loading label="Loading rate series…" />}
      {list.isError && <ErrorMessage error={list.error} />}
      {list.data && list.data.items.length === 0 && <Empty>No rate series is registered for these filters.</Empty>}
      {list.data && list.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Interest-rate series</caption>
              <thead>
                <tr>
                  <th scope="col">Series</th>
                  <th scope="col">Description</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Currency</th>
                  <th scope="col">Tenor</th>
                  <th scope="col">Unit</th>
                  <th scope="col">Stored observations</th>
                  <th scope="col">First</th>
                  <th scope="col">Last</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((s) => (
                  <tr key={s.code}>
                    <th scope="row">
                      <a href={href(["rates", "series", s.code])}>{s.code}</a>
                    </th>
                    <td>{s.description}</td>
                    <td>
                      {providerLabel(s.provider_code)}
                      <div className="small muted">
                        <code>{s.provider_series_id}</code>
                      </div>
                    </td>
                    <td>{s.currency}</td>
                    <td>{s.tenor}</td>
                    <td>{unitLabel(s.unit) ?? s.unit}</td>
                    <td className="num">
                      {s.observation_count > 0 ? s.observation_count : <span className="muted">none stored</span>}
                    </td>
                    <td>{s.first_date ?? "—"}</td>
                    <td>{s.last_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={list.data.total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
          />
        </>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ series detail

function SeriesDetail({ code, route }: { code: string; route: Route }) {
  const series = useQuery({ queryKey: ["rate-series-detail", code], queryFn: () => api.rateSeriesDetail(code) });
  const latest = useQuery({
    queryKey: ["rate-latest", code],
    queryFn: () => api.rateObservations(code, { revisions: "latest", sort: "-date", limit: 1 }),
    enabled: series.data !== undefined && series.data.observation_count > 0,
  });
  return (
    <div className="stack">
      <nav aria-label="Breadcrumb" className="small">
        <a href={href(["rates"])}>All rate series</a> / {code}
      </nav>
      {series.isPending && <Loading label="Loading series…" />}
      {series.isError && <ErrorMessage error={series.error} />}
      {series.data && (
        <div className="grid-2">
          <SeriesSummary s={series.data} />
          <section className="card">
            <h2>Latest stored observation</h2>
            {series.data.observation_count === 0 ? (
              <Empty>No observation of this series is stored. The series is registered, but its provider has not been fetched.</Empty>
            ) : latest.isPending ? (
              <Loading />
            ) : latest.isError ? (
              <ErrorMessage error={latest.error} />
            ) : latest.data?.items[0] ? (
              <KV
                items={[
                  ["Date", latest.data.items[0].obs_date],
                  [
                    "Value",
                    <span key="v">
                      <strong>{latest.data.items[0].value}</strong> {unitLabel(latest.data.items[0].unit) ?? latest.data.items[0].unit}
                    </span>,
                  ],
                  ["Stored revisions for this date", String(latest.data.items[0].revisions)],
                  ["Provider fetch", <Hash key="f" value={latest.data.items[0].fetch_id} />],
                ]}
              />
            ) : (
              <Empty>No observation is stored.</Empty>
            )}
          </section>
        </div>
      )}
      {series.data && <Observations s={series.data} route={route} />}
    </div>
  );
}

function SeriesSummary({ s }: { s: RateSeriesOut }) {
  return (
    <section className="card">
      <h2>{s.description}</h2>
      <KV
        items={[
          ["Series", <code key="c">{s.code}</code>],
          ["Provider", providerLabel(s.provider_code)],
          ["Provider series", <code key="p">{s.provider_series_id}</code>],
          ["Currency", s.currency],
          ["Rate type", s.rate_type.replaceAll("_", " ")],
          ["Tenor", s.tenor_months !== null ? `${s.tenor} (${s.tenor_months} months)` : `${s.tenor} (no fixed maturity)`],
          ["Unit", unitLabel(s.unit) ?? s.unit],
          ["Quotation", s.quote_convention],
          ["Stored observations", String(s.observation_count)],
          ["Coverage", s.first_date ? `${s.first_date} to ${s.last_date ?? "?"}` : null],
        ]}
      />
      {s.unit === "percent" && (
        <p className="small muted">
          Values are percentage points as published: 4.84 means 4.84 % per annum. Conversion to a decimal
          fraction happens only at the valuation input boundary, never in storage or display.
        </p>
      )}
    </section>
  );
}

function Observations({ s, route }: { s: RateSeriesOut; route: Route }) {
  const f = readHistoryFilters(route);
  const obs = useQuery({
    queryKey: ["rate-observations", s.code, f],
    queryFn: () =>
      api.rateObservations(s.code, {
        start: f.start || undefined,
        end: f.end || undefined,
        revisions: f.revisions,
        sort: f.sort,
        limit: LIMIT,
        offset: f.offset,
      }),
    placeholderData: keepPreviousData,
    enabled: s.observation_count > 0 && validRange(f),
  });
  const unit = unitLabel(s.unit) ?? s.unit;
  const marks = obs.data ? revisionMarks(obs.data.items.map((o) => ({ id: o.observation_id, date: o.obs_date, revisions: o.revisions }))) : {};
  return (
    <section className="card stack">
      <h2>Stored observations</h2>
      <HistoryFilters route={route} />
      {s.observation_count === 0 ? (
        <Empty>No observation of this series is stored.</Empty>
      ) : (
        <>
          {obs.isPending && <Loading label="Loading observations…" />}
          {obs.isError && <ErrorMessage error={obs.error} />}
          {obs.data && obs.data.items.length === 0 && (
            <Empty>
              No observation is stored{f.start || f.end ? ` between ${f.start || "the first date"} and ${f.end || "the last date"}` : ""}.
            </Empty>
          )}
          {f.revisions === "all" && (
            <Notice tone="info">
              Every stored observation is listed. For each date, the latest stored one (highest observation ID) is the
              value in effect; earlier ones are marked superseded.
            </Notice>
          )}
          {obs.data && obs.data.items.length > 0 && (
            <>
              <div className="table-wrap">
                <table className="data">
                  <caption className="sr-only">Observations of {s.code}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Date</th>
                      <th scope="col">
                        Value
                        <div className="small muted">{unit}</div>
                      </th>
                      <th scope="col">Revisions</th>
                      <th scope="col">Provider</th>
                      <th scope="col">Fetch</th>
                      <th scope="col">Observation ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {obs.data.items.map((o) => (
                      <ObservationRow key={o.observation_id} o={o} mark={marks[o.observation_id]} />
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager
                total={obs.data.total}
                limit={LIMIT}
                offset={f.offset}
                onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
              />
            </>
          )}
        </>
      )}
    </section>
  );
}

function ObservationRow({ o, mark }: { o: RateObservationOut; mark: "superseded" | "in effect" | undefined }) {
  return (
    <tr>
      <th scope="row">{o.obs_date}</th>
      <td className="num">{o.value}</td>
      <td>
        {o.revisions > 1 ? <Badge value={`${o.revisions} stored`} tone="warn" /> : "1"}
        {mark && <div className="small muted">{mark}</div>}
      </td>
      <td>{providerLabel(o.provider_code)}</td>
      <td>
        <Hash value={o.fetch_id} />
      </td>
      <td className="num">{o.observation_id}</td>
    </tr>
  );
}
