import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
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
      accounts: vi.fn(),
      accountDetail: vi.fn(),
      snapshots: vi.fn(),
      orders: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { AccountProvider } from "@/app/account-context";
import DashboardPage from "@/app/page";

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "99000", starting_cash: "100000" };

beforeEach(() => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...manual,
    equity: "100100",
    positions: [{
      symbol: "SPY", qty: 10, avg_cost: "100", last_price: "110",
      market_value: "1100", unrealized_pnl: "100", realized_pnl: "0",
    }],
  });
  vi.mocked(api.snapshots).mockResolvedValue([
    { date: "2026-07-01", equity: "100000", cash: "100000" },
    { date: "2026-07-02", equity: "100050", cash: "99000" },
  ]);
  vi.mocked(api.orders).mockResolvedValue([]);
});

it("shows equity, cash, total and since-close P&L, and positions", async () => {
  renderWithClient(
    <AccountProvider>
      <DashboardPage />
    </AccountProvider>,
  );
  expect(await screen.findByText("$100,100.00")).toBeInTheDocument(); // equity
  expect(screen.getByText("$99,000.00")).toBeInTheDocument(); // cash
  // "+$100.00" appears twice by construction: total P&L stat AND the
  // position's unrealized P&L (equity−starting == unrealized here).
  expect(screen.getAllByText("+$100.00").length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText("+$50.00")).toBeInTheDocument(); // since last close
  expect(screen.getByText("SPY")).toBeInTheDocument();
  expect(screen.getByText("$110.00")).toBeInTheDocument(); // last price
});
