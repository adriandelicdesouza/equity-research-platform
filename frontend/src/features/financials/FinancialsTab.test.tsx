import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseHash } from "../../lib/router";
import {
  apiError,
  company,
  COMPANY_ID,
  conflict,
  dataset,
  DS_DRAFT,
  DS_LOCKED,
  DS_REPORTED,
  FY2024,
  FY2025,
  IMPORT_ID,
  mockFetch,
  page,
  renderWithSession,
} from "../../test/helpers";
import type { CompanyDetailOut } from "../../api/schema.gen";
import { FinancialsTab } from "./FinancialsTab";

const C = company() as unknown as CompanyDetailOut;

function statement(datasetId: string, basis = "standardized") {
  return {
    dataset_id: datasetId,
    basis,
    version_no: 3,
    status: "draft",
    currency: "USD",
    statement: "IS",
    periods: [FY2024, FY2025],
    rows: [
      {
        line_item_code: "IS.REVENUE",
        label: "Revenue",
        is_subtotal: false,
        value_type: "flow",
        unit: "USD",
        cells: [
          { value: "150123456789", provenance_kind: "reported", status: "ok" },
          { value: "162000000", provenance_kind: "reported", status: "ok" },
        ],
      },
      {
        line_item_code: "IS.OTHER_NONOP",
        label: "Other non-operating income",
        is_subtotal: false,
        value_type: "flow",
        unit: "USD",
        cells: [null, { value: "-2500000", provenance_kind: "derived", status: "ok" }],
      },
      {
        line_item_code: "IS.EPS_DILUTED",
        label: "Diluted EPS",
        is_subtotal: false,
        value_type: "per_share",
        unit: "USD/shares",
        cells: [{ value: null, provenance_kind: "reported", status: "ambiguous" }, { value: "6.0815", provenance_kind: "manual_import", status: "ok" }],
      },
    ],
  };
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    [`GET /api/companies/${COMPANY_ID}/datasets`]: page([
      dataset(DS_DRAFT, { version_no: 3 }),
      dataset(DS_REPORTED, { basis: "reported", version_no: 1 }),
    ]),
    [`GET /api/datasets/${DS_DRAFT}/statements/IS`]: statement(DS_DRAFT),
    [`GET /api/datasets/${DS_REPORTED}/statements/IS`]: statement(DS_REPORTED, "reported"),
    [`GET /api/datasets/${DS_DRAFT}/conflicts`]: page([conflict()]),
    [`GET /api/datasets/${DS_REPORTED}/conflicts`]: page([]),
    ...extra,
  };
}

