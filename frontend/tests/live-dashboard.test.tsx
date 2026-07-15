import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

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
      accounts: vi.fn(), accountDetail: vi.fn(), snapshots: vi.fn(), orders: vi.fn(),
    },
  };
});

import { LiveGate } from "@/app/live/live-context";
import LiveDashboardPage from "@/app/live/page";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
const liveAcct = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0",
  last_synced_at: "2026-07-05T12:00:00", sync_detail: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.snapshots).mockResolvedValue([
    { date: "2026-07-04", equity: "50500", cash: "50000" },
  ]);
  vi.mocked(api.orders).mockResolvedValue([]);
});

function renderLive() {
  return renderWithClient(
    <LiveGate>
      <LiveDashboardPage />
    </LiveGate>,
  );
}

it("shows the not-configured message when no live account exists", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  renderLive();
  expect(
    await screen.findByText(
      "Live trading not configured — set PT_ALPACA_TRADING_KEY_ID / PT_ALPACA_TRADING_SECRET.",
    ),
  ).toBeInTheDocument();
  expect(api.accountDetail).not.toHaveBeenCalled();
});

it("shows equity, cash, since-close, and the sync line — but no Total P&L", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, equity: "51000", positions: [],
  });
  renderLive();
  expect(await screen.findByText("$51,000.00")).toBeInTheDocument(); // equity
  expect(screen.getByText("$50,000.00")).toBeInTheDocument(); // cash
  expect(screen.getByText("+$500.00")).toBeInTheDocument(); // since last close
  expect(screen.getByText(/Synced with Alpaca as of/)).toBeInTheDocument();
  expect(screen.queryByText("Total P&L")).not.toBeInTheDocument();
  expect(api.accountDetail).toHaveBeenCalledWith(9);
});

it("shows a warning banner when sync_detail reports position drift", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, sync_detail: "AAPL: local 10, alpaca 12", equity: "51000", positions: [],
  });
  renderLive();
  expect(await screen.findByText(/AAPL: local 10, alpaca 12/)).toBeInTheDocument();
});

it("shows the not-yet-synced note when last_synced_at is null", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, { ...liveAcct, last_synced_at: null }]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, last_synced_at: null, equity: "51000", positions: [],
  });
  renderLive();
  expect(await screen.findByText("Not yet synced with Alpaca")).toBeInTheDocument();
});
