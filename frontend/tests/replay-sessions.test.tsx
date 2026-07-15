import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      replaySessions: vi.fn(), createReplaySession: vi.fn(),
      deleteReplaySession: vi.fn(), strategies: vi.fn(),
    },
  };
});

import ReplaySessionsPage from "@/app/replay/page";
import { api, ApiError } from "@/lib/api";
import type { ReplaySession } from "@/lib/types";

const session: ReplaySession = {
  id: 3, name: "SPY from 2024-06-03", symbols: ["SPY"],
  start_date: "2024-06-03", cursor_date: "2024-06-10", end_date: "2024-06-28",
  exhausted: false, created_at: "2026-07-16T10:00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.replaySessions).mockResolvedValue([session]);
  vi.mocked(api.strategies).mockResolvedValue([
    { name: "SmaCross", schedule: "daily_after_close", enabled: true, account_id: 2 },
  ]);
});

it("lists sessions with a link to the workbench", async () => {
  renderWithClient(<ReplaySessionsPage />);
  expect(await screen.findByText("SPY from 2024-06-03")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /open/i })).toHaveAttribute("href", "/replay/3");
});

it("creates a session from the form with unchecked strategies by default", async () => {
  vi.mocked(api.createReplaySession).mockResolvedValue({
    ...session, id: 4, accounts: [], coverage: [],
  });
  renderWithClient(<ReplaySessionsPage />);
  await screen.findByText("SPY from 2024-06-03");
  const checkbox = await screen.findByRole("checkbox", { name: "SmaCross" });
  expect(checkbox).not.toBeChecked(); // global enabled flag ignored
  await userEvent.click(checkbox);
  await userEvent.type(screen.getByLabelText(/symbols/i), "SPY, BTC-USD");
  // userEvent.type is unreliable for type="date" inputs in jsdom
  fireEvent.change(screen.getByLabelText(/start date/i),
    { target: { value: "2024-06-03" } });
  await userEvent.click(screen.getByRole("button", { name: /create session/i }));
  await waitFor(() => expect(api.createReplaySession).toHaveBeenCalled());
  const [body] = vi.mocked(api.createReplaySession).mock.calls[0];
  expect(body).toMatchObject({
    symbols: ["SPY", "BTC-USD"], start_date: "2024-06-03",
    strategies: ["SmaCross"], starting_cash: "100000",
  });
});

it("shows the backend's coverage error on creation failure", async () => {
  vi.mocked(api.createReplaySession).mockRejectedValue(
    new ApiError(400, "insufficient coverage at start date: SPY history starts 2024-08-01 (through 2026-07-15)"),
  );
  renderWithClient(<ReplaySessionsPage />);
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.type(screen.getByLabelText(/symbols/i), "SPY");
  fireEvent.change(screen.getByLabelText(/start date/i),
    { target: { value: "2024-06-03" } });
  await userEvent.click(screen.getByRole("button", { name: /create session/i }));
  expect(await screen.findByText(/SPY history starts 2024-08-01/)).toBeInTheDocument();
});

it("deletes a session after inline confirmation", async () => {
  vi.mocked(api.deleteReplaySession).mockResolvedValue({ ok: true });
  renderWithClient(<ReplaySessionsPage />);
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  expect(api.deleteReplaySession).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /confirm delete/i }));
  await waitFor(() => expect(api.deleteReplaySession).toHaveBeenCalledWith(3));
});
