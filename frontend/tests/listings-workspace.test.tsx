import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ListingsWorkspace } from "@/components/jobs/listings-workspace";
import { getListings } from "@/lib/jobs-api";

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    getListings: vi.fn().mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
      location_options: ["New York City, NY", "Remote"],
    }),
  };
});

test("listings expose only source category and multi-location filters", async () => {
  render(<ListingsWorkspace />);

  await waitFor(() => expect(screen.getByText("No internship or co-op listings match.")));
  expect(screen.getByRole("radio", { name: "All" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "FAANG+" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Quant" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "New York City, NY" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Remote" })).toBeInTheDocument();
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  expect(screen.queryByText("Career stage")).not.toBeInTheDocument();
});

test("listing title is the company link and company plus normalized location stay visible", async () => {
  vi.mocked(getListings).mockResolvedValueOnce({
    items: [{
      id: "listing-1",
      company_name: "Example Co",
      title: "Business Operations and AI Intern",
      location: "Dallas, TX",
      salary_text: "$40/hr",
      application_url: "https://example.com/jobs/1",
      discovery_url: "https://github.com/speedyapply/example",
      role_families: ["other"],
      career_stage: "internship",
      feed_region: "usa",
      source_posted_at: "2026-08-13",
      source_age_days: 0,
      source_category: "other",
      normalized_locations: ["Dallas, TX"],
      status: "saved",
      application_id: null,
    }],
    total: 1,
    limit: 200,
    offset: 0,
    location_options: ["Dallas, TX"],
  });

  render(<ListingsWorkspace />);

  const title = await screen.findByRole("link", { name: "Business Operations and AI Intern" });
  expect(title).toHaveAttribute("href", "https://example.com/jobs/1");
  expect(title).toHaveAttribute("target", "_blank");
  expect(screen.getByText("Example Co · Dallas, TX")).toBeInTheDocument();
  expect(screen.queryByText("Company listing")).not.toBeInTheDocument();
});
