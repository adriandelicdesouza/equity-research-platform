import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../api/endpoints";
import { Badge, Empty, ErrorMessage, Loading, Pager } from "../../components/ui";
import { href, navigate, type Route, withQuery } from "../../lib/router";

const LIMIT = 25;

export function CompanySearch({ route }: { route: Route }) {
  const q = route.query.get("q") ?? "";
  const offset = Number(route.query.get("offset") ?? 0) || 0;
  const [text, setText] = useState(q);

  // Debounce typing into the URL, so a search is bookmarkable and Back works.
  useEffect(() => {
    if (text === q) return;
    const t = setTimeout(() => navigate(withQuery(route, { q: text.trim(), offset: null })), 300);
    return () => clearTimeout(t);
  }, [text, q, route]);

  const companies = useQuery({
    queryKey: ["companies", q, offset],
    queryFn: () => api.companies({ q: q || undefined, sort: "name", limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="stack">
      <h1>Companies</h1>
      <form
        className="searchbar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          navigate(withQuery(route, { q: text.trim(), offset: null }));
        }}
      >
        <label className="grow">
          Search by company name or exact ticker
          <input
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Apple or AAPL"
          />
        </label>
      </form>
      {companies.isPending && <Loading />}
      {companies.isError && <ErrorMessage error={companies.error} />}
      {companies.data && companies.data.items.length === 0 && (
        <Empty>{q ? `No company matches “${q}”.` : "No companies are stored yet."}</Empty>
      )}
      {companies.data && companies.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <caption className="sr-only">Companies</caption>
            <thead>
              <tr>
                <th scope="col">Company</th>
                <th scope="col">Tickers</th>
                <th scope="col">Country</th>
                <th scope="col">Reporting currency</th>
                <th scope="col">Fiscal year end</th>
                <th scope="col">Classification</th>
              </tr>
            </thead>
            <tbody>
              {companies.data.items.map((c) => (
                <tr key={c.id}>
                  <th scope="row">
                    <a href={href(["companies", c.id])}>{c.common_name ?? c.legal_name}</a>
                    {c.common_name && c.common_name !== c.legal_name && (
                      <div className="small muted">{c.legal_name}</div>
                    )}
                  </th>
                  <td>{c.tickers.join(", ") || "—"}</td>
                  <td>{c.country ?? "—"}</td>
                  <td>{c.reporting_currency ?? "—"}</td>
                  <td>{c.fiscal_year_end_mmdd ?? "—"}</td>
                  <td>
                    <Badge value={c.financial_classification} tone="neutral" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {companies.data && (
        <Pager
          total={companies.data.total}
          limit={LIMIT}
          offset={offset}
          onChange={(o) => navigate(withQuery(route, { offset: o || null }))}
        />
      )}
    </div>
  );
}
