import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
      accountDetail: vi.fn(), orders: vi.fn(), journal: vi.fn(), snapshots: vi.fn(),
    },
  };
});

import { ReplayAccounts } from "@/components/ReplayAccounts";
import { api } from "@/lib/api";
import type { ReplayAccount } from "@/lib/types";

const accounts: ReplayAccount[] = [
  { id: 40, name: "replay:3:manual", role: "manual" },
  { id: 41, name: "replay:3:strategy:SmaCross", role: "SmaCross" },
];

function detailFor(id: number, equity: string) {
  return {
    id, name: `acct-${id}`, kind: "manual" as const, mode: "replay" as const,
    cash: equity, starting_cash: "100000", last_synced_at: null,
    sync_detail: null, equity, positions: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accountDetail).mockImplementation(async (id: number) =>
    detailFor(id, id === 40 ? "105000" : "98000"));
  vi.mocked(api.orders).mockResolvedValue([]);
  vi.mocked(api.journal).mockResolvedValue([]);
  vi.mocked(api.snapshots).mockResolvedValue([
    { date: "2024-06-04", equity: "100000", cash: "100000" },
  ]);
});

it("shows the equity comparison with signed P&L per account", async () => {
  // Role labels render twice (comparison table + tab strip): use roles for
  // presence, unique money strings for values.
  renderWithClient(<ReplayAccounts accounts={accounts} />);
  expect(await screen.findByRole("tab", { name: "Manual" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "SmaCross" })).toBeInTheDocument();
  expect(await screen.findByText("$105,000.00")).toBeInTheDocument();
  expect(screen.getByText("+$5,000.00")).toBeInTheDocument();
  expect(screen.getByText("$98,000.00")).toBeInTheDocument();
  expect(screen.getByText("-$2,000.00")).toBeInTheDocument();
});

it("switches account tabs and scopes orders/journal to the active account", async () => {
  renderWithClient(<ReplayAccounts accounts={accounts} />);
  await screen.findByRole("tab", { name: "Manual" });
  await waitFor(() => expect(api.orders).toHaveBeenCalledWith(40, undefined));
  await userEvent.click(screen.getByRole("tab", { name: "SmaCross" }));
  await waitFor(() => expect(api.orders).toHaveBeenCalledWith(41, undefined));
  await waitFor(() => expect(api.journal).toHaveBeenCalledWith(41));
});

it("renders the active account's trades", async () => {
  vi.mocked(api.journal).mockResolvedValue([
    { order_id: 9, symbol: "SPY", side: "buy", qty: "10", price: "104",
      commission: "0", realized_pnl: null, filled_at: "2024-06-11T21:00:00",
      note: null, account_mode: "replay" },
  ]);
  renderWithClient(<ReplayAccounts accounts={accounts} />);
  await screen.findByRole("tab", { name: "Manual" });
  expect(await screen.findByText(/10 SPY @ \$104\.00/)).toBeInTheDocument();
});
