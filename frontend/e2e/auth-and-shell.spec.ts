import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("login and denied-account states are explicit and accessible", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await page.goto("/login?reason=unauthorized");
  await expect(page.locator(".auth-notice")).toHaveText(
    "This Google account is not authorized for Personal OS.",
  );
});

test("callback failure returns to a fixed sign-in error", async ({ page }) => {
  await page.goto("/auth/callback");
  await expect(page).toHaveURL(/\/login\?reason=callback$/);
  await expect(page.locator(".auth-notice")).toHaveText(
    "Google sign-in could not be completed. Please try again.",
  );
});

test("authenticated shell persists across Jobs navigation and logs out", async ({ page }) => {
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  await page.addInitScript(({ session }) => {
    localStorage.setItem("sb-test-auth-token", JSON.stringify(session));
  }, {
    session: {
      access_token: "approved",
      refresh_token: "refresh",
      expires_in: 3600,
      expires_at: expiresAt,
      token_type: "bearer",
      user: {
        id: "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
        aud: "authenticated",
        role: "authenticated",
        email: "eahtree@gmail.com",
        app_metadata: { provider: "google" },
        user_metadata: { full_name: "Eah Tree" },
        identities: [],
        created_at: new Date().toISOString(),
      },
    },
  });
  const account = {
    user_id: "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
    email: "eahtree@gmail.com",
    display_name: "Eah Tree",
    avatar_url: null,
  };
  await page.route("https://api.test/v1/**", (route) => route.fulfill({ json: account }));
  await page.route("https://test.supabase.co/auth/v1/logout**", (route) => route.fulfill({ status: 204 }));

  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await page.getByRole("link", { name: "Jobs" }).click();
  await page.setViewportSize({ width: 800, height: 900 });
  await expect(page.getByRole("heading", { name: "Job listings" })).toBeAttached();
  await expect(page.getByRole("region", { name: "Listing filters" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare selected (0)" })).toBeVisible();
  expect(await page.getByRole("region", { name: "Listing filters" }).evaluate(
    (toolbar) => toolbar.scrollWidth <= toolbar.clientWidth,
  )).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("link", { name: "Cover Letters" }).click();
  await expect(page.getByRole("heading", { name: "Cover Letters" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Dashboard", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Account: Eah Tree" }).click();
  await page.getByRole("menuitem", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
});
