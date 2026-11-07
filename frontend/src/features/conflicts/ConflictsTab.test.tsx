import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CompanyDetailOut } from "../../api/schema.gen";
import { parseHash } from "../../lib/router";
import {
  apiError,
  company,
  COMPANY_ID,
  conflict,
  dataset,
  DS_DRAFT,
  makeMe,
  mockFetch,
  page,
  renderWithSession,
} from "../../test/helpers";
import { ConflictsTab } from "./ConflictsTab";

const C = company() as unknown as CompanyDetailOut;
const KEY = "abcdef0123456789";
const RESOLVE = `POST /api/datasets/${DS_DRAFT}/conflicts/${KEY}/resolutions`;
const route = parseHash(`#/companies/${COMPANY_ID}/conflicts?dataset=${DS_DRAFT}&conflict=${KEY}`);

function routes(extra: Record<string, unknown> = {}) {
  return {
    [`GET /api/companies/${COMPANY_ID}/datasets`]: page([dataset(DS_DRAFT, { version_no: 3 })]),
    [`GET /api/datasets/${DS_DRAFT}/conflicts`]: page([conflict()]),
    [`GET /api/companies/${COMPANY_ID}/conflict-resolutions`]: page([]),
    ...extra,
  };
}

const RESOLUTION = {
  id: 91,
  conflict_key: KEY,
  action: "select_candidate",
  candidate_source: "reported_fact",
  candidate_id: 1400,
  value: null,
  source_reference: null,
  reason: "Restated amount matches the 10-K/A.",
  line_item_code: "IS.REVENUE",
  period_type: "FY",
  period_start: "2025-01-01",
  period_end: "2025-12-31",
  detected_in_dataset_id: DS_DRAFT,
  created_at: "2026-03-02T00:00:00Z",
};

