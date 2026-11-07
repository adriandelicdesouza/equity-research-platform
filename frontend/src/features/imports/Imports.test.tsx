import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseHash } from "../../lib/router";
import { apiError, IMPORT_ID, makeMe, mockFetch, page, renderWithSession } from "../../test/helpers";
import { ImportsPage } from "./ImportsPage";

const SHA = "d".repeat(64);
const preview = parseHash("#/imports/preview");

function statementReport() {
  return {
    template: "financial_statements.v1",
    filename: "example_2025.csv",
    file_sha256: SHA,
    bytes: 2048,
    file_errors: [],
    file_warnings: ["Scale row missing; units assumed."],
    issuer: { company_id: "c-1", name: "Example Industries" },
    metadata: { currency: "USD", scale: 6 },
    mapping_version: "v2",
    periods: [
      { label: "FY2025", period_type: "FY", fiscal_year: 2025, start: "2025-01-01", end: "2025-12-31", source_document: "AR 2025" },
    ],
    sec_compared: true,
    summary: { records: 6, accepted: 1, conflicting: 1, duplicate: 1, unmapped: 1, ambiguous: 1, rejected: 1, warnings: 1 },
    records: [
      { row: 5, locator: "B5", status: "accepted", key: "Revenue", line_item: "IS.REVENUE", period: "FY2025", value: "162", normalized_value: "162000000", unit: "USD", issues: [] },
      { row: 6, locator: "B6", status: "conflicting", key: "COGS", line_item: "IS.COGS", period: "FY2025", value: "90", normalized_value: "90000000", unit: "USD", issues: [{ level: "warning", code: "differs_from_sec", message: "SEC value is 91000000" }] },
      { row: 7, locator: "B7", status: "duplicate", key: "SG&A", line_item: "IS.SGA", period: "FY2025", value: "10", normalized_value: "10000000", unit: "USD", issues: [] },
      { row: 8, locator: "B8", status: "unmapped", key: "Widgets sold", line_item: null, period: "FY2025", value: "5", normalized_value: null, unit: null, issues: [{ level: "error", code: "unmapped_row", message: "No standard line item" }] },
      { row: 9, locator: "B9", status: "ambiguous", key: "Other", line_item: null, period: "FY2025", value: "3", normalized_value: null, unit: null, issues: [{ level: "error", code: "ambiguous_row", message: "Several line items match" }] },
      { row: 10, locator: "B10", status: "rejected", key: "Tax", line_item: "IS.TAX", period: "FY2025", value: "abc", normalized_value: null, unit: null, issues: [{ level: "error", code: "invalid_number", message: "Not a number" }] },
    ],
  };
}

async function uploadAndValidate(name = "example_2025.csv") {
  const input = screen.getByLabelText(/CSV file/);
  await userEvent.upload(input, new File(["Revenue,162\n"], name, { type: "text/csv" }), { applyAccept: false });
  await userEvent.click(screen.getByRole("button", { name: "Validate (preview)" }));
}

