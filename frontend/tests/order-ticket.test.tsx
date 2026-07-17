import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(),
      accountDetail: vi.fn(),
      placeOrder: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import { OrderTicket } from "@/components/OrderTicket";
import { api } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};

function setup(quotePrice?: string, symbol = "SPY") {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol={symbol} quotePrice={quotePrice} />
    </AccountProvider>,
  );
}

it("previews cost exactly and blocks unaffordable buys", async () => {
  setup("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "9");
  expect(await screen.findByText("$900.00")).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "11"); // 1100 > 1000 cash
  expect(await screen.findByText(/insufficient cash/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("submits a market order with an idempotency key and shows the result", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 7, account_id: 1, symbol: "SPY", side: "buy", order_type: "market",
    tif: "day", qty: "5", limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [accountId, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(accountId).toBe(1);
  expect(body).toMatchObject({ symbol: "SPY", side: "buy", order_type: "market", qty: "5", tif: "day" });
  expect(typeof body.idempotency_key).toBe("string");
  expect(body.idempotency_key!.length).toBeGreaterThan(10);
  expect(body).not.toHaveProperty("limit_price");
  expect(await screen.findByText(/filled/i)).toBeInTheDocument();
});

it("shows rejection reasons from the backend", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 8, account_id: 1, symbol: "SPY", side: "buy", order_type: "limit",
    tif: "day", qty: "5", limit_price: "90", status: "rejected",
    reject_reason: "market data unavailable", placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.click(screen.getByRole("radio", { name: /limit/i }));
  await userEvent.type(screen.getByLabelText(/limit price/i), "90");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(await screen.findByText(/market data unavailable/i)).toBeInTheDocument();
  const [, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(body.order_type).toBe("limit");
  expect(body.limit_price).toBe("90");
});

it("disables submit for an unparsable limit price", async () => {
  setup("100");
  await userEvent.click(screen.getByRole("radio", { name: /limit/i }));
  await userEvent.type(screen.getByLabelText(/limit price/i), "9..5");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("clears the previous result when a new submit starts", async () => {
  vi.mocked(api.placeOrder).mockResolvedValueOnce({
    id: 9, account_id: 1, symbol: "SPY", side: "buy", order_type: "market",
    tif: "day", qty: "1", limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(await screen.findByText(/filled/i)).toBeInTheDocument();
  vi.mocked(api.placeOrder).mockImplementation(() => new Promise(() => {}));
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(screen.queryByText(/filled/i)).not.toBeInTheDocument();
});

it("allows fractional quantity and whole numbers for a crypto symbol", async () => {
  setup("65000", "BTC-USD");
  expect(screen.getByLabelText(/quantity/i)).toHaveAccessibleName(/up to 8 decimal places/i);
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "0.005");
  expect(await screen.findByText("$325.00")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place order/i })).not.toBeDisabled();
});

it("rejects fractional quantity for a stock symbol", async () => {
  setup("100", "SPY");
  expect(screen.getByLabelText(/quantity/i)).toHaveAccessibleName(/whole shares/i);
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "1.5");
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

function setupLive(quotePrice?: string, symbol = "SPY") {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol={symbol} quotePrice={quotePrice} accountId={9} live />
    </AccountProvider>,
  );
}

it("live mode requires an explicit confirmation before submitting", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 11, account_id: 9, symbol: "SPY", side: "buy", order_type: "market",
    tif: "day", qty: "5", limit_price: null, status: "pending", reject_reason: null,
    placed_at: "2026-07-05T15:00:00",
  });
  setupLive("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place live order/i }));
  expect(api.placeOrder).not.toHaveBeenCalled();
  expect(screen.getByText(/Place LIVE buy: 5 SPY, market, DAY/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [accountId] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(accountId).toBe(9); // the live account, not the paper context's
  expect(await screen.findByText(/pending/i)).toBeInTheDocument();
});

it("live confirmation can be backed out of", async () => {
  setupLive("100");
  await userEvent.click(screen.getByRole("button", { name: /place live order/i }));
  await userEvent.click(screen.getByRole("button", { name: /^back$/i }));
  expect(screen.queryByText(/Place LIVE buy/)).not.toBeInTheDocument();
  expect(api.placeOrder).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /place live order/i })).toBeInTheDocument();
});

it("live mode blocks crypto symbols and forces whole shares", async () => {
  setupLive("65000", "BTC-USD");
  expect(screen.getByLabelText(/quantity/i)).toHaveAccessibleName(/whole shares/i);
  expect(screen.getByText(/crypto is not supported in live trading/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place live order/i })).toBeDisabled();
});

const OCC = "SPY260821C00625000";

function setupOption() {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol={OCC} quotePrice="5.00" bid="4.90" ask="5.10" />
    </AccountProvider>,
  );
}

it("option mode: human label, contracts qty, x100 cost at the ask", async () => {
  setupOption();
  expect(screen.getByText(/Order — SPY 08\/21\/26 \$625 C/)).toBeInTheDocument();
  expect(screen.getByText("Bid $4.90 · Ask $5.10")).toBeInTheDocument();
  expect(screen.getByLabelText(/contracts \(whole numbers\)/i)).toBeInTheDocument();
  // qty defaults to 1: est cost = 5.10 x 100 x 1
  expect(await screen.findByText("$510.00")).toBeInTheDocument();
});

it("option mode: sell previews proceeds at the bid", async () => {
  setupOption();
  await userEvent.click(screen.getByRole("radio", { name: /sell/i }));
  await userEvent.clear(screen.getByLabelText(/contracts/i));
  await userEvent.type(screen.getByLabelText(/contracts/i), "2");
  // 4.90 x 100 x 2
  expect(await screen.findByText("$980.00")).toBeInTheDocument();
});

it("option mode: fractional contracts are invalid", async () => {
  setupOption();
  await userEvent.clear(screen.getByLabelText(/contracts/i));
  await userEvent.type(screen.getByLabelText(/contracts/i), "1.5");
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("option mode: unaffordable premium blocks the buy", async () => {
  setupOption(); // cash 1000; 2 contracts at ask = 1020
  await userEvent.clear(screen.getByLabelText(/contracts/i));
  await userEvent.type(screen.getByLabelText(/contracts/i), "2");
  expect(await screen.findByText(/insufficient cash/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("option mode: submits the raw OCC symbol", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 11, account_id: 1, symbol: OCC, side: "buy", order_type: "market",
    tif: "day", qty: "1", limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-17T15:00:00",
  });
  setupOption();
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(body.symbol).toBe(OCC);
  expect(body.qty).toBe("1");
});
