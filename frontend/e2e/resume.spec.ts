import { expect, test } from "@playwright/test";

test("resume serves the stored PDF without a session", async ({ request, page }) => {
  const response = await request.get("/resume", { maxRedirects: 0 });
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("application/pdf");
  const dispositions = (await response.headersArray())
    .filter((header) => header.name.toLowerCase() === "content-disposition");
  expect(dispositions).toHaveLength(1);
  expect(dispositions[0].value).toBe('inline; filename="Alex_Example.pdf"');
  expect(response.headers()["cache-control"]).toBe("no-cache");
  const pdf = await response.body();
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");

  const gone = await request.get("/Alex_Example.pdf");
  expect(gone.status()).toBe(404);

  await page.goto("/hunt");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
});
