import { render, screen } from "@testing-library/react";
import { PositionsTable } from "@/components/PositionsTable";
import type { PositionValue } from "@/lib/types";

const stock: PositionValue = {
  symbol: "AAPL", qty: "10", avg_cost: "150", last_price: "160",
  market_value: "1600", unrealized_pnl: "100", realized_pnl: "0",
};
const crypto: PositionValue = {
  symbol: "BTC-USD", qty: "0.05", avg_cost: "60000", last_price: "65000",
  market_value: "3250", unrealized_pnl: "250", realized_pnl: "0",
};

it("groups positions into Stocks and Crypto sections", () => {
  render(<PositionsTable positions={[stock, crypto]} />);
  expect(screen.getByText("Stocks")).toBeInTheDocument();
  expect(screen.getByText("Crypto")).toBeInTheDocument();
  expect(screen.getByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText("BTC-USD")).toBeInTheDocument();
});

it("omits an empty group's header", () => {
  render(<PositionsTable positions={[stock]} />);
  expect(screen.getByText("Stocks")).toBeInTheDocument();
  expect(screen.queryByText("Crypto")).not.toBeInTheDocument();
});

it("shows the empty-state message when there are no positions at all", () => {
  render(<PositionsTable positions={[]} />);
  expect(screen.getByText(/no open positions/i)).toBeInTheDocument();
});
