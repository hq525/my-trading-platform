import { render, screen } from "@testing-library/react";
import { OrdersTable } from "@/components/OrdersTable";
import type { Order } from "@/lib/types";

const occOrder: Order = {
  id: 31, account_id: 1, symbol: "SPY260821C00625000", side: "buy",
  order_type: "market", tif: "day", qty: "2", limit_price: null,
  status: "filled", reject_reason: null, placed_at: "2026-07-17T15:00:00",
};
const stockOrder: Order = {
  id: 32, account_id: 1, symbol: "AAPL", side: "buy", order_type: "market",
  tif: "day", qty: "5", limit_price: null, status: "filled",
  reject_reason: null, placed_at: "2026-07-17T15:00:00",
};

it("badges option orders and links them to the chain page", () => {
  render(<OrdersTable orders={[occOrder, stockOrder]} />);
  expect(screen.getByText("Option")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /SPY 08\/21\/26 \$625 C/ });
  expect(link).toHaveAttribute("href", "/options?symbol=SPY");
  expect(screen.getByText("Stock")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "AAPL" })).toHaveAttribute(
    "href", "/trade?symbol=AAPL");
});