describe("Financial statement grid", () => {
  it("defaults to the newest draft when nothing is locked and states its basis and version", async () => {
    const { calls } = mockFetch(routes());
    renderWithSession(<FinancialsTab company={C} route={parseHash("#/companies/x/financials")} />);
    expect(await screen.findByText(/Proposed by default/)).toBeInTheDocument();
    const picker = screen.getByLabelText("Dataset version");
    expect(picker).toHaveValue(DS_DRAFT);
    expect(screen.getByText("standardized", { selector: "strong" })).toBeInTheDocument();
    await screen.findByRole("table");
    expect(calls.some((c) => c.path === `/api/datasets/${DS_DRAFT}/statements/IS`)).toBe(true);
    expect(calls.some((c) => c.path.includes(DS_REPORTED))).toBe(false);
  });

  it("reads exactly the dataset named in the URL", async () => {
    const { calls } = mockFetch(routes());
    renderWithSession(
      <FinancialsTab company={C} route={parseHash(`#/companies/x/financials?dataset=${DS_REPORTED}`)} />,
    );
    await screen.findByRole("table");
    expect(screen.queryByText(/Proposed by default/)).not.toBeInTheDocument();
    expect(screen.getByText("imported statements", { exact: false })).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes(`${DS_DRAFT}/statements`))).toBe(false);
  });

  it("refuses to substitute a dataset that does not belong to the company", async () => {
    mockFetch(routes());
    renderWithSession(
      <FinancialsTab company={C} route={parseHash(`#/companies/x/financials?dataset=${DS_LOCKED}`)} />,
    );
    expect(await screen.findByText(/no other dataset is substituted automatically/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("formats values exactly, marks missing, ambiguous, derived and conflicting cells", async () => {
    mockFetch(routes());
    renderWithSession(<FinancialsTab company={C} route={parseHash("#/companies/x/financials")} />);
    const table = await screen.findByRole("table");
    // Millions with one decimal by default; exact value in the tooltip.
    const rev = within(table).getByRole("button", { name: /Revenue, FY2024: 150,123.5/ });
    expect(rev).toHaveAttribute("title", expect.stringContaining("150,123,456,789"));
    // Negative in parentheses, derived provenance.
    expect(within(table).getByRole("button", { name: /Other non-operating income, FY2025: \(2\.5\).*Derived/ })).toBeInTheDocument();
    // Missing cell.
    expect(within(table).getByText("missing")).toBeInTheDocument();
    // Ambiguous: no value shown.
    expect(within(table).getByRole("button", { name: /Diluted EPS, FY2024: ambiguous/ })).toBeInTheDocument();
    // Per-share values are never scaled.
    expect(within(table).getByRole("button", { name: /Diluted EPS, FY2025: 6\.08.*Imported/ })).toBeInTheDocument();
    // Open conflict on revenue FY2025.
    expect(within(table).getByRole("button", { name: /Revenue, FY2025: 162\.0.*conflict open/ })).toBeInTheDocument();
    expect(screen.getByText(/amounts and share counts in millions/)).toBeInTheDocument();
  });

  it("switches to exact display without rounding", async () => {
    mockFetch(routes());
    renderWithSession(<FinancialsTab company={C} route={parseHash("#/companies/x/financials")} />);
    await screen.findByRole("table");
    await userEvent.selectOptions(screen.getByLabelText("Display scale"), "0");
    await userEvent.selectOptions(screen.getByLabelText("Decimals"), "exact");
    expect(screen.getByRole("button", { name: /Revenue, FY2024: 150,123,456,789\./ })).toBeInTheDocument();
  });

  it("opens a value's provenance with SEC facts, original reported value and the conflict link", async () => {
    mockFetch(
      routes({
        [`GET /api/datasets/${DS_DRAFT}/values`]: page([
          {
            line_item_code: "IS.REVENUE",
            label: "Revenue",
            statement: "IS",
            period: FY2025,
            value: "162000000",
            unit: "USD",
            scale: 0,
            status: "ok",
            provenance_kind: "reported",
            concept: "Revenues",
            sign_multiplier: 1,
            derivation: { form: "10-K/A", restated: true },
            fact_sources: [
              {
                fact_id: 1400,
                taxonomy: "us-gaap",
                concept: "Revenues",
                unit: "USD",
                value: "162000000",
                accession_no: "0000000001-26-000009",
                form: "10-K/A",
                filed_date: "2026-03-01",
                period_start: "2025-01-01",
                period_end: "2025-12-31",
              },
            ],
            import_source: null,
          },
        ]),
      }),
    );
    renderWithSession(<FinancialsTab company={C} route={parseHash("#/companies/x/financials")} />);
    await userEvent.click(await screen.findByRole("button", { name: /Revenue, FY2025/ }));
    const panel = await screen.findByRole("complementary");
    expect(await within(panel).findByText("0000000001-26-000009")).toBeInTheDocument();
    expect(within(panel).getByText("us-gaap:Revenues")).toBeInTheDocument();
    expect(within(panel).getAllByText("162,000,000 USD")).toHaveLength(2);
    expect(within(panel).getByRole("link", { name: "Review the conflict" })).toHaveAttribute(
      "href",
      `#/companies/${COMPANY_ID}/conflicts?dataset=${DS_DRAFT}&conflict=abcdef0123456789`,
    );
  });

  it("shows the imported file, cell, document and page for imported values", async () => {
    mockFetch(
      routes({
        [`GET /api/datasets/${DS_DRAFT}/values`]: page([
          {
            line_item_code: "IS.EPS_DILUTED",
            label: "Diluted EPS",
            statement: "IS",
            period: FY2025,
            value: "6.0815",
            unit: "USD/shares",
            scale: 0,
            status: "ok",
            provenance_kind: "manual_import",
            concept: "IS.EPS_DILUTED",
            sign_multiplier: 1,
            derivation: {},
            fact_sources: [],
            import_source: {
              import_id: IMPORT_ID,
              import_value_id: 55,
              original_filename: "example_2025.csv",
              file_sha256: "c".repeat(64),
              locator: "D14",
              source_document: "Annual report 2025",
              source_page: "87",
            },
          },
        ]),
      }),
    );
    renderWithSession(<FinancialsTab company={C} route={parseHash("#/companies/x/financials")} />);
    await userEvent.click(await screen.findByRole("button", { name: /Diluted EPS, FY2025/ }));
    const panel = await screen.findByRole("complementary");
    expect(await within(panel).findByRole("link", { name: "example_2025.csv" })).toHaveAttribute("href", `#/imports/${IMPORT_ID}`);
    expect(within(panel).getByText("D14")).toBeInTheDocument();
    expect(within(panel).getByText("87")).toBeInTheDocument();
    expect(within(panel).getByText("Annual report 2025")).toBeInTheDocument();
  });

  it("explains a too-wide period range (422)", async () => {
    mockFetch(
      routes({
        [`GET /api/datasets/${DS_DRAFT}/statements/IS`]: apiError(
          422,
          "too_many_periods",
          "75 periods match; narrow fiscal_year_from/fiscal_year_to (at most 60)",
        ),
      }),
    );
    renderWithSession(<FinancialsTab company={C} route={parseHash("#/companies/x/financials?period=Q1")} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid input. 75 periods match");
  });
});
