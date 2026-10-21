import { expect, test } from "@playwright/test";

test("resume serves the published PDF without a session", async ({ request, page }) => {
  const response = await request.get("/resume", { maxRedirects: 0 });
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("application/pdf");
  expect(response.headers()["content-disposition"]).toBe('inline; filename="Alex_Example.pdf"');
  const pdf = await response.body();
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");

  const download = await request.get("/Alex_Example.pdf");
  expect(download.status()).toBe(200);
  expect(await download.body()).toEqual(pdf);

  await page.goto("/hunt");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
});
