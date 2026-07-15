import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, accounts: vi.fn(async () => []) } };
});

import { AccountProvider } from "@/app/account-context";
import { NavBar } from "@/components/NavBar";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accounts).mockResolvedValue([]);
});

function renderNav() {
  return renderWithClient(
    <AccountProvider>
      <NavBar />
    </AccountProvider>,
  );
}

it("renders the paper links and no LIVE badge on paper pages", () => {
  pathname = "/";
  renderNav();
  for (const label of ["Dashboard", "Trade", "Orders", "Journal", "Strategies"]) {
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  }
  expect(screen.getByRole("link", { name: "Paper" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "Live" })).toHaveAttribute("href", "/live");
  expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
});

it("shows the LIVE badge and live links in the live section", () => {
  pathname = "/live/trade";
  renderNav();
  expect(screen.getByText("LIVE")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/live");
  expect(screen.getByRole("link", { name: "Trade" })).toHaveAttribute("href", "/live/trade");
  expect(screen.getByRole("link", { name: "Orders" })).toHaveAttribute("href", "/live/orders");
  expect(screen.queryByRole("link", { name: "Journal" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Strategies" })).not.toBeInTheDocument();
});

it("hides the account switcher in the live section only", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  pathname = "/";
  const paper = renderNav();
  expect(await screen.findByRole("combobox")).toBeInTheDocument();
  paper.unmount();

  pathname = "/live";
  renderNav();
  expect(screen.getByText("LIVE")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});
