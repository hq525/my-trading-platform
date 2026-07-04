import { render } from "@testing-library/react";
import { vi } from "vitest";

const setData = vi.fn();
const chart = {
  addSeries: vi.fn(() => ({ setData })),
  priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
  timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  applyOptions: vi.fn(),
  remove: vi.fn(),
};
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => chart),
  CandlestickSeries: "CandlestickSeries",
  HistogramSeries: "HistogramSeries",
  AreaSeries: "AreaSeries",
}));

import { createChart } from "lightweight-charts";
import { CandleChart } from "@/components/CandleChart";
import { EquityCurve } from "@/components/EquityCurve";

beforeEach(() => vi.clearAllMocks());

const bar = {
  timestamp: "2026-06-30T04:00:00",
  open: "100", high: "101.5", low: "99", close: "100.25", volume: 12345,
};

it("maps bars to numeric candle and volume points keyed by date", () => {
  render(<CandleChart bars={[bar]} />);
  expect(createChart).toHaveBeenCalledOnce();
  expect(setData).toHaveBeenCalledTimes(2); // candles + volume
  expect(setData.mock.calls[0][0]).toEqual([
    { time: "2026-06-30", open: 100, high: 101.5, low: 99, close: 100.25 },
  ]);
  expect(setData.mock.calls[1][0]).toEqual([{ time: "2026-06-30", value: 12345 }]);
});

it("maps snapshots to an equity area series", () => {
  render(
    <EquityCurve snapshots={[{ date: "2026-07-02", equity: "100100.5", cash: "99000" }]} />,
  );
  expect(setData).toHaveBeenCalledWith([{ time: "2026-07-02", value: 100100.5 }]);
});

it("removes the chart on unmount", () => {
  const { unmount } = render(<CandleChart bars={[bar]} />);
  unmount();
  expect(chart.remove).toHaveBeenCalled();
});
