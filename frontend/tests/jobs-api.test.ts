import { afterEach, expect, test, vi } from "vitest";

import { createAnswer, deleteApplication, getListings, queueApplications } from "@/lib/jobs-api";

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

test("application archival and queueing use the lifecycle endpoints", async () => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  const fetchSpy = vi.fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{ id: "application-1", status: "queued" }]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchSpy);

  await deleteApplication("approved", "application-1");
  const queued = await queueApplications("approved", ["posting-1"]);

  expect(fetchSpy).toHaveBeenNthCalledWith(1, "https://api.test/v1/jobs/applications/application-1", {
    method: "DELETE",
    headers: { Authorization: "Bearer approved" },
  });
  expect(fetchSpy).toHaveBeenNthCalledWith(2, "https://api.test/v1/jobs/applications/queue", {
    method: "POST",
    headers: { Authorization: "Bearer approved", "Content-Type": "application/json" },
    body: JSON.stringify({ posting_ids: ["posting-1"] }),
  });
  expect(queued.map((item) => item.status)).toEqual(["queued"]);
});

test("long-form answer creation sends no client key", async () => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), {
    status: 201,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchSpy);

  await createAnswer("approved", {
    question: "Why this team?",
    answer_text: "The work aligns with my experience.",
    approved: true,
  });

  const request = fetchSpy.mock.calls[0][1] as RequestInit;
  expect(fetchSpy.mock.calls[0][0]).toBe("https://api.test/v1/jobs/answers");
  expect(request.method).toBe("POST");
  expect(JSON.parse(String(request.body))).toEqual({
    question: "Why this team?",
    answer_text: "The work aligns with my experience.",
    approved: true,
  });
  expect(String(request.body)).not.toContain("key");
});
