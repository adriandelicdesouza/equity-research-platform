import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CompanyDetailOut } from "../../api/schema.gen";
import { parseHash } from "../../lib/router";
import {
  apiError,
  company,
  COMPANY_ID,
  dataset,
  datasetDetail,
  DS_DRAFT,
  DS_LOCKED,
  makeMe,
  mockFetch,
  page,
  renderWithSession,
} from "../../test/helpers";
import { DatasetsTab } from "./DatasetsTab";

const C = company() as unknown as CompanyDetailOut;
const draftRoute = parseHash(`#/companies/${COMPANY_ID}/datasets?dataset=${DS_DRAFT}`);
const lockedRoute = parseHash(`#/companies/${COMPANY_ID}/datasets?dataset=${DS_LOCKED}`);

function routes(extra: Record<string, unknown> = {}, draft: Record<string, unknown> = {}) {
  return {
    [`GET /api/companies/${COMPANY_ID}/datasets`]: page([
      dataset(DS_DRAFT, { version_no: 3 }),
      dataset(DS_LOCKED, { version_no: 2, status: "locked" }),
    ]),
    [`GET /api/datasets/${DS_DRAFT}`]: datasetDetail(DS_DRAFT, { version_no: 3, ...draft }),
    [`GET /api/datasets/${DS_LOCKED}`]: datasetDetail(DS_LOCKED, {
      version_no: 2,
      status: "locked",
      open_material_conflicts: 0,
    }),
    ...extra,
  };
}

function reasonOf(name: string) {
  return screen.getByRole("button", { name }).getAttribute("aria-describedby");
}

describe("Dataset workflow controls", () => {
  it("shows lock as pending owner approval for every role, administrators included", async () => {
    for (const role of ["admin", "analyst", "reviewer"] as const) {
      mockFetch(routes());
      const { unmount } = renderWithSession(<DatasetsTab company={C} route={draftRoute} />, makeMe(role));
      const lock = await screen.findByRole("button", { name: "Lock…" });
      expect(lock).toBeDisabled();
      const why = document.getElementById(reasonOf("Lock…") ?? "");
      expect(why).toHaveTextContent("Dataset locking awaits the owner's approval (decision P15)");
      unmount();
    }
  });

  it("gives reviewers verify only, explaining why the other actions are unavailable", async () => {
    const { calls } = mockFetch(
      routes({
        [`POST /api/datasets/${DS_DRAFT}/verify`]: {
          action_id: "act-v",
          action: "dataset.verify",
          result: {
            dataset_id: DS_DRAFT,
            ok: true,
            stored_matches: true,
            validation_matches: true,
            rebuild_matches: true,
            inputs_match: true,
            mapping_matches: true,
            recomputed_sha256: "a".repeat(64),
          },
        },
      }),
    );
    renderWithSession(<DatasetsTab company={C} route={draftRoute} />, makeMe("reviewer"));
    expect(await screen.findByRole("button", { name: "Discard draft…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Build…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Rebuild draft…" })).toBeDisabled();
    expect(document.getElementById(reasonOf("Build…") ?? "")).toHaveTextContent(
      "Your role (reviewer) does not include the company:write permission.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Verify…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("passed")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.path).toBe(`/api/datasets/${DS_DRAFT}/verify`);
    expect(post?.headers["x-csrf-token"]).toBe("csrf-test-token");
  });

  it("requires a reason and confirmation before discarding a draft", async () => {
    const { calls } = mockFetch(
      routes({
        [`POST /api/datasets/${DS_DRAFT}/discard`]: {
          action_id: "act-d",
          action: "dataset.discard",
          result: { dataset: dataset(DS_DRAFT, { version_no: 3, status: "discarded" }), validation: null, conflicts: null },
        },
      }),
    );
    renderWithSession(<DatasetsTab company={C} route={draftRoute} />, makeMe("analyst"));
    await userEvent.click(await screen.findByRole("button", { name: "Discard draft…" }));
    const dialog = screen.getByRole("dialog", { name: "Discard this draft" });
    const confirm = within(dialog).getByRole("button", { name: "Discard draft" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Superseded by the corrected build.");
    await userEvent.click(confirm);
    expect(await screen.findByText(/is now/)).toHaveTextContent("standardized v3 is now discarded");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ reason: "Superseded by the corrected build." });
    expect(post?.headers["idempotency-key"]).toMatch(/^ui-/);
  });

  it("explains why a locked dataset cannot be discarded", async () => {
    mockFetch(routes());
    renderWithSession(<DatasetsTab company={C} route={lockedRoute} />, makeMe("analyst"));
    expect(await screen.findByRole("button", { name: "Discard draft…" })).toBeDisabled();
    expect(screen.getByText("Only drafts can be discarded; this version is locked.")).toBeInTheDocument();
  });

  it("with dataset:lock granted (simulated approval) still explains open conflicts and relays backend refusals", async () => {
    const approved = makeMe("analyst", ["dataset:lock"]);
    mockFetch(routes());
    const first = renderWithSession(<DatasetsTab company={C} route={draftRoute} />, approved);
    expect(await screen.findByText(/1 open material conflict\(s\) must be decided/)).toBeInTheDocument();
    first.unmount();

    mockFetch(
      routes(
        { [`POST /api/datasets/${DS_DRAFT}/lock`]: apiError(403, "pending_approval", "Permission dataset:lock awaits approval.") },
        { open_material_conflicts: 0 },
      ),
    );
    renderWithSession(<DatasetsTab company={C} route={draftRoute} />, approved);
    await userEvent.click(await screen.findByRole("button", { name: "Lock…" }));
    const dialog = screen.getByRole("dialog", { name: "Lock this dataset" });
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Verified and reviewed for valuation.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Lock" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Awaiting owner approval.");
  });

  it("builds a version and reports when nothing new was stored", async () => {
    const { calls } = mockFetch(
      routes({
        [`POST /api/companies/${COMPANY_ID}/datasets/build`]: {
          action_id: "act-b",
          action: "dataset.build",
          result: { dataset: dataset(DS_DRAFT, { version_no: 3 }), created: false, validation: null, conflicts: null },
        },
      }),
    );
    renderWithSession(<DatasetsTab company={C} route={draftRoute} />, makeMe("analyst"));
    await userEvent.click(await screen.findByRole("button", { name: "Build…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Build" }));
    expect(await screen.findByText(/Identical to the current version; nothing was stored/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      basis: "standardized",
      policy: "latest_reported",
      facts_as_of: null,
    });
  });

  it("shows a rebuild refusal (409) and a security failure (403 csrf_failed)", async () => {
    mockFetch(
      routes({
        [`POST /api/companies/${COMPANY_ID}/datasets/rebuild`]: apiError(409, "action_refused", "No open draft to rebuild.", {
          action_id: "act-x",
        }),
        [`POST /api/datasets/${DS_DRAFT}/verify`]: apiError(403, "csrf_failed", "CSRF token missing or invalid."),
      }),
    );
    renderWithSession(<DatasetsTab company={C} route={draftRoute} />, makeMe("analyst"));
    await userEvent.click(await screen.findByRole("button", { name: "Rebuild draft…" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Apply the recorded decisions.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Rebuild draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Refused by a business rule. No open draft to rebuild.");

    await userEvent.click(screen.getByRole("button", { name: "Verify…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/Security check failed/)).toBeInTheDocument();
  });
});