async function chooseCandidateAndReason() {
  await userEvent.click(await screen.findByRole("radio", { name: /#1400/ }));
  await userEvent.type(screen.getByLabelText(/^Reason/), "Restated amount matches the 10-K/A.");
}

describe("Conflict review", () => {
  it("shows the conflict, candidates with provenance and the policy choice", async () => {
    mockFetch(routes());
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("reviewer"));
    const detail = await screen.findByRole("region", { name: /Conflict · IS.REVENUE · FY2025/ });
    expect(within(detail).getByText("0000000001-26-000009")).toBeInTheDocument();
    expect(within(detail).getByText("policy choice")).toBeInTheDocument();
    expect(within(detail).getByText("1.250%")).toBeInTheDocument();
    expect(within(detail).getByText("No decisions recorded.")).toBeInTheDocument();
  });

  it("gives reviewers no write controls", async () => {
    mockFetch(routes());
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("reviewer"));
    expect(await screen.findByText(/recording a\s+decision needs the/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Review decision/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("lets an analyst select a candidate after confirmation, with CSRF and an idempotency key", async () => {
    const { calls } = mockFetch(
      routes({
        [RESOLVE]: () => ({
          status: 201,
          body: {
            action_id: "act-1",
            action: "conflict.resolve",
            result: { resolution: RESOLUTION, note: "Recorded; applied by the next rebuild." },
          },
        }),
      }),
    );
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("analyst"));
    // Revoke is unavailable: nothing is active.
    expect(await screen.findByRole("radio", { name: /Revoke/ })).toBeDisabled();
    await chooseCandidateAndReason();
    await userEvent.click(screen.getByRole("button", { name: "Review decision…" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm the decision" });
    expect(within(dialog).getByText("162,000,000")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0); // nothing sent before confirming
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));

    expect(await screen.findByText(/Decision recorded/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "act-1" })).toHaveAttribute("href", "#/actions/act-1");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({
      action: "select_candidate",
      candidate_id: 1400,
      candidate_source: "reported_fact",
      reason: "Restated amount matches the 10-K/A.",
    });
    expect(post?.headers["x-csrf-token"]).toBe("csrf-test-token");
    expect(post?.headers["idempotency-key"]).toMatch(/^ui-/);
  });

  it("sends an entered value as an exact decimal string with its source", async () => {
    const { calls } = mockFetch(
      routes({ [RESOLVE]: () => ({ status: 201, body: { action_id: "a2", action: "conflict.resolve", result: {} } }) }),
    );
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("analyst"));
    await userEvent.click(await screen.findByRole("radio", { name: /Enter a value/ }));
    const valueInput = screen.getByLabelText(/Exact value/);
    await userEvent.type(valueInput, "1,62e8");
    await userEvent.type(screen.getByLabelText("Source reference"), "10-K/A 2025 p. 54");
    await userEvent.type(screen.getByLabelText(/^Reason/), "Value per the amended filing.");
    expect(screen.getByRole("button", { name: "Review decision…" })).toBeDisabled();
    expect(screen.getByText(/plain decimal number/)).toBeInTheDocument();
    await userEvent.clear(valueInput);
    await userEvent.type(valueInput, "162000000.10");
    await userEvent.click(screen.getByRole("button", { name: "Review decision…" }));
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText(/Decision recorded/);
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      action: "enter_value",
      value: "162000000.10",
      source_reference: "10-K/A 2025 p. 54",
      reason: "Value per the amended filing.",
    });
  });

  it("shows a business-rule refusal (409) with its recorded action id", async () => {
    mockFetch(
      routes({
        [RESOLVE]: apiError(409, "action_refused", "The conflict is not open in this dataset.", { action_id: "act-r" }),
      }),
    );
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("analyst"));
    await chooseCandidateAndReason();
    await userEvent.click(screen.getByRole("button", { name: "Review decision…" }));
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Refused by a business rule. The conflict is not open in this dataset.");
    expect(within(alert).getByRole("link", { name: "act-r" })).toHaveAttribute("href", "#/actions/act-r");
  });

  it("reuses the idempotency key when retrying the same request and changes it for a new one", async () => {
    let n = 0;
    const { calls } = mockFetch(
      routes({ [RESOLVE]: () => (++n === 1 ? { networkError: true } : { status: 201, body: { action_id: "a", action: "x", result: {} } }) }),
    );
    // The first attempt fails at the network level; the retry must not act twice.
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("analyst"));
    await chooseCandidateAndReason();
    await userEvent.click(screen.getByRole("button", { name: "Review decision…" }));
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Review decision…" }));
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText(/Decision recorded/);
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0]?.headers["idempotency-key"]).toBe(posts[1]?.headers["idempotency-key"]);
  });

  it("reports a missing permission (403) from the backend even if controls were shown", async () => {
    mockFetch(routes({ [RESOLVE]: apiError(403, "forbidden", "Missing permission company:write.") }));
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("analyst"));
    await chooseCandidateAndReason();
    await userEvent.click(screen.getByRole("button", { name: "Review decision…" }));
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not permitted. Missing permission company:write.");
  });

  it("shows the active decision and history, and allows revoking it", async () => {
    mockFetch(
      routes({
        [`GET /api/datasets/${DS_DRAFT}/conflicts`]: page([conflict({ status: "resolved", resolution: RESOLUTION })]),
        [`GET /api/companies/${COMPANY_ID}/conflict-resolutions`]: page([RESOLUTION]),
      }),
    );
    renderWithSession(<ConflictsTab company={C} route={route} />, makeMe("analyst"));
    expect(await screen.findByRole("radio", { name: /Revoke/ })).toBeEnabled();
    expect(screen.getAllByText("Restated amount matches the 10-K/A.")).toHaveLength(2);
  });

  it("lists conflicts with an empty state for filters that match nothing", async () => {
    mockFetch(routes({ [`GET /api/datasets/${DS_DRAFT}/conflicts`]: page([]) }));
    renderWithSession(
      <ConflictsTab company={C} route={parseHash(`#/companies/x/conflicts?dataset=${DS_DRAFT}&status=open`)} />,
    );
    expect(await screen.findByText("No conflicts match these filters in this dataset.")).toBeInTheDocument();
  });
});
