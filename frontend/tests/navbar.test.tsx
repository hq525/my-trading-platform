import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { NavBar } from "@/components/NavBar";

it("renders all five nav links", () => {
  render(<NavBar />);
  for (const label of ["Dashboard", "Trade", "Orders", "Journal", "Strategies"]) {
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  }
});
