import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseHash } from "../../lib/router";
import { apiError, makeMe, mockFetch, page, renderWithSession } from "../../test/helpers";
import { revisionMarks } from "./history";
import { RatesPage } from "./RatesPage";

// Synthetic test values only (never real provider data).
function series(overrides: Record<string, unknown> = {}) {
  return {
    code: "UST_PAR_10Y",
    provider_code: "us_treasury",
    provider_series_id: "BC_10YEAR",
    currency: "USD",
    rate_type: "par_yield",
    tenor: "10Y",
    tenor_months: 120,
    unit: "percent",
    quote_convention: "bond-equivalent, percent per annum as published by the US Treasury",
    description: "US Treasury par yield curve rate, 10Y",
    observation_count: 3,
    first_date: "2026-01-02",
    last_date: "2026-01-06",
    ...overrides,
  };
}

function obs(id: number, date: string, value: string, revisions = 1) {
  return {
    observation_id: id,
    series_code: "UST_PAR_10Y",
    obs_date: date,
    value,
    unit: "percent",
    provider_code: "us_treasury",
    fetch_id: "f0000000-0000-4000-8000-00000000000" + String(id % 10),
    revisions,
  };
}

function fx(id: number, date: string, rate: string, revisions = 1) {
  return {
    observation_id: id,
    base: "USD",
    quote: "CAD",
    direction: "CAD per 1 USD",
    obs_date: date,
    rate,
    provider_code: "bank_of_canada",
    provider_series_id: "FXUSDCAD",
    fetch_id: "e0000000-0000-4000-8000-000000000001",
    revisions,
  };
}

const SERIES = "/api/rates/series/UST_PAR_10Y";
const OBS = `${SERIES}/observations`;
const detailRoute = (q = "") => parseHash(`#/rates/series/UST_PAR_10Y${q}`);

function detailRoutes(observations: unknown[], extra: Record<string, unknown> = {}, s = series()) {
  return {
    [`GET ${SERIES}`]: s,
    [`GET ${OBS}`]: (c: { query: URLSearchParams }) =>
      c.query.get("limit") === "1" ? { body: page(observations.slice(0, 1)) } : { body: page(observations) },
    ...extra,
  };
}

