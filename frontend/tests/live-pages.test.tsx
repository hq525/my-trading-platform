import { screen, waitFor } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  })),
  CandlestickSeries: "C", HistogramSeries: "H", AreaSeries: "A",
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(), accountDetail: vi.fn(), orders: vi.fn(),
      quote: vi.fn(), bars: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import { LiveGate } from "@/app/live/live-context";
import LiveOrdersPage from "@/app/live/orders/page";
import LiveTradePage from "@/app/live/trade/page";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
const liveAcct = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0", last_synced_at: null, sync_detail: null,
};
const liveOrder: Order = {
  id: 21, account_id: 9, symbol: "AAPL", side: "buy", order_type: "limit", tif: "gtc",
  qty: "5", limit_price: "150", status: "pending", reject_reason: null,
  placed_at: "2026-07-05T15:00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
});

it("live orders page lists the live account's orders", async () => {
  vi.mocked(api.orders).mockResolvedValue([liveOrder]);
  renderWithClient(
    <LiveGate>
      <LiveOrdersPage />
    </LiveGate>,
  );
  expect(await screen.findByText("AAPL")).toBeInTheDocument();
  await waitFor(() => expect(api.orders).toHaveBeenCalledWith(9, undefined));
});

it("live trade page renders the ticket in live mode against the live account", async () => {
  vi.mocked(api.quote).mockResolvedValue({
    symbol: "SPY", price: "100", as_of: "2026-07-05T15:00:00", bid: null, ask: null,
  });
  vi.mocked(api.bars).mockResolvedValue([]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, equity: "50000", positions: [],
  });
  renderWithClient(
    <AccountProvider>
      <LiveGate>
        <LiveTradePage />
      </LiveGate>
    </AccountProvider>,
  );
  expect(
    await screen.findByRole("button", { name: /place live order/i }),
  ).toBeInTheDocument();
  await waitFor(() => expect(api.accountDetail).toHaveBeenCalledWith(9));
});
