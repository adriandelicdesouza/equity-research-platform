import { screen, waitFor, within } from "@testing-library/react";
import { parseHash } from "../../lib/router";
import { ME_KEY } from "../../queryClient";
import {
  apiError,
  company,
  COMPANY_ID,
  dataset,
  datasetDetail,
  DS_DRAFT,
  DS_LOCKED,
  DS_REPORTED,
  IMPORT_ID,
  makeMe,
  mockFetch,
  page,
  renderWithSession,
} from "../../test/helpers";
import { CompanySearch } from "./CompanySearch";
import { CompanyWorkspace } from "./CompanyWorkspace";

function routes(overrides: Record<string, unknown> = {}) {
  return {
    [`GET /api/companies/${COMPANY_ID}`]: company(),
    [`GET /api/companies/${COMPANY_ID}/datasets`]: page([
      dataset(DS_DRAFT, { version_no: 3, status: "draft" }),
      dataset(DS_REPORTED, { basis: "reported", version_no: 1, status: "draft" }),
      dataset(DS_LOCKED, { version_no: 2, status: "locked", locked_at: "2026-02-05T00:00:00Z" }),
    ]),
    [`GET /api/datasets/${DS_DRAFT}`]: datasetDetail(DS_DRAFT, { version_no: 3 }),
    [`GET /api/datasets/${DS_LOCKED}`]: datasetDetail(DS_LOCKED, {
      version_no: 2,
      status: "locked",
      open_material_conflicts: 0,
    }),
    [`GET /api/datasets/${DS_REPORTED}`]: datasetDetail(DS_REPORTED, {
      basis: "reported",
      open_material_conflicts: 0,
      source_summary: { imports: [IMPORT_ID] },
    }),
    [`GET /api/datasets/${DS_LOCKED}/integrity`]: {
      dataset_id: DS_LOCKED,
      status: "locked",
      stored_values_match: true,
      stored_checks_match: true,
      note: "Stored rows re-hashed.",
    },
    [`GET /api/imports/${IMPORT_ID}`]: {
      id: IMPORT_ID,
      original_filename: "example_2025.csv",
      source_name: "Annual report 2025",
      status: "complete",
      imported_at: "2026-02-01T00:00:00Z",
    },
    ...overrides,
  };
}

describe("Company workspace", () => {
  it("shows identity, listings, datasets with status and conflicts, integrity and imports", async () => {
    mockFetch(routes());
    renderWithSession(<CompanyWorkspace companyId={COMPANY_ID} tab="overview" route={parseHash("#/")} />);
    expect(await screen.findByRole("heading", { name: "Example Industries" })).toBeInTheDocument();
    expect(screen.getByText("US-GAAP (inferred)")).toBeInTheDocument();
    expect(screen.getByText("Nasdaq")).toBeInTheDocument();

    const table = (await screen.findByText("Financial datasets")).closest("section") as HTMLElement;
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(await within(table).findByText("1 open")).toBeInTheDocument();
    await waitFor(() => expect(within(table).getAllByText("passed 40 · failed 2")).toHaveLength(3));

    // Integrity is for the default dataset: the newest *locked* version, named explicitly.
    expect(await screen.findByText(/Integrity — standardized v2 · locked/)).toBeInTheDocument();
    expect(await screen.findByText("Stored rows re-hashed.")).toBeInTheDocument();
    // Company-scoped import history comes from the reported dataset's source summary.
    expect(await screen.findByRole("link", { name: "example_2025.csv" })).toHaveAttribute(
      "href",
      `#/imports/${IMPORT_ID}`,
    );
  });

  it("shows an empty state when no datasets exist", async () => {
    mockFetch(routes({ [`GET /api/companies/${COMPANY_ID}/datasets`]: page([]) }));
    renderWithSession(<CompanyWorkspace companyId={COMPANY_ID} tab="overview" route={parseHash("#/")} />);
    expect(await screen.findByText("No financial datasets have been built for this company.")).toBeInTheDocument();
  });

  it("shows loading, then a not-found error", async () => {
    mockFetch({ [`GET /api/companies/${COMPANY_ID}`]: apiError(404, "not_found", "No company.") });
    renderWithSession(<CompanyWorkspace companyId={COMPANY_ID} tab="overview" route={parseHash("#/")} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading company…");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found. No company.");
  });

  it("signs the user out when the session has expired (401)", async () => {
    mockFetch({ [`GET /api/companies/${COMPANY_ID}`]: apiError(401, "not_authenticated", "Login required.") });
    const { client } = renderWithSession(
      <CompanyWorkspace companyId={COMPANY_ID} tab="overview" route={parseHash("#/")} />,
      makeMe("reviewer"),
    );
    await waitFor(() => expect(client.getQueryData(ME_KEY)).toBeNull());
  });
});

describe("Company search", () => {
  it("lists matching companies with links to their workspace", async () => {
    const { calls } = mockFetch({ "GET /api/companies": page([company()]) });
    renderWithSession(<CompanySearch route={parseHash("#/companies?q=EXMP")} />);
    const link = await screen.findByRole("link", { name: "Example Industries" });
    expect(link).toHaveAttribute("href", `#/companies/${COMPANY_ID}`);
    expect(calls[0]?.query.get("q")).toBe("EXMP");
  });

  it("shows an empty state", async () => {
    mockFetch({ "GET /api/companies": page([]) });
    renderWithSession(<CompanySearch route={parseHash("#/companies?q=zzz")} />);
    expect(await screen.findByText("No company matches “zzz”.")).toBeInTheDocument();
  });
});
