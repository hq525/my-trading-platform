import type {
  Account, AccountDetail, Bar, Order, PlaceOrderBody, Quote, Snapshot,
  Stats, Strategy, StrategyRun, Trade, CreateReplaySessionBody, ReplaySession,
  ReplaySessionDetail, StepResult,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (
    res.status === 401 &&
    typeof window !== "undefined" &&
    !window.location.pathname.startsWith("/login")
  ) {
    window.location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body: keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export const api = {
  login: (password: string) => request<{ ok: boolean }>("/api/login", post({ password })),
  logout: () => request<{ ok: boolean }>("/api/logout", { method: "POST" }),
  accounts: () => request<Account[]>("/api/accounts"),
  accountDetail: (id: number) => request<AccountDetail>(`/api/accounts/${id}`),
  snapshots: (id: number) => request<Snapshot[]>(`/api/accounts/${id}/snapshots`),
  orders: (accountId: number, status?: string) =>
    request<Order[]>(`/api/accounts/${accountId}/orders${status ? `?status=${status}` : ""}`),
  placeOrder: (accountId: number, body: PlaceOrderBody) =>
    request<Order>(`/api/accounts/${accountId}/orders`, post(body)),
  cancelOrder: (orderId: number) =>
    request<Order>(`/api/orders/${orderId}/cancel`, { method: "POST" }),
  saveNote: (orderId: number, text: string) =>
    request<{ ok: boolean }>(`/api/orders/${orderId}/note`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),
  quote: (symbol: string) =>
    request<Quote>(`/api/market/quote/${encodeURIComponent(symbol)}`),
  bars: (symbol: string, limit = 200) =>
    request<Bar[]>(`/api/market/bars/${encodeURIComponent(symbol)}?limit=${limit}`),
  journal: (accountId: number) => request<Trade[]>(`/api/journal?account_id=${accountId}`),
  stats: (accountId: number) => request<Stats>(`/api/journal/stats?account_id=${accountId}`),
  strategies: () => request<Strategy[]>("/api/strategies"),
  toggleStrategy: (name: string) =>
    request<Strategy>(`/api/strategies/${encodeURIComponent(name)}/toggle`, { method: "POST" }),
  runs: (name: string, limit = 20) =>
    request<StrategyRun[]>(`/api/strategies/${encodeURIComponent(name)}/runs?limit=${limit}`),
  createReplaySession: (body: CreateReplaySessionBody) =>
    request<ReplaySessionDetail>("/api/replay/sessions", post(body)),
  replaySessions: () => request<ReplaySession[]>("/api/replay/sessions"),
  replaySession: (id: number) =>
    request<ReplaySessionDetail>(`/api/replay/sessions/${id}`),
  stepReplay: (id: number, steps = 1) =>
    request<StepResult>(`/api/replay/sessions/${id}/step?steps=${steps}`, {
      method: "POST",
    }),
  deleteReplaySession: (id: number) =>
    request<{ ok: boolean }>(`/api/replay/sessions/${id}`, { method: "DELETE" }),
  replayBars: (id: number, symbol: string, limit = 1000) =>
    request<Bar[]>(
      `/api/replay/sessions/${id}/bars/${encodeURIComponent(symbol)}?limit=${limit}`),
  replayQuote: (id: number, symbol: string) =>
    request<Quote>(
      `/api/replay/sessions/${id}/quote/${encodeURIComponent(symbol)}`),
};
