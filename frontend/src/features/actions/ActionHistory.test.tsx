import { screen } from "@testing-library/react";
import { parseHash } from "../../lib/router";
import { apiError, COMPANY_ID, makeMe, mockFetch, page, renderWithSession } from "../../test/helpers";
import { ActionHistory } from "./ActionHistory";

const SUCCESS = {
  id: "aaaaaaaa-0000-4000-8000-000000000001",
  action: "dataset.build",
  target: COMPANY_ID,
  outcome: "succeeded",
  status_code: 201,
  idempotency_key: "ui-123456789",
  created_at: "2026-03-01T12:00:00Z",
  response: { action_id: "aaaaaaaa-0000-4000-8000-000000000001", action: "dataset.build", result: { created: true } },
};
const REFUSED = {
  id: "aaaaaaaa-0000-4000-8000-000000000002",
  action: "dataset.lock",
  target: "33333333-3333-4333-8333-333333333333",
  outcome: "refused",
  status_code: 409,
  idempotency_key: null,
  created_at: "2026-03-01T13:00:00Z",
  response: { error: { code: "action_refused", message: "1 open material conflict.", action_id: "aaaaaaaa-0000-4000-8000-000000000002" } },
};

describe("Action history", () => {
  it("lists the user's own actions with outcome, target and refusal reason", async () => {
    mockFetch({ "GET /api/actions": page([REFUSED, SUCCESS]) });
    renderWithSession(<ActionHistory actionId={null} route={parseHash("#/actions")} />, makeMe("analyst"));
    expect(await screen.findByRole("link", { name: "Build dataset" })).toHaveAttribute("href", `#/actions/${SUCCESS.id}`);
    expect(screen.getByText("succeeded")).toBeInTheDocument();
    expect(screen.getByText("refused")).toBeInTheDocument();
    expect(screen.getByText("1 open material conflict.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "11111111…" })).toHaveAttribute("href", `#/companies/${COMPANY_ID}`);
    expect(screen.queryByLabelText(/Open an action by id/)).not.toBeInTheDocument();
  });

  it("offers administrators a lookup by id", async () => {
    mockFetch({ "GET /api/actions": page([]) });
    renderWithSession(<ActionHistory actionId={null} route={parseHash("#/actions")} />, makeMe("admin"));
    expect(await screen.findByText("You have not performed any workflow action yet.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Open an action by id/)).toBeInTheDocument();
  });

  it("shows one action's stored response and refusal", async () => {
    mockFetch({ [`GET /api/actions/${REFUSED.id}`]: REFUSED });
    renderWithSession(<ActionHistory actionId={REFUSED.id} route={parseHash(`#/actions/${REFUSED.id}`)} />);
    expect(await screen.findByRole("heading", { name: "Lock dataset" })).toBeInTheDocument();
    expect(screen.getByText("action_refused")).toBeInTheDocument();
    expect(screen.getAllByText(/1 open material conflict\./).length).toBeGreaterThan(0);
  });

  it("explains that other users' actions are not visible (404)", async () => {
    mockFetch({ [`GET /api/actions/${SUCCESS.id}`]: apiError(404, "not_found", `No action ${SUCCESS.id}.`) });
    renderWithSession(<ActionHistory actionId={SUCCESS.id} route={parseHash(`#/actions/${SUCCESS.id}`)} />, makeMe("reviewer"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    expect(screen.getByText(/visible to the user who performed them/)).toBeInTheDocument();
  });
});
