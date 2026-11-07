import { screen, within } from "@testing-library/react";
import { parseHash } from "../../lib/router";
import { apiError, IMPORT_ID, makeMe, mockFetch, page, renderWithSession } from "../../test/helpers";
import { unitLabel } from "../../lib/format";
import { fieldColumns, ReferencePage } from "./ReferencePage";

const REF_ID = "99999999-9999-4999-8999-999999999999";
const OLD_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function refDataset(overrides: Record<string, unknown> = {}) {
  return {
    id: REF_ID,
    provider_code: "damodaran",
    kind: "country_risk",
    template_version: "damodaran_country_risk.v1",
    edition_date: "2026-01-05",
    region: null,
    variant: null,
    lineage_key: "country_risk:2026-01-05",
    version_no: 2,
    is_current: true,
    supersedes_id: OLD_ID,
    superseded_by_id: null,
    reason: "Corrected transcription",
    record_count: 2,
    content_sha256: "c".repeat(64),
    import_id: IMPORT_ID,
    created_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

const DETAIL = {
  ...refDataset(),
  source_name: "Synthetic test source",
  source_reference: null,
  original_filename: "ctry_test.csv",
  file_sha256: "d".repeat(64),
  import_status: "ingested",
};

function value(record_key: string, field: string, v: string | null, text: string | null, unit: string, row: number) {
  return { record_key, field, value: v, value_text: text, unit, import_row: row, locator: `row ${row}` };
}

const RECORDS = [
  {
    record_key: "XA",
    import_row: 2,
    locator: "row 2",
    fields: {
      country: value("XA", "country", null, "Testland", "text", 2),
      country_risk_premium: value("XA", "country_risk_premium", "1.2345", null, "percent", 2),
    },
  },
  {
    record_key: "XB",
    import_row: 3,
    locator: "row 3",
    fields: {
      country: value("XB", "country", null, "Examplia", "text", 3),
      country_risk_premium: value("XB", "country_risk_premium", "0.00", null, "percent", 3),
    },
  },
];

describe("Reference data", () => {
  it("lists current versions by default, with links to each dataset", async () => {
    const { calls } = mockFetch({ "GET /api/reference/datasets": page([refDataset()]) });
    renderWithSession(<ReferencePage route={parseHash("#/reference")} />, makeMe("reviewer"));
    const link = await screen.findByRole("link", { name: "Country risk premiums" });
    expect(link).toHaveAttribute("href", `#/reference/${REF_ID}`);
    expect(calls[0]?.query.get("current_only")).toBe("true");
    expect(screen.getByRole("row", { name: /Country risk premiums/ })).toHaveTextContent("v2 current");
  });

  it("requests every version and the chosen kind from the URL", async () => {
    const { calls } = mockFetch({
      "GET /api/reference/datasets": page([refDataset({ is_current: false, superseded_by_id: REF_ID })]),
    });
    renderWithSession(<ReferencePage route={parseHash("#/reference?kind=implied_erp&versions=all")} />);
    expect(await screen.findByText("superseded")).toBeInTheDocument();
    expect(calls[0]?.query.get("current_only")).toBe("false");
    expect(calls[0]?.query.get("kind")).toBe("implied_erp");
  });

  it("shows an empty state", async () => {
    mockFetch({ "GET /api/reference/datasets": page([]) });
    renderWithSession(<ReferencePage route={parseHash("#/reference")} />);
    expect(await screen.findByText(/No reference dataset has been imported/)).toBeInTheDocument();
  });

  it("shows provenance, lineage and records exactly as stored", async () => {
    mockFetch({
      [`GET /api/reference/datasets/${REF_ID}`]: DETAIL,
      [`GET /api/reference/datasets/${REF_ID}/records`]: page(RECORDS),
    });
    renderWithSession(<ReferencePage route={parseHash(`#/reference/${REF_ID}`)} />);
    expect(await screen.findByRole("link", { name: "ctry_test.csv" })).toHaveAttribute("href", `#/imports/${IMPORT_ID}`);
    expect(screen.getByRole("link", { name: "previous version" })).toHaveAttribute("href", `#/reference/${OLD_ID}`);
    expect(screen.getByText("Corrected transcription")).toBeInTheDocument();

    const table = await screen.findByRole("table", { name: "Records of this reference dataset" });
    expect(within(table).getByRole("columnheader", { name: /country risk premium/ })).toHaveTextContent(
      "percentage points",
    );
    const xa = within(table).getByRole("row", { name: /XA/ });
    expect(xa).toHaveTextContent("Testland");
    expect(xa).toHaveTextContent("1.2345");
    expect(within(table).getByRole("row", { name: /XB/ })).toHaveTextContent("0.00");
  });

  it("filters records by key and explains a miss", async () => {
    const { calls } = mockFetch({
      [`GET /api/reference/datasets/${REF_ID}`]: DETAIL,
      [`GET /api/reference/datasets/${REF_ID}/records`]: page([]),
    });
    renderWithSession(<ReferencePage route={parseHash(`#/reference/${REF_ID}?record=ZZ`)} />);
    expect(await screen.findByText('No record has the key "ZZ".')).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/records"))?.query.get("record_key")).toBe("ZZ");
  });

  it("shows a missing dataset as not found", async () => {
    mockFetch({
      [`GET /api/reference/datasets/${REF_ID}`]: apiError(404, "not_found", "No reference dataset."),
      [`GET /api/reference/datasets/${REF_ID}/records`]: apiError(404, "not_found", "No reference dataset."),
    });
    renderWithSession(<ReferencePage route={parseHash(`#/reference/${REF_ID}`)} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });

  it("derives columns and unit labels", () => {
    expect(fieldColumns(RECORDS).map((c) => c.field)).toEqual(["country", "country_risk_premium"]);
    expect(unitLabel("percent")).toBe("percentage points");
    expect(unitLabel("text")).toBeNull();
    expect(unitLabel("multiple")).toBe("multiple");
  });
});
