import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { accountInitials, AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/hunt" }));

const account = {
  user_id: "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
  email: "eahtree@gmail.com",
  display_name: "Eah Tree",
  avatar_url: null,
};

test("account initials use the first and final names", () => {
  expect(accountInitials("Eah Middle Tree")).toBe("ET");
});

test("the shell links only Hunt, with Integrations in the account menu", () => {
  render(<AppShell account={account} onSignOut={() => undefined}><h1>Hunt</h1></AppShell>);
  expect(screen.getByRole("link", { name: "Hunt" })).toHaveAttribute("href", "/hunt");
  expect(screen.queryByRole("link", { name: "Integrations" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Jobs" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Hunt" })).toBeInTheDocument();
});

test("account menu shows identity and no profile route", () => {
  const onSignOut = vi.fn();
  render(<AccountMenu account={account} onSignOut={onSignOut} />);
  fireEvent.click(screen.getByRole("button", { name: "Account: Eah Tree" }));
  expect(screen.getByText("eahtree@gmail.com")).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Integrations" })).toHaveAttribute("href", "/integrations");
  expect(screen.queryByRole("menuitem", { name: "Profile" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: "Log out" }));
  expect(onSignOut).toHaveBeenCalledOnce();
});
