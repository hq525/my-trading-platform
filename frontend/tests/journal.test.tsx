import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { ...actual.api, accounts: vi.fn(), journal: vi.fn(), stats: vi.fn(), saveNote: vi.fn() },
  };
});

import { AccountProvider } from "@/app/account-context";
import JournalPage from "@/app/journal/page";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.journal).mockResolvedValue([
    {
      order_id: 5, symbol: "SPY", side: "sell", qty: "5", price: "120",
      commission: "0", realized_pnl: "100", filled_at: "2026-07-02T15:30:00",
      note: "took profits into strength", account_mode: "paper" as const,
    },
    {
      order_id: 4, symbol: "SPY", side: "buy", qty: "10", price: "100",
      commission: "0", realized_pnl: null, filled_at: "2026-07-01T15:30:00",
      note: null, account_mode: "paper" as const,
    },
  ]);
  vi.mocked(api.stats).mockResolvedValue({
    closed_trades: 1, wins: 1, win_rate: 1.0, avg_gain: "100", avg_loss: null,
  });
});

it("shows stats and the trade log with notes", async () => {
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  expect(await screen.findByText("100%")).toBeInTheDocument(); // win rate
  expect(screen.getByText("took profits into strength")).toBeInTheDocument();
  expect(screen.getByText("+$100.00")).toBeInTheDocument(); // realized on the sell
});

it("saves an edited note", async () => {
  vi.mocked(api.saveNote).mockResolvedValue({ ok: true });
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  const editButtons = await screen.findAllByRole("button", { name: /edit note|add note/i });
  await userEvent.click(editButtons[editButtons.length - 1]); // the buy row (no note yet)
  await userEvent.type(screen.getByRole("textbox"), "breakout entry");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.saveNote).toHaveBeenCalledWith(4, "breakout entry"));
});

it("tags each trade with its asset class", async () => {
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  await screen.findByText("took profits into strength");
  const tags = screen.getAllByText("Stock");
  expect(tags).toHaveLength(2); // both mocked trades are SPY
});

const liveAcct = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0", last_synced_at: null, sync_detail: null,
};
const liveTrade = {
  order_id: 21, symbol: "AAPL", side: "buy" as const, qty: "2", price: "150",
  commission: "0", realized_pnl: null, filled_at: "2026-07-03T15:30:00",
  note: null, account_mode: "live" as const,
};

it("merges live trades into the list and filters by mode", async () => {
  const paperTrade = {
    order_id: 4, symbol: "SPY", side: "buy" as const, qty: "10", price: "100",
    commission: "0", realized_pnl: null, filled_at: "2026-07-01T15:30:00",
    note: null, account_mode: "paper" as const,
  };
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
  vi.mocked(api.journal).mockImplementation(async (id: number) =>
    id === 9 ? [liveTrade] : [paperTrade],
  );
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  expect(await screen.findByText(/AAPL/)).toBeInTheDocument();
  expect(screen.getByText(/SPY/)).toBeInTheDocument();
  expect(screen.getByText("Live")).toBeInTheDocument();
  expect(screen.getByText("Paper")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^live$/i }));
  expect(screen.getByText(/AAPL/)).toBeInTheDocument();
  expect(screen.queryByText(/SPY/)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^paper$/i }));
  expect(screen.getByText(/SPY/)).toBeInTheDocument();
  expect(screen.queryByText(/AAPL/)).not.toBeInTheDocument();
});

it("excludes replay accounts from the journal fan-out", async () => {
  const replayAcct = {
    id: 42, name: "replay:3:manual", kind: "manual" as const,
    mode: "replay" as const, cash: "100000", starting_cash: "100000",
    last_synced_at: null, sync_detail: null,
  };
  vi.mocked(api.accounts).mockResolvedValue([manual, replayAcct]);
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  await screen.findByText("took profits into strength");
  expect(vi.mocked(api.journal)).toHaveBeenCalledWith(1);
  expect(vi.mocked(api.journal)).not.toHaveBeenCalledWith(42);
});
