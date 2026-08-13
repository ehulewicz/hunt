import { afterEach, expect, test, vi } from "vitest";

import { getListings } from "@/lib/jobs-api";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

test("listing requests send one category and every selected normalized location", async () => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  const fetchSpy = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ items: [], total: 0, limit: 200, offset: 0, location_options: [] }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchSpy);

  await getListings("approved", {
    category: "quant",
    locations: ["Remote", "New York City, NY"],
  });

  expect(fetchSpy).toHaveBeenCalledWith(
    "https://api.test/v1/jobs/listings?limit=200&category=quant&locations=Remote&locations=New+York+City%2C+NY",
    { headers: { Authorization: "Bearer approved" } },
  );
});
