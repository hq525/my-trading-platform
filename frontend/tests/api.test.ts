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
