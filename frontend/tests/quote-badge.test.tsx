import { render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { QuoteBadge } from "@/components/QuoteBadge";

const now = new Date("2026-07-02T15:02:00Z");

it("shows price and age for a fresh quote", () => {
  render(
    <QuoteBadge
      quote={{ symbol: "SPY", price: "512.34", as_of: "2026-07-02T15:01:30", bid: null, ask: null }}
      now={now}
    />,
  );
  expect(screen.getByText("$512.34")).toBeInTheDocument();
  expect(screen.getByText("30s ago")).toBeInTheDocument();
});

it("marks a stale quote", () => {
  render(
    <QuoteBadge
      quote={{ symbol: "SPY", price: "512.34", as_of: "2026-07-02T14:55:00", bid: null, ask: null }}
      now={now}
    />,
  );
  expect(screen.getByText(/stale/i)).toBeInTheDocument();
});

it("reports unknown symbols and outages distinctly", () => {
  const { rerender } = render(<QuoteBadge error={new ApiError(404, "unknown symbol: XXXX")} />);
  expect(screen.getByText(/unknown symbol/i)).toBeInTheDocument();
  rerender(<QuoteBadge error={new ApiError(503, "market data unavailable")} />);
  expect(screen.getByText(/market data unavailable/i)).toBeInTheDocument();
});
