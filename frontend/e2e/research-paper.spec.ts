import { expect, test } from "@playwright/test";

test("research paper serves the published PDF without a session", async ({ request, page }) => {
  const response = await request.get("/research/tiny-object-tracking", { maxRedirects: 0 });
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("application/pdf");
  expect(response.headers()["content-disposition"]).toBe(
    'inline; filename="Tiny_Object_Tracking_Applied_to_Shrimp_Detection.pdf"',
  );
  const pdf = await response.body();
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");

  const download = await request.get("/Tiny_Object_Tracking.pdf");
  expect(download.status()).toBe(200);
  expect(await download.body()).toEqual(pdf);

  await page.goto("/hunt");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
});
