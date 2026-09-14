import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ListingsWorkspace } from "@/components/jobs/listings-workspace";
import { getListings, queueApplications, type JobApplication } from "@/lib/jobs-api";

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    queueApplications: vi.fn(),
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

test("listings start with one unlabeled control bar and no results header", async () => {
  render(<ListingsWorkspace />);

  await waitFor(() => expect(screen.getByText("No internship or co-op listings match.")));
  const prepare = screen.getByRole("button", { name: "Queue selected (0)" });
  const toolbar = prepare.closest(".jobs-toolbar");

  expect(toolbar).not.toBeNull();
  expect(toolbar?.parentElement?.firstElementChild).toBe(toolbar);
  expect(screen.getByRole("radiogroup", { name: "Category" }).closest(".jobs-toolbar")).toBe(toolbar);
  expect(screen.getByText("Locations").closest(".jobs-toolbar")).toBe(toolbar);
  expect(screen.queryByText("Category")).not.toBeInTheDocument();
  expect(screen.queryByText("0 matches")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Internships & co-ops" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Job listings" })).toHaveClass("sr-only");
  expect(screen.queryByText(/SpeedyApply's SWE and AI\/ML college lists/)).not.toBeInTheDocument();
});

test("listing filters stay fixed while queueing is in flight", async () => {
  let finishPreparation!: (value: JobApplication[]) => void;
  const pendingPreparation = new Promise<JobApplication[]>((resolve) => {
    finishPreparation = resolve;
  });
  let finishReload!: (value: Awaited<ReturnType<typeof getListings>>) => void;
  const pendingReload = new Promise<Awaited<ReturnType<typeof getListings>>>(
    (resolve) => { finishReload = resolve; },
  );
  vi.mocked(getListings).mockResolvedValueOnce({
    items: [{
      id: "listing-2",
      company_name: "Example Co",
      title: "Software Engineering Intern",
      location: "Remote",
      salary_text: null,
      application_url: "https://example.com/jobs/2",
      discovery_url: null,
      role_families: ["software_engineering"],
      career_stage: "internship",
      feed_region: "usa",
      source_posted_at: "2026-08-13",
      source_age_days: 0,
      source_category: "other",
      normalized_locations: ["Remote"],
      status: "saved",
      application_id: null,
    }],
    total: 1,
    limit: 200,
    offset: 0,
    location_options: ["Remote"],
  }).mockReturnValueOnce(pendingReload);
  vi.mocked(queueApplications).mockReturnValueOnce(pendingPreparation);
  render(<ListingsWorkspace />);

  fireEvent.click(await screen.findByRole("checkbox", { name: /Select Software Engineering/ }));
  fireEvent.click(screen.getByRole("button", { name: "Queue selected (1)" }));

  await waitFor(() => expect(screen.getByRole("radio", { name: "All" })).toBeDisabled());
  expect(screen.getByRole("checkbox", { name: "Remote" })).toBeDisabled();

  const listingCallCount = vi.mocked(getListings).mock.calls.length;
  finishPreparation([]);
  await waitFor(() => expect(vi.mocked(getListings).mock.calls).toHaveLength(listingCallCount + 1));
  expect(screen.getByRole("radio", { name: "All" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Remote" })).toBeDisabled();

  finishReload({ items: [], total: 0, limit: 200, offset: 0, location_options: ["Remote"] });
  await waitFor(() => expect(screen.getByRole("button", { name: "Queue selected (0)" })).toBeDisabled());
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
