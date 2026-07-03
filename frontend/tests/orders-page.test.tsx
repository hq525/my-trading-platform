import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { ...actual.api, accounts: vi.fn(), orders: vi.fn(), cancelOrder: vi.fn() },
  };
});

import { AccountProvider } from "@/app/account-context";
import OrdersPage from "@/app/orders/page";
import { api, ApiError } from "@/lib/api";
import type { Order } from "@/lib/types";

beforeEach(() => {
  vi.clearAllMocks();
});

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };
const pendingOrder: Order = {
  id: 3, account_id: 1, symbol: "SPY", side: "buy", order_type: "limit", tif: "gtc",
  qty: 10, limit_price: "95", status: "pending", reject_reason: null,
  placed_at: "2026-07-02T15:00:00",
};

function setup(orders: Order[]) {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.orders).mockResolvedValue(orders);
  return renderWithClient(
    <AccountProvider>
      <OrdersPage />
    </AccountProvider>,
  );
}

it("lists orders and requests the selected status filter", async () => {
  setup([pendingOrder]);
  expect(await screen.findByText("SPY")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^filled$/i }));
  await waitFor(() => expect(api.orders).toHaveBeenLastCalledWith(1, "filled"));
});

it("cancels a pending order", async () => {
  vi.mocked(api.cancelOrder).mockResolvedValue({ ...pendingOrder, status: "cancelled" });
  setup([pendingOrder]);
  await screen.findByText("SPY");
  await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledWith(3));
});

it("surfaces cancel failures", async () => {
  vi.mocked(api.cancelOrder).mockRejectedValue(
    new ApiError(409, "cannot cancel order in status filled"),
  );
  setup([pendingOrder]);
  await screen.findByText("SPY");
  await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText(/cannot cancel order/i)).toBeInTheDocument();
});
