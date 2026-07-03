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

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };

function setup(quotePrice?: string) {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol="SPY" quotePrice={quotePrice} />
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
    tif: "day", qty: 5, limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [accountId, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(accountId).toBe(1);
  expect(body).toMatchObject({ symbol: "SPY", side: "buy", order_type: "market", qty: 5, tif: "day" });
  expect(typeof body.idempotency_key).toBe("string");
  expect(body.idempotency_key!.length).toBeGreaterThan(10);
  expect(await screen.findByText(/filled/i)).toBeInTheDocument();
});

it("shows rejection reasons from the backend", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 8, account_id: 1, symbol: "SPY", side: "buy", order_type: "limit",
    tif: "day", qty: 5, limit_price: "90", status: "rejected",
    reject_reason: "market data unavailable", placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.click(screen.getByRole("radio", { name: /limit/i }));
  await userEvent.type(screen.getByLabelText(/limit price/i), "90");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(await screen.findByText(/market data unavailable/i)).toBeInTheDocument();
});
