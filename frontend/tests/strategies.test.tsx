import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  })),
  CandlestickSeries: "C", HistogramSeries: "H", AreaSeries: "A",
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      strategies: vi.fn(), toggleStrategy: vi.fn(), runs: vi.fn(), snapshots: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import StrategiesPage from "@/app/strategies/page";

const sma = { name: "SmaCross", schedule: "daily_after_close", enabled: false, account_id: 2 };

beforeEach(() => {
  vi.mocked(api.strategies).mockResolvedValue([sma]);
  vi.mocked(api.snapshots).mockResolvedValue([]);
  vi.mocked(api.runs).mockResolvedValue([
    {
      id: 1, strategy_name: "SmaCross", started_at: "2026-07-02T20:05:00",
      finished_at: "2026-07-02T20:05:01", status: "error", detail: "RuntimeError: boom",
    },
  ]);
});

it("lists strategies with schedule and run errors", async () => {
  renderWithClient(<StrategiesPage />);
  expect(await screen.findByText("SmaCross")).toBeInTheDocument();
  expect(screen.getByText("daily_after_close")).toBeInTheDocument();
  expect(await screen.findByText(/RuntimeError: boom/)).toBeInTheDocument();
});

it("toggles a strategy", async () => {
  vi.mocked(api.toggleStrategy).mockResolvedValue({ ...sma, enabled: true });
  renderWithClient(<StrategiesPage />);
  await userEvent.click(await screen.findByRole("switch"));
  await waitFor(() => expect(api.toggleStrategy).toHaveBeenCalledWith("SmaCross"));
});
