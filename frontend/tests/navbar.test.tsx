import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, accounts: vi.fn(async () => []) } };
});

import { AccountProvider } from "@/app/account-context";
import { NavBar } from "@/components/NavBar";

it("renders all five nav links", () => {
  renderWithClient(
    <AccountProvider>
      <NavBar />
    </AccountProvider>,
  );
  for (const label of ["Dashboard", "Trade", "Orders", "Journal", "Strategies"]) {
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  }
});