describe("Import preview", () => {
  it("validates without ingesting and shows every record status and row-level issue", async () => {
    const { calls } = mockFetch({ "POST /api/imports/statements/validate": statementReport() });
    renderWithSession(<ImportsPage route={preview} />, makeMe("analyst"));
    expect(screen.getByText(/nothing\s+is stored, ingested or archived/)).toBeInTheDocument();
    await uploadAndValidate();

    expect(await screen.findByText("Validation result")).toBeInTheDocument();
    const counts = screen.getByRole("list", { name: "Record counts" });
    for (const s of ["accepted", "conflicting", "duplicate", "unmapped", "ambiguous", "rejected"]) {
      expect(within(counts).getByText(s)).toBeInTheDocument();
    }
    expect(screen.getByText("Scale row missing; units assumed.")).toBeInTheDocument();
    expect(screen.getByText("Not a number")).toBeInTheDocument();
    expect(screen.getByText("SEC value is 91000000")).toBeInTheDocument();
    expect(screen.getAllByText(SHA).length).toBeGreaterThan(0);

    const call = calls[0];
    expect(call?.path).toBe("/api/imports/statements/validate");
    expect(call?.query.get("filename")).toBe("example_2025.csv");
    expect(call?.headers["content-type"]).toBe("text/csv");
    expect(call?.headers["x-csrf-token"]).toBe("csrf-test-token");
    expect(call?.body).toBeInstanceOf(File);
    expect(calls.filter((c) => c.path === "/api/imports/statements")).toHaveLength(0);
  });

  it("filters records by status", async () => {
    mockFetch({ "POST /api/imports/statements/validate": statementReport() });
    renderWithSession(<ImportsPage route={preview} />, makeMe("analyst"));
    await uploadAndValidate();
    await screen.findByText("Validation result");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Status" }), "rejected");
    const table = screen.getAllByRole("table").at(-1) as HTMLElement;
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("B10")).toBeInTheDocument();
  });

  it("shows ingestion as pending owner approval for administrators and analysts alike", async () => {
    for (const role of ["admin", "analyst"] as const) {
      mockFetch({ "POST /api/imports/statements/validate": statementReport() });
      const { unmount } = renderWithSession(<ImportsPage route={preview} />, makeMe(role));
      await uploadAndValidate();
      await screen.findByText("Next step: ingest this file");
      expect(screen.getByText(/File ingestion awaits the owner's approval \(decision P15\)/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Ingest…" })).toBeDisabled();
      expect(screen.getByLabelText("Source (required)")).toBeDisabled();
      unmount();
    }
  });

  it("refuses XLSX files and empty selections before any request", async () => {
    const { calls } = mockFetch({});
    renderWithSession(<ImportsPage route={preview} />, makeMe("analyst"));
    await userEvent.click(screen.getByRole("button", { name: "Validate (preview)" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a CSV file.");
    await uploadAndValidate("book.xlsx");
    expect(screen.getByRole("alert")).toHaveTextContent("XLS/XLSX");
    expect(calls).toHaveLength(0);
  });

  it("explains a file the backend refuses to validate (422)", async () => {
    mockFetch({
      "POST /api/imports/statements/validate": apiError(422, "validation_error", "Missing metadata row: issuer"),
    });
    renderWithSession(<ImportsPage route={preview} />, makeMe("analyst"));
    await uploadAndValidate();
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid input. Missing metadata row: issuer");
  });

  it("sends the reference template, edition date and region as query parameters", async () => {
    const { calls } = mockFetch({
      "POST /api/imports/reference/validate": {
        template: "damodaran_industry_betas.v1",
        lineage: { kind: "industry_betas", edition_date: "2026-01-05", region: "us", variant: null },
        filename: "betas.csv",
        file_sha256: SHA,
        bytes: 10,
        file_errors: [],
        summary: { records: 1, accepted: 1, storable: 1, removed_from_current: 0, warnings: 0 },
        outcome: "new_version_requires_supersede",
        current_version: { version_no: 1 },
        removed_keys: [],
        content_sha256: "e".repeat(64),
        records: [{ row: 2, locator: "A2", status: "accepted", record_key: "Software", storable: true, issues: [] }],
      },
    });
    renderWithSession(<ImportsPage route={preview} />, makeMe("analyst"));
    await userEvent.click(screen.getByRole("radio", { name: "Damodaran reference data" }));
    await userEvent.selectOptions(screen.getByLabelText("Template"), "damodaran_industry_betas.v1");
    await uploadAndValidate("betas.csv");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the edition date.");
    await userEvent.type(screen.getByLabelText("Edition date"), "2026-01-05");
    await userEvent.selectOptions(screen.getByLabelText("Region (betas/spreads)"), "us");
    await userEvent.click(screen.getByRole("button", { name: "Validate (preview)" }));
    expect(await screen.findByText("new version requires supersede")).toBeInTheDocument();
    const q = calls[0]?.query;
    expect(q?.get("template")).toBe("damodaran_industry_betas.v1");
    expect(q?.get("edition_date")).toBe("2026-01-05");
    expect(q?.get("region")).toBe("us");
    expect(q?.has("variant")).toBe(false);
  });

  it("gives reviewers the history but no preview form", () => {
    mockFetch({});
    renderWithSession(<ImportsPage route={preview} />, makeMe("reviewer"));
    expect(screen.getByText(/Previewing a file needs the/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Validate/ })).not.toBeInTheDocument();
  });
});

describe("Ingestion once data:import is granted (simulated owner approval)", () => {
  const approved = () => makeMe("admin", ["data:import"]);

  async function fillAndIngest() {
    await uploadAndValidate();
    await screen.findByText("Next step: ingest this file");
    const ingest = screen.getByRole("button", { name: "Ingest…" });
    expect(ingest).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Source (required)"), "Annual report 2025");
    await userEvent.click(screen.getByLabelText(/Allow a partial import/));
    expect(ingest).toBeDisabled(); // conflicting values not yet allowed
    await userEvent.click(screen.getByLabelText(/Allow conflicting values/));
    await userEvent.click(ingest);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Ingest" }));
  }

  it("sends the previewed hash and every required argument", async () => {
    const { calls } = mockFetch({
      "POST /api/imports/statements/validate": statementReport(),
      "POST /api/imports/statements": () => ({
        status: 201,
        body: { action_id: "act-imp", action: "import.statements", result: { import_id: IMPORT_ID, status: "partial" } },
      }),
    });
    renderWithSession(<ImportsPage route={preview} />, approved());
    await fillAndIngest();
    expect(await screen.findByText(/Ingested/)).toBeInTheDocument();
    const post = calls.find((c) => c.path === "/api/imports/statements");
    expect(post?.query.get("expect_sha256")).toBe(SHA);
    expect(post?.query.get("source")).toBe("Annual report 2025");
    expect(post?.query.get("allow_partial")).toBe("true");
    expect(post?.query.get("allow_conflicts")).toBe("true");
    expect(post?.headers["idempotency-key"]).toMatch(/^ui-/);
    expect(post?.headers["x-csrf-token"]).toBe("csrf-test-token");
  });

  it("explains an unavailable archive (503)", async () => {
    mockFetch({
      "POST /api/imports/statements/validate": statementReport(),
      "POST /api/imports/statements": apiError(503, "archive_unavailable", "The raw archive is not writable by the API process."),
    });
    renderWithSession(<ImportsPage route={preview} />, approved());
    await fillAndIngest();
    expect(await screen.findByRole("alert")).toHaveTextContent("Temporarily unavailable. The raw archive is not writable");
    expect(screen.getByText(/cannot write the raw-file archive/)).toBeInTheDocument();
  });

  it("shows the backend's pending-approval refusal (403) if the UI's view is stale", async () => {
    mockFetch({
      "POST /api/imports/statements/validate": statementReport(),
      "POST /api/imports/statements": apiError(403, "pending_approval", "Permission data:import awaits approval."),
    });
    renderWithSession(<ImportsPage route={preview} />, approved());
    await fillAndIngest();
    expect(await screen.findByRole("alert")).toHaveTextContent("Awaiting owner approval. Permission data:import awaits approval.");
  });
});

describe("Import history", () => {
  it("lists imports and shows a stored import with its skipped records", async () => {
    mockFetch({
      "GET /api/imports": page([
        {
          id: IMPORT_ID,
          category: "statement",
          template_version: "financial_statements.v1",
          provider_code: "manual_import",
          file_kind: "csv",
          original_filename: "example_2025.csv",
          file_sha256: SHA,
          bytes: 2048,
          source_name: "Annual report 2025",
          source_reference: null,
          status: "partial",
          record_counts: { accepted: 4, rejected: 1 },
          skipped_count: 1,
          imported_at: "2026-02-01T00:00:00Z",
        },
      ]),
      [`GET /api/imports/${IMPORT_ID}`]: {
        id: IMPORT_ID,
        category: "statement",
        template_version: "financial_statements.v1",
        provider_code: "manual_import",
        file_kind: "csv",
        original_filename: "example_2025.csv",
        file_sha256: SHA,
        bytes: 2048,
        source_name: "Annual report 2025",
        source_reference: null,
        status: "partial",
        record_counts: { accepted: 4, rejected: 1 },
        skipped_count: 1,
        imported_at: "2026-02-01T00:00:00Z",
        summary: {},
        skipped: [{ row: 10, locator: "B10", status: "rejected", key: "Tax", line_item: "IS.TAX", period: "FY2025", value: "abc", normalized_value: null, unit: null, issues: [{ level: "error", code: "invalid_number", message: "Not a number" }] }],
        skipped_truncated: false,
        stored_objects: { values: 4 },
      },
    });
    const { unmount } = renderWithSession(<ImportsPage route={parseHash("#/imports")} />, makeMe("reviewer"));
    expect(await screen.findByRole("link", { name: "example_2025.csv" })).toHaveAttribute("href", `#/imports/${IMPORT_ID}`);
    expect(screen.getByText("accepted 4 · rejected 1")).toBeInTheDocument();
    unmount();
    renderWithSession(<ImportsPage route={parseHash(`#/imports/${IMPORT_ID}`)} />, makeMe("reviewer"));
    expect(await screen.findByText("Not a number")).toBeInTheDocument();
    expect(screen.getByText("Skipped records (1)")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    mockFetch({ "GET /api/imports": page([]) });
    renderWithSession(<ImportsPage route={parseHash("#/imports")} />, makeMe("reviewer"));
    expect(await screen.findByText("No file has been imported yet.")).toBeInTheDocument();
  });
});
