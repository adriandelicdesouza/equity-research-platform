import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CompanyDetailOut } from "../../api/schema.gen";
import { parseHash } from "../../lib/router";
import {
  apiError,
  company,
  COMPANY_ID,
  makeMe,
  mockFetch,
  page,
  renderWithSession,
} from "../../test/helpers";
import { ClassificationTab } from "./ClassificationTab";

const SUGGESTION = {
  id: 1,
  kind: "suggested",
  classification: "non_financial",
  classifier_version: "sic-v1",
  reason: null,
  evidence: { sic: "3571" },
  created_at: "2026-01-02T00:00:00Z",
};

const C = company({
  classification: {
    effective: "non_financial",
    status: "suggested",
    latest_suggestion: SUGGESTION,
    latest_confirmation: null,
  },
}) as unknown as CompanyDetailOut;
const route = parseHash(`#/companies/${COMPANY_ID}/classification`);
const HISTORY = `GET /api/companies/${COMPANY_ID}/classifications`;
const CONFIRM = `POST /api/companies/${COMPANY_ID}/classification/confirm`;

describe("Classification", () => {
  it("shows the current status and the append-only history", async () => {
    mockFetch({ [HISTORY]: page([SUGGESTION]) });
    renderWithSession(<ClassificationTab company={C} route={route} />);
    expect(await screen.findByText("sic-v1")).toBeInTheDocument();
    expect(screen.getByText("Latest suggestion").nextSibling).toHaveTextContent("Non-financial (sic-v1)");
    expect(screen.getAllByText("suggested").length).toBeGreaterThan(0);
  });

  it("shows an empty history", async () => {
    mockFetch({ [HISTORY]: page([]) });
    renderWithSession(<ClassificationTab company={C} route={route} />);
    expect(await screen.findByText("No classification has been recorded for this company.")).toBeInTheDocument();
  });

  it("confirms with a reason, a confirmation dialog, CSRF and an idempotency key", async () => {
    const { calls } = mockFetch({
      [HISTORY]: page([SUGGESTION]),
      [CONFIRM]: () => ({
        status: 201,
        body: {
          action_id: "act-c",
          action: "classification.confirm",
          result: {
            company_id: COMPANY_ID,
            effective: "bank",
            confirmation: { ...SUGGESTION, id: 2, kind: "confirmed", classification: "bank" },
          },
        },
      }),
    });
    renderWithSession(<ClassificationTab company={C} route={route} />, makeMe("analyst"));
    const button = await screen.findByRole("button", { name: "Confirm…" });
    expect(button).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText("Classification"), "bank");
    expect(screen.getByText(/This differs from the classifier's suggestion/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^Reason/), "Deposit-taking bank holding company.");
    await userEvent.click(screen.getByRole("button", { name: "Confirm…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm classification" }));

    expect(await screen.findByText(/The effective classification is now/)).toHaveTextContent("Bank");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ classification: "bank", reason: "Deposit-taking bank holding company." });
    expect(post?.headers["x-csrf-token"]).toBe("csrf-test-token");
    expect(post?.headers["idempotency-key"]).toMatch(/^ui-/);
    expect(screen.getByRole("link", { name: "act-c" })).toHaveAttribute("href", "#/actions/act-c");
  });

  it("keeps reviewers read-only, with the reason shown", async () => {
    mockFetch({ [HISTORY]: page([SUGGESTION]) });
    renderWithSession(<ClassificationTab company={C} route={route} />, makeMe("reviewer"));
    const button = await screen.findByRole("button", { name: "Confirm…" });
    expect(button).toBeDisabled();
    expect(screen.getByLabelText("Classification")).toBeDisabled();
    expect(document.getElementById(button.getAttribute("aria-describedby") ?? "")).toHaveTextContent(
      "does not include the company:write permission",
    );
  });

  it("shows a backend refusal with its error envelope", async () => {
    mockFetch({
      [HISTORY]: page([SUGGESTION]),
      [CONFIRM]: apiError(422, "validation_error", "Invalid request.", {
        details: [{ loc: ["body", "reason"], msg: "String should have at least 10 characters" }],
      }),
    });
    renderWithSession(<ClassificationTab company={C} route={route} />);
    await userEvent.type(await screen.findByLabelText(/^Reason/), "Ten chars!!");
    await userEvent.click(screen.getByRole("button", { name: "Confirm…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm classification" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid input.");
    expect(screen.getByRole("alert")).toHaveTextContent("reason: String should have at least 10 characters");
  });

  it("explains a 403 refusal", async () => {
    mockFetch({
      [HISTORY]: page([SUGGESTION]),
      [CONFIRM]: apiError(403, "forbidden", "Missing permission company:write."),
    });
    renderWithSession(<ClassificationTab company={C} route={route} />);
    await userEvent.type(await screen.findByLabelText(/^Reason/), "Industrial company, no lending.");
    await userEvent.click(screen.getByRole("button", { name: "Confirm…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm classification" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not permitted.");
  });
});
