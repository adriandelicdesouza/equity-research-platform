import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../api/endpoints";
import { Empty, ErrorMessage, Loading, Pager } from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";
import { ListingPage } from "./ListingPage";
import { RegisterListing } from "./RegisterListing";

const LIMIT = 50;

export function ListingsPage({ route }: { route: Route }) {
  const sub = route.segments[1];
  const isRegister = sub === "register";
  if (sub && !isRegister) return <ListingPage listingId={sub} route={route} />;
  return (
    <div className="stack">
      <h1>Listings</h1>
      <nav aria-label="Listing sections" className="tabs">
        <a href={href(["listings"])} aria-current={!sub ? "page" : undefined}>
          All listings
        </a>
        <a href={href(["listings", "register"])} aria-current={isRegister ? "page" : undefined}>
          Register a listing
        </a>
      </nav>
      {isRegister ? <RegisterListing route={route} /> : <ListingIndex route={route} />}
    </div>
  );
}

function ListingIndex({ route }: { route: Route }) {
  const ticker = route.query.get("ticker") ?? "";
  const mic = route.query.get("mic") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const list = useQuery({
    queryKey: ["listings", ticker, mic, offset],
    queryFn: () => api.listings({ ticker: ticker || undefined, mic: mic || undefined, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="card stack">
      <form
        className="control-row"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          navigate(
            withQuery(route, {
              ticker: String(f.get("ticker") ?? "").trim(),
              mic: String(f.get("mic") ?? "").trim(),
              offset: null,
            }),
          );
        }}
      >
        <label>
          Ticker
          <input name="ticker" defaultValue={ticker} key={`t-${ticker}`} />
        </label>
        <label>
          MIC
          <input name="mic" defaultValue={mic} key={`m-${mic}`} maxLength={4} />
        </label>
        <button type="submit" className="secondary">
          Filter
        </button>
      </form>
      {list.isPending && <Loading />}
      {list.isError && <ErrorMessage error={list.error} />}
      {list.data && list.data.items.length === 0 && <Empty>No listing matches.</Empty>}
      {list.data && list.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">Exchange</th>
                  <th scope="col">MIC</th>
                  <th scope="col">Trading currency</th>
                  <th scope="col">Primary</th>
                  <th scope="col">Origin</th>
                  <th scope="col">Company</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((l) => (
                  <tr key={l.id}>
                    <th scope="row">
                      <a href={href(["listings", l.id])}>{l.ticker}</a>
                    </th>
                    <td>{l.exchange_name}</td>
                    <td>{l.exchange_mic ?? "—"}</td>
                    <td>{l.trading_currency ?? <span className="muted">not declared</span>}</td>
                    <td>{l.is_primary ? "yes" : "no"}</td>
                    <td>{l.origin.replaceAll("_", " ")}</td>
                    <td>
                      <a href={href(["companies", l.company_id])}>Open company</a>
                    </td>
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
