/**
 * api.ts unit tests — focus on the contracts the app relies on:
 *  - Bearer token gets attached when present
 *  - Idempotency-Key is auto-attached on trade POSTs (not on GETs)
 *  - On 401, refresh is attempted and retries with the new token
 *  - Non-2xx responses throw ApiError with the detail string
 */
import { api, ApiError, tokenStore } from "../api";

describe("lib/api", () => {
  beforeEach(() => {
    (global.fetch as jest.Mock).mockReset();
  });

  afterEach(async () => {
    await tokenStore.clear();
  });

  it("attaches Bearer header when a token is stored", async () => {
    await tokenStore.set("access123", "refresh123");
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ ok: true }),
    });
    const out = await api<{ ok: boolean }>("/dashboard");
    expect(out.ok).toBe(true);
    const [, opts] = (global.fetch as jest.Mock).mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer access123");
    // No idempotency key on GET.
    expect(opts.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("auto-attaches Idempotency-Key on /trade/approve POST", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => "{}",
    });
    await api("/trade/approve", { method: "POST", body: { signal_id: "x" } });
    const [, opts] = (global.fetch as jest.Mock).mock.calls[0];
    const key = opts.headers["Idempotency-Key"];
    expect(typeof key).toBe("string");
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("does NOT attach Idempotency-Key on non-trade POSTs", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => "{}",
    });
    await api("/controls/pause-engine", { method: "POST", body: { engine: "alpha" } });
    const [, opts] = (global.fetch as jest.Mock).mock.calls[0];
    expect(opts.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("throws ApiError with detail string on 4xx", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      text: async () => JSON.stringify({ detail: "Insufficient bankroll" }),
    });
    await expect(api("/trade/approve", { method: "POST", body: {} })).rejects.toMatchObject({
      message: "Insufficient bankroll",
      status: 400,
    });
  });

  it("refreshes and retries when first call returns 401", async () => {
    await tokenStore.set("old_access", "refresh_good");
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => JSON.stringify({ detail: "Token expired" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ access_token: "new_access", refresh_token: "new_refresh" }),
        text: async () =>
          JSON.stringify({ access_token: "new_access", refresh_token: "new_refresh" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ bankroll: 10000 }),
      });
    const out = await api<{ bankroll: number }>("/dashboard");
    expect(out.bankroll).toBe(10000);

    // Verify second call to /dashboard went out with the new token.
    const third = (global.fetch as jest.Mock).mock.calls[2];
    expect(third[1].headers.Authorization).toBe("Bearer new_access");

    // And token storage was updated.
    expect(await tokenStore.get()).toBe("new_access");
  });

  it("ApiError is an Error with status", () => {
    const e = new ApiError("nope", 418);
    expect(e).toBeInstanceOf(Error);
    expect(e.status).toBe(418);
  });
});
