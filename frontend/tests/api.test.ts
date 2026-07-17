import { api, ApiError } from "@/lib/api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

it("returns parsed JSON on success", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([{ id: 1 }])));
  await expect(api.accounts()).resolves.toEqual([{ id: 1 }]);
});

it("throws ApiError with FastAPI detail on failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "no such account" }, 404)));
  const err = await api.accountDetail(99).catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.status).toBe(404);
  expect(err.message).toBe("no such account");
});

it("redirects to /login on 401 outside the login page", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "not authenticated" }, 401)));
  const fake = { pathname: "/", href: "" };
  Object.defineProperty(window, "location", { value: fake, writable: true });
  await api.accounts().catch(() => {});
  expect(fake.href).toBe("/login");
});

it("does not redirect on 401 from the login page itself", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "wrong password" }, 401)));
  const fake = { pathname: "/login", href: "" };
  Object.defineProperty(window, "location", { value: fake, writable: true });
  await api.login("bad").catch(() => {});
  expect(fake.href).toBe("");
});

it("falls back to statusText when detail is not a string", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify({ detail: [{ loc: ["body"], msg: "invalid" }] }), {
      status: 422,
      statusText: "Unprocessable Entity",
      headers: { "Content-Type": "application/json" },
    }),
  ));
  const err = await api.accounts().catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.message).toBe("Unprocessable Entity");
});

it("POSTs JSON bodies with content-type", async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
  vi.stubGlobal("fetch", fetchMock);
  await api.login("pw");
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/login");
  expect(init.method).toBe("POST");
  expect(init.body).toBe(JSON.stringify({ password: "pw" }));
  expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
});

it("steps a replay session with the steps param", async () => {
  const fetchMock = vi.fn(async () => jsonResponse({
    cursor_date: "2024-06-04", fills: [], expired: [],
    cancelled_at_exhaustion: [], strategy_errors: {}, exhausted: false,
  }));
  vi.stubGlobal("fetch", fetchMock);
  await api.stepReplay(3, 5);
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/replay/sessions/3/step?steps=5");
  expect(init.method).toBe("POST");
});

it("fetches replay bars with limit=1000 by default", async () => {
  const fetchMock = vi.fn(async () => jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);
  await api.replayBars(3, "BTC-USD");
  const [url] = fetchMock.mock.calls[0] as unknown as [string];
  expect(url).toBe("/api/replay/sessions/3/bars/BTC-USD?limit=1000");
});

it("creates a replay session with a JSON body", async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ id: 1 }));
  vi.stubGlobal("fetch", fetchMock);
  await api.createReplaySession({
    symbols: ["SPY"], start_date: "2024-06-03", strategies: ["SmaCross"],
  });
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/replay/sessions");
  expect(JSON.parse(init.body as string)).toEqual({
    symbols: ["SPY"], start_date: "2024-06-03", strategies: ["SmaCross"],
  });
});

it("builds option endpoint URLs", async () => {
  const fetchMock = vi.fn(async () =>
    jsonResponse({ underlying: "SPY", expirations: [] }));
  vi.stubGlobal("fetch", fetchMock);
  await api.optionExpirations("SPY");
  // Cast per this file's convention: a zero-arg vi.fn types mock.calls as [].
  const [expUrl] = fetchMock.mock.calls[0] as unknown as [string];
  expect(expUrl).toBe("/api/market/options/SPY/expirations");
  await api.optionChain("SPY", "2026-08-21");
  const [chainUrl] = fetchMock.mock.calls[1] as unknown as [string];
  expect(chainUrl).toBe("/api/market/options/SPY/chain?expiry=2026-08-21");
});
