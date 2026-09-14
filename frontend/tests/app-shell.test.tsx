import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { accountInitials, AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

const account = {
  user_id: "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
  email: "eahtree@gmail.com",
  display_name: "Eah Tree",
  avatar_url: null,
};

test("account initials use the first and final names", () => {
  expect(accountInitials("Eah Middle Tree")).toBe("ET");
});

test("the shell has no Jobs navigation", () => {
  render(<AppShell account={account} onSignOut={() => undefined}><h1>Dashboard</h1></AppShell>);
  expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
  expect(screen.queryByRole("link", { name: "Jobs" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
});

test("account menu shows identity and no profile route", () => {
  const onSignOut = vi.fn();
  render(<AccountMenu account={account} onSignOut={onSignOut} />);
  fireEvent.click(screen.getByRole("button", { name: "Account: Eah Tree" }));
  expect(screen.getByText("eahtree@gmail.com")).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Profile" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: "Log out" }));
  expect(onSignOut).toHaveBeenCalledOnce();
});
