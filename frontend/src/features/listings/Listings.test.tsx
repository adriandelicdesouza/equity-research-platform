import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseHash } from "../../lib/router";
import {
  apiError,
  company,
  COMPANY_ID,
  IMPORT_ID,
  makeMe,
  mockFetch,
  page,
  renderWithSession,
} from "../../test/helpers";
import { ListingsPage } from "./ListingsPage";
import { buildRequest, parseSymbols } from "./RegisterListing";

const LISTING_ID = "77777777-7777-4777-8777-777777777777";

function listing(overrides: Record<string, unknown> = {}) {
  return {
    id: LISTING_ID,
    company_id: COMPANY_ID,
    ticker: "EXMP",
    exchange_mic: "XTSE",
    exchange_name: "Toronto Stock Exchange",
    trading_currency: null,
    is_primary: false,
    security_type: "common_equity",
    share_class: null,
    isin: null,
    provider_symbols: { yahoo_chart: "EXMP.TO" },
    origin: "manual_registration",
    registration_source: "TSX listed-company directory",
    ...overrides,
  };
}

const PRICE = {
  observation_id: 9,
  trade_date: "2026-01-05",
  open: "101.25",
  high: "103.10",
  low: "100.0001",
  close: "102.3456789",
  close_basis: "raw",
  adj_close: null,
  adj_close_basis: "none",
  volume: 120000,
  currency: "CAD",
  provider_code: "manual_import",
  origin: { kind: "file_import", import_id: IMPORT_ID, import_row: 4 },
  revisions: 2,
};

const SPLIT = {
  id: 3,
  kind: "split",
  ex_date: "2025-06-02",
  ratio_numerator: "4",
  ratio_denominator: "1",
  amount: null,
  currency: null,
  currency_source: null,
  provider_code: "manual_import",
  origin: { kind: "file_import", import_id: IMPORT_ID, import_row: 2 },
};

const GET_LISTING = `GET /api/listings/${LISTING_ID}`;
const GET_PRICES = `GET /api/listings/${LISTING_ID}/prices`;
const GET_ACTIONS = `GET /api/listings/${LISTING_ID}/corporate-actions`;
const POST_CCY = `POST /api/listings/${LISTING_ID}/trading-currency`;

function listingRoutes(extra: Record<string, unknown> = {}, l: Record<string, unknown> = {}) {
  return {
    [GET_LISTING]: listing(l),
    [`GET /api/companies/${COMPANY_ID}`]: company(),
    [GET_PRICES]: page([PRICE]),
    [GET_ACTIONS]: page([SPLIT]),
    ...extra,
  };
}

const listingRoute = (q = "") => parseHash(`#/listings/${LISTING_ID}${q}`);

