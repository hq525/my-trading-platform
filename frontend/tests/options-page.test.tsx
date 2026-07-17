import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

let search = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => search }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(),
      accountDetail: vi.fn(),
      optionExpirations: vi.fn(),
      optionChain: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import OptionsPage from "@/app/options/page";
import { api } from "@/lib/api";
import type { OptionChainRow } from "@/lib/types";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "100000", starting_cash: "100000", last_synced_at: null, sync_detail: null,
};

const call: OptionChainRow = {
  symbol: "SPY260821C00625000", strike: "625", right: "call",
  bid: "4.90", ask: "5.10", last: "5.05", open_interest: "120",
  iv: "0.172", delta: "0.55", gamma: "0.01", theta: "-0.12", vega: "0.35",
};
const put: OptionChainRow = {
  symbol: "SPY260821P00600000", strike: "600", right: "put",
  bid: "1.00", ask: "1.20", last: null, open_interest: null,
  iv: null, delta: "-0.40", gamma: null, theta: null, vega: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  search = new URLSearchParams();
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...manual, equity: "100000", positions: [],
  });
  vi.mocked(api.optionExpirations).mockResolvedValue({
    underlying: "SPY", expirations: ["2026-08-21", "2026-09-18"],
  });
  vi.mocked(api.optionChain).mockResolvedValue({
    underlying: "SPY", expiry: "2026-08-21", calls: [call], puts: [put],
  });
});

function renderPage() {
  return renderWithClient(
    <AccountProvider>
      <OptionsPage />
    </AccountProvider>,
  );
}

async function loadSpy() {
  await userEvent.type(screen.getByLabelText(/underlying/i), "spy");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));
}

it("loads expirations and renders the calls chain", async () => {
  renderPage();
  await loadSpy();
  expect(await screen.findByLabelText(/expiration/i)).toBeInTheDocument();
  expect(api.optionExpirations).toHaveBeenCalledWith("SPY");
  await waitFor(() =>
    expect(api.optionChain).toHaveBeenCalledWith("SPY", "2026-08-21"));
  const row = (await screen.findByText("625")).closest("tr")!;
  expect(within(row).getByText("$4.90")).toBeInTheDocument(); // bid
  expect(within(row).getByText("$5.10")).toBeInTheDocument(); // ask
  expect(within(row).getByText("17.2%")).toBeInTheDocument(); // iv
  expect(within(row).getByText("120")).toBeInTheDocument(); // OI
});

it("switches to the puts tab and renders null fields as em dashes", async () => {
  renderPage();
  await loadSpy();
  await screen.findByText("625");
  await userEvent.click(screen.getByRole("tab", { name: /puts/i }));
  const row = (await screen.findByText("600")).closest("tr")!;
  expect(within(row).getByText("$1.00")).toBeInTheDocument();
  expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  expect(screen.queryByText("625")).not.toBeInTheDocument();
});

it("clicking a row mounts the order ticket for that contract", async () => {
  renderPage();
  await loadSpy();
  await userEvent.click(await screen.findByText("625"));
  expect(
    await screen.findByText(/Order — SPY 08\/21\/26 \$625 C/),
  ).toBeInTheDocument();
  expect(screen.getByText("Bid $4.90 · Ask $5.10")).toBeInTheDocument();
});

it("re-syncs the mounted ticket to fresh chain data on refetch", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.mocked(api.optionChain)
      .mockResolvedValueOnce({
        underlying: "SPY", expiry: "2026-08-21", calls: [call], puts: [put],
      })
      .mockResolvedValue({
        underlying: "SPY", expiry: "2026-08-21",
        calls: [{ ...call, bid: "5.00", ask: "5.30" }], puts: [put],
      });
    renderPage();
    await user.type(screen.getByLabelText(/underlying/i), "spy");
    await user.click(screen.getByRole("button", { name: /load/i }));
    await user.click(await screen.findByText("625"));
    expect(await screen.findByText("Bid $4.90 · Ask $5.10")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(30_000)); // chain refetchInterval fires
    expect(await screen.findByText("Bid $5.00 · Ask $5.30")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("preloads the underlying from the symbol query param", async () => {
  search = new URLSearchParams("symbol=SPY");
  renderPage();
  await waitFor(() => expect(api.optionExpirations).toHaveBeenCalledWith("SPY"));
  expect(await screen.findByText("625")).toBeInTheDocument();
});

it("shows the backend message when an underlying has no options", async () => {
  const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  vi.mocked(api.optionExpirations).mockRejectedValue(
    new ApiError(404, "no options listed for symbol"));
  renderPage();
  await userEvent.type(screen.getByLabelText(/underlying/i), "ZZZZ");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));
  expect(
    await screen.findByText(/no options listed for symbol/i),
  ).toBeInTheDocument();
});

it("surfaces chain fetch errors instead of claiming no contracts", async () => {
  const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  vi.mocked(api.optionChain).mockRejectedValue(new ApiError(503, "market data unavailable"));
  renderPage();
  await userEvent.type(screen.getByLabelText(/underlying/i), "spy");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));
  expect(
    await screen.findByText(/could not load the chain/i),
  ).toBeInTheDocument();
  expect(screen.queryByText("No contracts")).not.toBeInTheDocument();
});
