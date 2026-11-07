import { ApiError, buildQuery, newIdempotencyKey, request, setCsrfToken } from "./client";
import { api } from "./endpoints";
import { apiError, mockFetch } from "../test/helpers";

describe("API client", () => {
  beforeEach(() => setCsrfToken("tok-123"));

  it("sends the CSRF token on POST but not on GET, with same-origin credentials", async () => {
    const { calls, fn } = mockFetch({
      "GET /api/companies": { items: [], total: 0, limit: 50, offset: 0 },
      "POST /api/datasets/d1/verify": { action_id: "a1", action: "dataset.verify", result: {} },
    });
    await api.companies({ q: "x" });
    await api.verifyDataset("d1", "key-12345678");
    expect(calls[0]?.headers["x-csrf-token"]).toBeUndefined();
    expect(calls[1]?.headers["x-csrf-token"]).toBe("tok-123");
    expect(calls[1]?.headers["idempotency-key"]).toBe("key-12345678");
    expect(fn.mock.calls[1]?.[1]?.credentials).toBe("same-origin");
  });

  it("sends JSON bodies for actions and the raw file for import previews", async () => {
    const { calls } = mockFetch({
      "POST /api/datasets/d1/discard": { action_id: "a", action: "dataset.discard", result: {} },
      "POST /api/imports/market/validate": { records: [] },
    });
    await api.discardDataset("d1", "Superseded by the next build.", "key-12345678");
    expect(calls[0]?.body).toEqual({ reason: "Superseded by the next build." });
    const file = new Blob(["a,b\n"], { type: "text/csv" });
    await api.previewImport({ kind: "market", filename: "p.csv", template: "market_prices.v1" }, file);
    expect(calls[1]?.headers["content-type"]).toBe("text/csv");
    expect(calls[1]?.body).toBe(file);
    expect(calls[1]?.query.get("template")).toBe("market_prices.v1");
    expect(calls[1]?.query.get("filename")).toBe("p.csv");
    expect(calls[1]?.headers["x-csrf-token"]).toBe("tok-123");
  });

  it("parses the error envelope, validation details and the refusal's action id", async () => {
    mockFetch({
      "POST /api/x": apiError(422, "validation_error", "Request validation failed.", {
        details: [{ loc: ["body", "reason"], msg: "too short" }],
      }),
      "POST /api/y": apiError(409, "action_refused", "Open material conflicts.", { action_id: "act-9" }),
    });
    const e1 = await request("POST", "/x").catch((e: unknown) => e);
    expect(e1).toBeInstanceOf(ApiError);
    expect((e1 as ApiError).status).toBe(422);
    expect((e1 as ApiError).details[0]?.msg).toBe("too short");
    const e2 = (await request("POST", "/y").catch((e: unknown) => e)) as ApiError;
    expect(e2.code).toBe("action_refused");
    expect(e2.actionId).toBe("act-9");
  });

  it("reports network failures as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const e = (await request("GET", "/health").catch((x: unknown) => x)) as ApiError;
    expect(e.status).toBe(0);
    expect(e.code).toBe("network_error");
  });

  it("builds repeatable query parameters and drops empty ones", () => {
    expect(buildQuery({ line_item: ["A", "B"], q: "", x: undefined, n: 0, b: false })).toBe(
      "?line_item=A&line_item=B&n=0&b=false",
    );
  });

  it("creates idempotency keys the backend accepts", () => {
    const key = newIdempotencyKey();
    expect(key).toMatch(/^[A-Za-z0-9_.:-]{8,128}$/);
    expect(newIdempotencyKey()).not.toBe(key);
  });
});