describe("Listing workspace", () => {
  it("shows the listing, its issuer and prices exactly as stored, with origin links", async () => {
    const { calls } = mockFetch(listingRoutes());
    renderWithSession(<ListingsPage route={listingRoute()} />);
    expect(await screen.findByRole("heading", { name: "EXMP · Toronto Stock Exchange" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Example Industries" })).toHaveAttribute(
      "href",
      `#/companies/${COMPANY_ID}`,
    );
    const table = await screen.findByRole("table", { name: "Prices of EXMP" });
    const row = within(table).getByRole("row", { name: /2026-01-05/ });
    expect(row).toHaveTextContent("102.3456789");
    expect(row).toHaveTextContent("100.0001");
    expect(within(row).getByRole("link", { name: "import, row 4" })).toHaveAttribute("href", `#/imports/${IMPORT_ID}`);
    const priceCall = calls.find((c) => c.path.endsWith("/prices"));
    expect(priceCall?.query.get("sort")).toBe("-date");
    expect(priceCall?.query.get("revisions")).toBe("latest");
  });

  it("explains an ambiguous price series and passes the chosen filters", async () => {
    const { calls } = mockFetch(
      listingRoutes({
        [GET_PRICES]: (c: { query: URLSearchParams }) =>
          c.query.get("close_basis")
            ? { body: page([PRICE]) }
            : {
                status: 409,
                body: {
                  error: {
                    code: "ambiguous_series",
                    message: "Several values of 'close_basis' are stored (raw, split_adjusted); choose one.",
                  },
                },
              },
      }),
    );
    renderWithSession(<ListingsPage route={listingRoute()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a series.");
    expect(screen.getByRole("alert")).toHaveTextContent("raw, split_adjusted");

    window.location.hash = "";
    await userEvent.selectOptions(screen.getByLabelText("Close basis"), "raw");
    expect(window.location.hash).toContain("close_basis=raw");
    expect(calls.some((c) => c.path.endsWith("/prices") && c.query.get("close_basis") === null)).toBe(true);
  });

  it("shows empty price and corporate-action states", async () => {
    mockFetch(listingRoutes({ [GET_PRICES]: page([]), [GET_ACTIONS]: page([]) }));
    const { unmount } = renderWithSession(<ListingsPage route={listingRoute()} />);
    expect(await screen.findByText("No price is stored for this listing.")).toBeInTheDocument();
    unmount();
    renderWithSession(<ListingsPage route={listingRoute("?view=actions")} />);
    expect(await screen.findByText("No corporate action is stored for this listing.")).toBeInTheDocument();
  });

  it("lists corporate actions with exact ratios", async () => {
    mockFetch(listingRoutes());
    renderWithSession(<ListingsPage route={listingRoute("?view=actions")} />);
    const table = await screen.findByRole("table", { name: "Corporate actions of EXMP" });
    expect(within(table).getByRole("row", { name: /2025-06-02/ })).toHaveTextContent("4 for 1");
  });

  it("declares the trading currency with a reason and confirmation", async () => {
    const { calls } = mockFetch(
      listingRoutes({
        [POST_CCY]: {
          action_id: "act-ccy",
          action: "listing.currency",
          result: { listing: listing({ trading_currency: "CAD" }), changed: true },
        },
      }),
    );
    renderWithSession(<ListingsPage route={listingRoute()} />);
    const declare = await screen.findByRole("button", { name: "Declare…" });
    expect(declare).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Currency (ISO 4217)"), "cad");
    await userEvent.type(screen.getByLabelText(/^Reason/), "TSX listing trades in Canadian dollars.");
    await userEvent.click(screen.getByRole("button", { name: "Declare…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Declare currency" }));
    expect(await screen.findByText(/Declared CAD\./)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ currency: "CAD", reason: "TSX listing trades in Canadian dollars." });
    expect(post?.headers["x-csrf-token"]).toBe("csrf-test-token");
    expect(post?.headers["idempotency-key"]).toMatch(/^ui-/);
  });

  it("explains that a declared currency cannot change, and keeps reviewers read-only", async () => {
    mockFetch(listingRoutes({}, { trading_currency: "CAD" }));
    const { unmount } = renderWithSession(<ListingsPage route={listingRoute()} />);
    const declare = await screen.findByRole("button", { name: "Declare…" });
    expect(declare).toBeDisabled();
    expect(document.getElementById(declare.getAttribute("aria-describedby") ?? "")).toHaveTextContent(
      "declared once (CAD)",
    );
    unmount();

    mockFetch(listingRoutes());
    renderWithSession(<ListingsPage route={listingRoute()} />, makeMe("reviewer"));
    const button = await screen.findByRole("button", { name: "Declare…" });
    expect(button).toBeDisabled();
    expect(document.getElementById(button.getAttribute("aria-describedby") ?? "")).toHaveTextContent(
      "does not include the company:write permission",
    );
  });

  it("shows a 409 refusal with its recorded action", async () => {
    mockFetch(
      listingRoutes({
        [POST_CCY]: apiError(409, "action_refused", "The trading currency is already declared.", {
          action_id: "act-refused",
        }),
      }),
    );
    renderWithSession(<ListingsPage route={listingRoute()} />);
    await userEvent.type(await screen.findByLabelText("Currency (ISO 4217)"), "USD");
    await userEvent.type(screen.getByLabelText(/^Reason/), "Cross-listed in US dollars.");
    await userEvent.click(screen.getByRole("button", { name: "Declare…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Declare currency" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Refused by a business rule.");
    expect(within(alert).getByRole("link", { name: "act-refused" })).toHaveAttribute("href", "#/actions/act-refused");
  });

  it("lists listings with links to the listing and the company", async () => {
    const { calls } = mockFetch({ "GET /api/listings": page([listing()]) });
    renderWithSession(<ListingsPage route={parseHash("#/listings?ticker=EXMP")} />);
    expect(await screen.findByRole("link", { name: "EXMP" })).toHaveAttribute("href", `#/listings/${LISTING_ID}`);
    expect(calls[0]?.query.get("ticker")).toBe("EXMP");
  });
});

// ------------------------------------------------------------------ registration

const CHECK_OK = {
  listing: { ticker: "NWC", mic: "XTSE" },
  issuer: { resolution: "new" },
  suggested_provider_symbols: { yahoo_chart: "NWC.TO" },
  errors: [],
  warnings: [{ code: "possible_existing_issuer_dismissed", message: "Similar name dismissed." }],
  ok: true,
};

async function fillNewIssuer() {
  await userEvent.type(screen.getByLabelText("Legal name"), "New Workflow Corp");
  await userEvent.type(screen.getByLabelText(/^Country/), "ca");
  await userEvent.type(screen.getByLabelText("Ticker"), "NWC");
  await userEvent.type(screen.getByLabelText("Exchange MIC"), "xtse");
  await userEvent.type(screen.getByLabelText(/^Trading currency/), "cad");
  await userEvent.type(screen.getByLabelText(/^Provider symbols/), "yahoo_chart=NWC.TO");
  await userEvent.type(screen.getByLabelText("Source"), "TSX listed-company directory");
  await userEvent.type(screen.getByLabelText(/^Reason/), "Listing confirmed on the exchange.");
}

describe("Listing registration", () => {
  it("checks first, then registers exactly the checked request", async () => {
    const { calls } = mockFetch({
      "POST /api/listings/validate": CHECK_OK,
      "POST /api/listings": () => ({
        status: 201,
        body: {
          action_id: "act-reg",
          action: "listing.register",
          result: {
            listing: listing({ ticker: "NWC", trading_currency: "CAD" }),
            company_id: COMPANY_ID,
            company_created: true,
            registration_id: "88888888-8888-4888-8888-888888888888",
            warnings: [],
          },
        },
      }),
    });
    renderWithSession(<ListingsPage route={parseHash("#/listings/register")} />);
    await fillNewIssuer();
    expect(screen.getByRole("button", { name: "Register…" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/The check passed\./)).toHaveTextContent("1 warning(s)");
    expect(screen.getByText(/possible_existing_issuer_dismissed/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Register…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Register listing" }));
    expect(await screen.findByText(/Registered NWC on Toronto Stock Exchange with a new issuer\./)).toBeInTheDocument();

    const validate = calls.find((c) => c.path === "/api/listings/validate");
    const register = calls.find((c) => c.path === "/api/listings" && c.method === "POST");
    expect(register?.body).toEqual(validate?.body);
    expect(register?.body).toEqual({
      ticker: "NWC",
      mic: "XTSE",
      currency: "CAD",
      security_type: "common_equity",
      issuer: { new: { legal_name: "New Workflow Corp", country: "CA", common_name: null, reporting_currency: null } },
      source: "TSX listed-company directory",
      reason: "Listing confirmed on the exchange.",
      source_reference: null,
      share_class: null,
      isin: null,
      provider_symbols: { yahoo_chart: "NWC.TO" },
      confirm_new_issuer: false,
    });
    expect(validate?.headers["idempotency-key"]).toBeUndefined();
    expect(register?.headers["idempotency-key"]).toMatch(/^ui-/);
    expect(register?.headers["x-csrf-token"]).toBe("csrf-test-token");
  });

  it("requires a new check after the form changes, and blocks registration on errors", async () => {
    mockFetch({
      "POST /api/listings/validate": (c: { body: { ticker: string } }) => ({
        body:
          c.body.ticker === "NWC"
            ? CHECK_OK
            : { ...CHECK_OK, ok: false, errors: [{ code: "duplicate_listing", message: "NWCX on XTSE exists." }] },
      }),
    });
    renderWithSession(<ListingsPage route={parseHash("#/listings/register")} />);
    await fillNewIssuer();
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    await screen.findByText(/The check passed\./);
    expect(screen.getByRole("button", { name: "Register…" })).toBeEnabled();

    await userEvent.type(screen.getByLabelText("Ticker"), "X");
    expect(screen.getByText(/The form changed after this check/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register…" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/The check found 1 error/)).toBeInTheDocument();
    expect(screen.getByText(/duplicate_listing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register…" })).toBeDisabled();
  });

  it("presets the issuer from the company workspace link", async () => {
    mockFetch({ [`GET /api/companies/${COMPANY_ID}`]: company() });
    renderWithSession(<ListingsPage route={parseHash(`#/listings/register?company=${COMPANY_ID}`)} />);
    expect(await screen.findByRole("link", { name: "Example Industries Inc." })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Existing company" })).toBeChecked();
  });

  it("shows validation errors from the backend", async () => {
    mockFetch({
      "POST /api/listings/validate": apiError(422, "validation_error", "Invalid request.", {
        details: [{ loc: ["body", "mic"], msg: "String should match pattern" }],
      }),
    });
    renderWithSession(<ListingsPage route={parseHash("#/listings/register")} />);
    await fillNewIssuer();
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid input.");
    expect(alert).toHaveTextContent("mic: String should match pattern");
  });

  it("keeps reviewers read-only", async () => {
    mockFetch({});
    renderWithSession(<ListingsPage route={parseHash("#/listings/register")} />, makeMe("reviewer"));
    expect(screen.getByLabelText("Ticker")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Register…" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("does not include the company:write permission");
  });

  it("parses provider symbols and refuses malformed lines", () => {
    expect(parseSymbols("yahoo_chart=NWC.TO\n\nstooq: nwc.ca")).toEqual({
      value: { yahoo_chart: "NWC.TO", stooq: "nwc.ca" },
    });
    expect(parseSymbols("yahoo chart")).toEqual({ error: 'Provider symbols, line 1: write "provider=SYMBOL".' });
  });

  it("builds each issuer form exactly", () => {
    const base = {
      companyId: COMPANY_ID,
      sameTicker: "AAA",
      sameMic: "xnas",
      cik: "320193",
      legalName: "",
      country: "",
      commonName: "",
      reportingCurrency: "",
      confirmNewIssuer: true,
      ticker: "A",
      mic: "xnys",
      currency: "usd",
      securityType: "common_equity" as const,
      shareClass: "",
      isin: "",
      source: "s",
      sourceReference: "",
      reason: "reason text",
      symbols: "",
    };
    const issuer = (mode: "company" | "same_issuer" | "cik" | "new") => {
      const r = buildRequest({ ...base, mode });
      return "body" in r ? r.body.issuer : r.error;
    };
    expect(issuer("company")).toEqual({ company_id: COMPANY_ID });
    expect(issuer("same_issuer")).toEqual({ same_issuer_as: { ticker: "AAA", mic: "XNAS" } });
    expect(issuer("cik")).toEqual({ cik: 320193 });
    expect(buildRequest({ ...base, mode: "cik", cik: "12a" })).toEqual({
      error: "The CIK must be a positive whole number.",
    });
    const r = buildRequest({ ...base, mode: "company" });
    expect("body" in r && r.body.confirm_new_issuer).toBe(false);
  });
});
