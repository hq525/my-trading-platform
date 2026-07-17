import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "3" }),
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
      snapshots: vi.fn(), journal: vi.fn(),
      replaySession: vi.fn(), stepReplay: vi.fn(),
      replayBars: vi.fn(), replayQuote: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import ReplayWorkbenchPage from "@/app/replay/[id]/page";
import { api, ApiError } from "@/lib/api";
import type { ReplaySessionDetail } from "@/lib/types";

const detail: ReplaySessionDetail = {
  id: 3, name: "SPY from 2024-06-03", symbols: ["SPY", "BTC-USD"],
  start_date: "2024-06-03", cursor_date: "2024-06-10", end_date: "2024-06-28",
  exhausted: false, created_at: "2026-07-16T10:00:00",
  accounts: [
    { id: 40, name: "replay:3:manual", role: "manual" },
    { id: 41, name: "replay:3:strategy:SmaCross", role: "SmaCross" },
  ],
  coverage: [
    { symbol: "SPY", first_date: "2024-06-03", last_date: "2024-06-28" },
    { symbol: "BTC-USD", first_date: "2024-06-03", last_date: "2024-06-28" },
  ],
};

const manualDetail = {
  id: 40, name: "replay:3:manual", kind: "manual" as const,
  mode: "replay" as const, cash: "100000", starting_cash: "100000",
  last_synced_at: null, sync_detail: null, equity: "100000", positions: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  // api.accounts is consumed by AccountProvider (OrderTicket's context);
  // it is NOT unused — do not remove.
  vi.mocked(api.accounts).mockResolvedValue([]);
  vi.mocked(api.replaySession).mockResolvedValue(detail);
  vi.mocked(api.replayBars).mockResolvedValue([
    { timestamp: "2024-06-03T00:00:00", open: "100", high: "101",
      low: "99", close: "100", volume: 1000 },
  ]);
  vi.mocked(api.replayQuote).mockResolvedValue({
    symbol: "SPY", price: "100", as_of: "2024-06-10T21:00:00", bid: null, ask: null,
  });
  vi.mocked(api.accountDetail).mockResolvedValue(manualDetail);
  vi.mocked(api.orders).mockResolvedValue([]);
  vi.mocked(api.snapshots).mockResolvedValue([]);
  vi.mocked(api.journal).mockResolvedValue([]);
});

function renderPage() {
  return renderWithClient(
    <AccountProvider>
      <ReplayWorkbenchPage />
    </AccountProvider>,
  );
}

it("renders the session header, chart data, and fill-semantics note", async () => {
  renderPage();
  expect(await screen.findByText("SPY from 2024-06-03")).toBeInTheDocument();
  expect(screen.getByText(/2024-06-10/)).toBeInTheDocument();
  expect(
    screen.getByText("Market orders fill at the next bar's open · day = one bar"),
  ).toBeInTheDocument();
  await waitFor(() => expect(api.replayBars).toHaveBeenCalledWith(3, "SPY"));
  await waitFor(() => expect(api.replayQuote).toHaveBeenCalledWith(3, "SPY"));
});

it("virtual-now staleness: the replay quote is not flagged stale", async () => {
  // "0s ago" is unique to QuoteBadge ("$100.00" would also match the
  // ticket's est-cost); zero age proves now = virtual cursor time.
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  expect(await screen.findByText("0s ago")).toBeInTheDocument();
  expect(screen.queryByText(/stale/)).not.toBeInTheDocument();
});

it("steps the session and shows fills and strategy errors", async () => {
  vi.mocked(api.stepReplay).mockResolvedValue({
    cursor_date: "2024-06-11",
    fills: [{ order_id: 9, symbol: "SPY", side: "buy", qty: "10", price: "104" }],
    expired: [7], cancelled_at_exhaustion: [],
    strategy_errors: { SmaCross: "unknown symbol: TSLA" },
    exhausted: false,
  });
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.click(screen.getByRole("button", { name: "+1" }));
  await waitFor(() => expect(api.stepReplay).toHaveBeenCalledWith(3, 1));
  expect(await screen.findByText(/buy 10 SPY @ \$104\.00/)).toBeInTheDocument();
  expect(screen.getByText(/1 expired/)).toBeInTheDocument();
  // The combined pattern stays unique once Task 5 adds account tabs that
  // also render the bare strategy name.
  expect(screen.getByText(/SmaCross: unknown symbol: TSLA/)).toBeInTheDocument();
});

it("multi-step buttons pass the steps param and disable when exhausted", async () => {
  vi.mocked(api.replaySession).mockResolvedValue({ ...detail, exhausted: true });
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  expect(screen.getByText("Session exhausted — no more bars")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+5" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "+20" })).toBeDisabled();
});

it("switches the chart symbol", async () => {
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.click(screen.getByRole("button", { name: "BTC-USD" }));
  await waitFor(() => expect(api.replayBars).toHaveBeenCalledWith(3, "BTC-USD"));
});

it("shows the not-found state for a missing session", async () => {
  vi.mocked(api.replaySession).mockRejectedValue(
    new ApiError(404, "no such replay session"),
  );
  renderPage();
  expect(await screen.findByText("Replay session not found.")).toBeInTheDocument();
});

it("shows a retryable error state for non-404 failures", async () => {
  vi.mocked(api.replaySession).mockRejectedValueOnce(new Error("network down"));
  renderPage();
  expect(await screen.findByText(/Failed to load session\./)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /retry/i }));
  expect(await screen.findByText("SPY from 2024-06-03")).toBeInTheDocument();
});
