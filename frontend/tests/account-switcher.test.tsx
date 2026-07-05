import { screen, waitFor } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, accounts: vi.fn() } };
});

import { AccountProvider, useAccount } from "@/app/account-context";
import { AccountSwitcher } from "@/components/AccountSwitcher";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
const live = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0", last_synced_at: null, sync_detail: null,
};

function ShowAccount() {
  const { accountId } = useAccount();
  return <p data-testid="selected">{accountId ?? "none"}</p>;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it("lists only paper accounts in the switcher", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, live]);
  renderWithClient(
    <AccountProvider>
      <AccountSwitcher />
    </AccountProvider>,
  );
  expect(await screen.findByRole("option", { name: "manual" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "live" })).not.toBeInTheDocument();
});

it("never defaults the paper section to the live account", async () => {
  localStorage.setItem("pt-account", "9"); // stale selection of the live account
  vi.mocked(api.accounts).mockResolvedValue([manual, live]);
  renderWithClient(
    <AccountProvider>
      <ShowAccount />
    </AccountProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("selected")).toHaveTextContent("1"));
});
