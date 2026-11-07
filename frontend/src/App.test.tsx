import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { createQueryClient } from "./queryClient";
import { makeSession } from "./session";
import { apiError, makeMe, mockFetch, page, setHash } from "./test/helpers";

function renderApp() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  afterEach(() => setHash(""));

  it("shows the sign-in page when there is no session (401)", async () => {
    mockFetch({ "GET /api/auth/me": apiError(401, "not_authenticated", "Login required.") });
    renderApp();
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows the shell with navigation and the user's role once signed in", async () => {
    mockFetch({ "GET /api/auth/me": makeMe("reviewer"), "GET /api/companies": page([]) });
    setHash("#/companies");
    renderApp();
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Companies");
    expect(nav).toHaveTextContent("Action history");
    expect(screen.getByText(/Test reviewer · reviewer/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Companies" })).toHaveAttribute("aria-current", "page");
  });
});

describe("Session permissions", () => {
  it("never assumes pending permissions and explains them", () => {
    for (const role of ["admin", "analyst", "reviewer"] as const) {
      const s = makeSession(makeMe(role));
      expect(s.can("dataset:lock")).toBe(false);
      expect(s.can("data:import")).toBe(false);
      expect(s.whyNot("dataset:lock")).toMatch(/P15/);
    }
    expect(makeSession(makeMe("reviewer")).can("company:write")).toBe(false);
    expect(makeSession(makeMe("analyst", ["dataset:lock"])).can("dataset:lock")).toBe(true);
  });
});
