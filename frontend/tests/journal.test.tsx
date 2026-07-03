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

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.journal).mockResolvedValue([
    {
      order_id: 5, symbol: "SPY", side: "sell", qty: 5, price: "120",
      commission: "0", realized_pnl: "100", filled_at: "2026-07-02T15:30:00",
      note: "took profits into strength",
    },
    {
      order_id: 4, symbol: "SPY", side: "buy", qty: 10, price: "100",
      commission: "0", realized_pnl: null, filled_at: "2026-07-01T15:30:00",
      note: null,
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
