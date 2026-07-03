import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, login: vi.fn() } };
});

import { api, ApiError } from "@/lib/api";
import LoginPage from "@/app/login/page";

it("logs in and navigates to the dashboard", async () => {
  vi.mocked(api.login).mockResolvedValue({ ok: true });
  renderWithClient(<LoginPage />);
  await userEvent.type(screen.getByLabelText(/password/i), "pw");
  await userEvent.click(screen.getByRole("button", { name: /log in/i }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  expect(api.login).toHaveBeenCalledWith("pw");
});

it("shows the error on a wrong password", async () => {
  vi.mocked(api.login).mockRejectedValue(new ApiError(401, "wrong password"));
  renderWithClient(<LoginPage />);
  await userEvent.type(screen.getByLabelText(/password/i), "nope");
  await userEvent.click(screen.getByRole("button", { name: /log in/i }));
  expect(await screen.findByText("wrong password")).toBeInTheDocument();
});