describe("Interest-rate series", () => {
  it("lists registered series with provider, unit in percentage points and explicit missing observations", async () => {
    const { calls } = mockFetch({
      "GET /api/rates/series": page([
        series(),
        series({
          code: "GOC_BMK_LONG",
          provider_code: "bank_of_canada",
          provider_series_id: "BD.CDN.LONG.DQ.YLD",
          currency: "CAD",
          rate_type: "benchmark_bond_yield",
          tenor: "LONG",
          tenor_months: null,
          description: "Government of Canada benchmark bond yield, long-term (no fixed maturity)",
          observation_count: 0,
          first_date: null,
          last_date: null,
        }),
      ]),
    });
    renderWithSession(<RatesPage route={parseHash("#/rates?provider=bank_of_canada&currency=CAD")} />, makeMe("reviewer"));
    const table = await screen.findByRole("table", { name: "Interest-rate series" });
    expect(within(table).getByRole("link", { name: "UST_PAR_10Y" })).toHaveAttribute("href", "#/rates/series/UST_PAR_10Y");
    const goc = within(table).getByRole("row", { name: /GOC_BMK_LONG/ });
    expect(goc).toHaveTextContent("Bank of Canada");
    expect(goc).toHaveTextContent("BD.CDN.LONG.DQ.YLD");
    expect(goc).toHaveTextContent("none stored");
    expect(within(table).getAllByText("percentage points")).toHaveLength(2);
    expect(calls[0]?.query.get("provider")).toBe("bank_of_canada");
    expect(calls[0]?.query.get("currency")).toBe("CAD");
  });

  it("shows the latest observation and history with exact stored values, newest first", async () => {
    const { calls } = mockFetch(
      detailRoutes([obs(3, "2026-01-06", "4.8400"), obs(2, "2026-01-05", "4.0100"), obs(1, "2026-01-02", "0.0000")]),
    );
    renderWithSession(<RatesPage route={detailRoute()} />);
    expect(await screen.findByRole("heading", { name: "US Treasury par yield curve rate, 10Y" })).toBeInTheDocument();
    expect(screen.getByText(/4\.84 means 4\.84 % per annum/)).toBeInTheDocument();
    const latest = (await screen.findByText("Latest stored observation")).closest("section");
    expect(await within(latest as HTMLElement).findByText("4.8400")).toBeInTheDocument();
    expect(latest).toHaveTextContent("4.8400 percentage points");

    const table = await screen.findByRole("table", { name: "Observations of UST_PAR_10Y" });
    expect(within(table).getByRole("columnheader", { name: /Value/ })).toHaveTextContent("percentage points");
    expect(within(table).getByRole("row", { name: /2026-01-05/ })).toHaveTextContent("4.0100");
    expect(within(table).getByRole("row", { name: /2026-01-02/ })).toHaveTextContent("0.0000");
    const history = calls.find((c) => c.path === OBS && c.query.get("limit") === "50");
    expect(history?.query.get("sort")).toBe("-date");
    expect(history?.query.get("revisions")).toBe("latest");
  });

  it("passes the date range and paginates historical observations", async () => {
    const { calls } = mockFetch({
      [`GET ${SERIES}`]: series({ observation_count: 120 }),
      [`GET ${OBS}`]: (c: { query: URLSearchParams }) => ({
        body: { ...page([obs(9, "2025-12-31", "4.5")]), total: c.query.get("limit") === "1" ? 1 : 120 },
      }),
    });
    window.location.hash = "";
    renderWithSession(<RatesPage route={detailRoute("?start=2025-01-01&end=2025-12-31&offset=50")} />);
    expect(await screen.findByText("51–100 of 120")).toBeInTheDocument();
    const history = calls.find((c) => c.path === OBS && c.query.get("limit") === "50");
    expect(history?.query.get("start")).toBe("2025-01-01");
    expect(history?.query.get("end")).toBe("2025-12-31");
    expect(history?.query.get("offset")).toBe("50");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(window.location.hash).toContain("offset=100");
  });

  it("lists every revision and marks the one in effect", async () => {
    const { calls } = mockFetch(
      detailRoutes([obs(4, "2026-01-05", "4.0200", 2), obs(2, "2026-01-05", "4.0100", 2), obs(1, "2026-01-02", "3.9", 1)]),
    );
    renderWithSession(<RatesPage route={detailRoute("?revisions=all")} />);
    const table = await screen.findByRole("table", { name: "Observations of UST_PAR_10Y" });
    const rows = within(table).getAllByRole("row", { name: /2026-01-05/ });
    expect(rows[0]).toHaveTextContent("4.0200");
    expect(rows[0]).toHaveTextContent("2 stored");
    expect(rows[0]).toHaveTextContent("in effect");
    expect(rows[1]).toHaveTextContent("superseded");
    expect(screen.getByText(/Every stored observation is listed/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === OBS && c.query.get("limit") === "50")?.query.get("revisions")).toBe("all");
  });

  it("states that a registered series has no stored observations, without requesting any", async () => {
    const { calls } = mockFetch(detailRoutes([], {}, series({ observation_count: 0, first_date: null, last_date: null })));
    renderWithSession(<RatesPage route={detailRoute()} />);
    expect(await screen.findByText(/The series is registered, but its provider has not been fetched/)).toBeInTheDocument();
    expect(screen.getByText("No observation of this series is stored.")).toBeInTheDocument();
    expect(calls.some((c) => c.path === OBS)).toBe(false);
  });

  it("says when a date range holds no observation, and refuses a reversed range locally", async () => {
    const { calls, fn } = mockFetch(
      detailRoutes([], {
        [`GET ${OBS}`]: (c: { query: URLSearchParams }) =>
          c.query.get("limit") === "1" ? { body: page([obs(3, "2026-01-06", "4.84")]) } : { body: page([]) },
      }),
    );
    const { unmount } = renderWithSession(<RatesPage route={detailRoute("?start=2020-01-01&end=2020-01-31")} />);
    expect(await screen.findByText("No observation is stored between 2020-01-01 and 2020-01-31.")).toBeInTheDocument();
    unmount();
    fn.mockClear();
    calls.length = 0;
    renderWithSession(<RatesPage route={detailRoute("?start=2020-02-01&end=2020-01-01")} />);
    expect(await screen.findByText("The start date is after the end date.")).toBeInTheDocument();
    await screen.findByText("Latest stored observation");
    expect(calls.some((c) => c.path === OBS && c.query.get("limit") === "50")).toBe(false);
  });

  it("shows an unknown series and a lost session as errors", async () => {
    mockFetch({
      [`GET ${SERIES}`]: apiError(404, "not_found", "No rate series UST_PAR_10Y."),
    });
    const { unmount } = renderWithSession(<RatesPage route={detailRoute()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    unmount();
    mockFetch({ "GET /api/rates/series": apiError(401, "not_authenticated", "Login required.") });
    renderWithSession(<RatesPage route={parseHash("#/rates")} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not signed in.");
  });
});

describe("FX rates", () => {
  const FX = "GET /api/fx/rates";

  it("asks for a pair first and offers the configured Bank of Canada pairs", async () => {
    const { calls } = mockFetch({});
    renderWithSession(<RatesPage route={parseHash("#/rates/fx")} />);
    expect(screen.getByText("Choose a currency pair to see its stored rates.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CAD per 1 USD" })).toHaveAttribute(
      "href",
      "#/rates/fx?base=USD&quote=CAD&provider=bank_of_canada",
    );
    expect(screen.getByRole("link", { name: "CAD per 1 EUR" })).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("shows stored rates exactly, in the stored direction, with provenance", async () => {
    const { calls } = mockFetch({ [FX]: page([fx(2, "2026-01-06", "1.36500"), fx(1, "2026-01-05", "1.3712")]) });
    renderWithSession(<RatesPage route={parseHash("#/rates/fx?base=USD&quote=CAD&provider=bank_of_canada")} />);
    const table = await screen.findByRole("table", { name: "FX rates, CAD per 1 USD" });
    expect(screen.getByRole("note")).toHaveTextContent("Stored direction: CAD per 1 USD");
    expect(screen.getByRole("note")).toHaveTextContent("the reverse (USD per 1 CAD) and cross rates are never calculated");
    const row = within(table).getByRole("row", { name: /2026-01-06/ });
    expect(row).toHaveTextContent("1.36500");
    expect(row).toHaveTextContent("FXUSDCAD");
    expect(row).toHaveTextContent("Bank of Canada");
    expect(within(table).getByRole("columnheader", { name: /Rate \(stored\)/ })).toHaveTextContent("CAD per 1 USD");
    expect(calls[0]?.query.get("base")).toBe("USD");
    expect(calls[0]?.query.get("quote")).toBe("CAD");
    expect(calls[0]?.query.get("provider")).toBe("bank_of_canada");
    expect(calls[0]?.query.get("sort")).toBe("-date");
  });

  it("never inverts: an empty reverse pair points to the published direction", async () => {
    const { calls } = mockFetch({ [FX]: page([]) });
    renderWithSession(<RatesPage route={parseHash("#/rates/fx?base=CAD&quote=USD")} />);
    const empty = await screen.findByText(/No USD per 1 CAD rate is stored\./);
    expect(empty).toHaveTextContent("The provider publishes this pair the other way round");
    expect(within(empty).getByRole("link", { name: "CAD per 1 USD" })).toHaveAttribute(
      "href",
      "#/rates/fx?base=USD&quote=CAD",
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.query.get("base")).toBe("CAD");
  });

  it("validates the pair locally and explains an ambiguous provider", async () => {
    const { calls } = mockFetch({
      [FX]: apiError(409, "ambiguous_series", "Several values of 'provider' are stored (bank_of_canada, other); choose one."),
    });
    const { unmount } = renderWithSession(<RatesPage route={parseHash("#/rates/fx?base=USD&quote=USD")} />);
    expect(screen.getByText("The two currencies must differ.")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    unmount();
    renderWithSession(<RatesPage route={parseHash("#/rates/fx?base=USD&quote=CAD")} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Choose a series.");
    expect(alert).toHaveTextContent("bank_of_canada, other");
  });

  it("marks FX revisions and passes the revision mode and date range", async () => {
    const { calls } = mockFetch({
      [FX]: page([fx(5, "2026-01-05", "1.3713", 2), fx(1, "2026-01-05", "1.3712", 2)]),
    });
    renderWithSession(
      <RatesPage route={parseHash("#/rates/fx?base=USD&quote=CAD&revisions=all&start=2026-01-01&end=2026-01-31")} />,
    );
    const table = await screen.findByRole("table", { name: "FX rates, CAD per 1 USD" });
    const rows = within(table).getAllByRole("row", { name: /2026-01-05/ });
    expect(rows[0]).toHaveTextContent("in effect");
    expect(rows[1]).toHaveTextContent("superseded");
    expect(calls[0]?.query.get("revisions")).toBe("all");
    expect(calls[0]?.query.get("start")).toBe("2026-01-01");
  });

  it("is read-only: only GET requests, for every role", async () => {
    for (const role of ["admin", "analyst", "reviewer"] as const) {
      const { calls } = mockFetch({ [FX]: page([fx(1, "2026-01-05", "1.3712")]) });
      const { unmount } = renderWithSession(<RatesPage route={parseHash("#/rates/fx?base=USD&quote=CAD")} />, makeMe(role));
      await screen.findByRole("table", { name: "FX rates, CAD per 1 USD" });
      expect(calls.every((c) => c.method === "GET")).toBe(true);
      const buttons = screen.getAllByRole("button").map((b) => b.textContent);
      expect(buttons).toEqual(["Show"]);
      unmount();
    }
  });
});

describe("revisionMarks", () => {
  it("marks only dates whose revisions are all on the page", () => {
    expect(
      revisionMarks([
        { id: 7, date: "2026-01-05", revisions: 2 },
        { id: 3, date: "2026-01-05", revisions: 2 },
        { id: 9, date: "2026-01-06", revisions: 3 },
        { id: 8, date: "2026-01-06", revisions: 3 },
        { id: 1, date: "2026-01-02", revisions: 1 },
      ]),
    ).toEqual({ 7: "in effect", 3: "superseded" });
  });
});
