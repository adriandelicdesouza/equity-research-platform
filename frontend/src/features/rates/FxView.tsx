import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import { Badge, Empty, ErrorMessage, Hash, Loading, Notice, Pager } from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { HistoryFilters, providerLabel, readHistoryFilters, revisionMarks, validRange } from "./history";

const LIMIT = 50;

/**
 * The pairs the Bank of Canada adapter is configured to store (D58, `rates/model.py`
 * FX_SERIES): CAD per 1 unit of the foreign currency. Shortcuts only — whether anything is
 * stored is always read from the API.
 */
export const CONFIGURED_PAIRS: { base: string; quote: string; provider: string; series: string }[] = [
  { base: "USD", quote: "CAD", provider: "bank_of_canada", series: "FXUSDCAD" },
  { base: "EUR", quote: "CAD", provider: "bank_of_canada", series: "FXEURCAD" },
  { base: "GBP", quote: "CAD", provider: "bank_of_canada", series: "FXGBPCAD" },
];

const CCY = /^[A-Z]{3}$/;

export function FxView({ route }: { route: Route }) {
  const q = route.query;
  const base = (q.get("base") ?? "").toUpperCase();
  const quote = (q.get("quote") ?? "").toUpperCase();
  const provider = q.get("provider") ?? "";
  const f = readHistoryFilters(route);
  const pairError =
    !base && !quote
      ? null
      : !CCY.test(base) || !CCY.test(quote)
        ? "Enter both currencies as ISO 4217 codes (three letters)."
        : base === quote
          ? "The two currencies must differ."
          : null;
  const ready = base !== "" && quote !== "" && pairError === null && validRange(f);
  const configuredReverse = CONFIGURED_PAIRS.find((p) => p.base === quote && p.quote === base);

  const rates = useQuery({
    queryKey: ["fx", base, quote, provider, f],
    queryFn: () =>
      api.fx({
        base,
        quote,
        provider: provider || undefined,
        start: f.start || undefined,
        end: f.end || undefined,
        revisions: f.revisions,
        sort: f.sort,
        limit: LIMIT,
        offset: f.offset,
      }),
    placeholderData: keepPreviousData,
    enabled: ready,
  });
  const marks = rates.data
    ? revisionMarks(rates.data.items.map((r) => ({ id: r.observation_id, date: r.obs_date, revisions: r.revisions })))
    : {};

  return (
    <section className="card stack">
      <form
        className="control-row"
        onSubmit={(e) => {
          e.preventDefault();
          const d = new FormData(e.currentTarget);
          navigate(
            withQuery(route, {
              base: String(d.get("base") ?? "").trim().toUpperCase(),
              quote: String(d.get("quote") ?? "").trim().toUpperCase(),
              provider: String(d.get("provider") ?? "").trim(),
              offset: null,
            }),
          );
        }}
      >
        <label>
          Base (1 unit of)
          <input name="base" defaultValue={base} key={`b-${base}`} maxLength={3} />
        </label>
        <label>
          Quote (units per base)
          <input name="quote" defaultValue={quote} key={`q-${quote}`} maxLength={3} />
        </label>
        <label>
          Provider
          <select name="provider" defaultValue={provider} key={`p-${provider}`}>
            <option value="">any (refused if several)</option>
            <option value="bank_of_canada">Bank of Canada</option>
          </select>
        </label>
        <button type="submit">Show</button>
      </form>
      <p className="small">
        Configured Bank of Canada pairs:{" "}
        {CONFIGURED_PAIRS.map((p, i) => (
          <span key={p.series}>
            {i > 0 && " · "}
            <a href={href(["rates", "fx"], { base: p.base, quote: p.quote, provider: p.provider })}>
              {p.quote} per 1 {p.base}
            </a>
          </span>
        ))}
        . Shortcuts only: observations appear once the provider has been fetched.
      </p>
      {pairError && <p className="small error">{pairError}</p>}

      {ready && (
        <>
          <Notice tone="info">
            Stored direction: <strong>{quote} per 1 {base}</strong>. Rates are shown only in the direction the provider
            publishes; the reverse ({base} per 1 {quote}) and cross rates are never calculated.
          </Notice>
          <HistoryFilters route={route} />
          {rates.isPending && <Loading label="Loading FX rates…" />}
          {rates.isError && <ErrorMessage error={rates.error} />}
          {rates.data && rates.data.items.length === 0 && (
            <Empty>
              No {quote} per 1 {base} rate is stored
              {f.start || f.end ? ` between ${f.start || "the first date"} and ${f.end || "the last date"}` : ""}.
              {configuredReverse && (
                <>
                  {" "}
                  The provider publishes this pair the other way round:{" "}
                  <a href={href(["rates", "fx"], { base: configuredReverse.base, quote: configuredReverse.quote })}>
                    {configuredReverse.quote} per 1 {configuredReverse.base}
                  </a>
                  .
                </>
              )}
            </Empty>
          )}
          {f.revisions === "all" && (
            <Notice tone="info">
              Every stored observation is listed. For each date, the latest stored one (highest observation ID) is the
              rate in effect; earlier ones are marked superseded.
            </Notice>
          )}
          {rates.data && rates.data.items.length > 0 && (
            <>
              <div className="table-wrap">
                <table className="data">
                  <caption className="sr-only">
                    FX rates, {quote} per 1 {base}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Date</th>
                      <th scope="col">
                        Rate (stored)
                        <div className="small muted">
                          {quote} per 1 {base}
                        </div>
                      </th>
                      <th scope="col">Revisions</th>
                      <th scope="col">Provider</th>
                      <th scope="col">Provider series</th>
                      <th scope="col">Fetch</th>
                      <th scope="col">Observation ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rates.data.items.map((r) => (
                      <tr key={r.observation_id}>
                        <th scope="row">{r.obs_date}</th>
                        <td className="num" title={r.direction}>
                          {r.rate}
                        </td>
                        <td>
                          {r.revisions > 1 ? <Badge value={`${r.revisions} stored`} tone="warn" /> : "1"}
                          {marks[r.observation_id] && <div className="small muted">{marks[r.observation_id]}</div>}
                        </td>
                        <td>{providerLabel(r.provider_code)}</td>
                        <td>
                          <code>{r.provider_series_id}</code>
                        </td>
                        <td>
                          <Hash value={r.fetch_id} />
                        </td>
                        <td className="num">{r.observation_id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager
                total={rates.data.total}
                limit={LIMIT}
                offset={f.offset}
                onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
              />
            </>
          )}
        </>
      )}
      {!base && !quote && <Empty>Choose a currency pair to see its stored rates.</Empty>}
    </section>
  );
}
