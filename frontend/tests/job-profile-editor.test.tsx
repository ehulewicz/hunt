import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { JobProfileEditor } from "@/components/jobs/job-profile-editor";

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    getJobsProfile: vi.fn().mockResolvedValue({
      target_roles: [],
      target_locations: [],
      target_career_stages: ["internship"],
      eeo_race_ethnicities: [],
      completed_at: null,
      updated_at: "2026-08-13T00:00:00Z",
    }),
    getWorkerCredentials: vi.fn().mockResolvedValue([]),
    updateJobsProfile: vi.fn(),
  };
});

test("profile groups deterministic application facts outside the answer library", async () => {
  render(<JobProfileEditor />);

  expect(await screen.findByRole("heading", { name: "Application facts" })).toBeInTheDocument();
  expect(screen.getByLabelText("Work authorization")).toBeInTheDocument();
  expect(screen.getByLabelText("Sponsorship required")).toBeInTheDocument();
  expect(screen.getByLabelText("Professional experience (years)")).toBeInTheDocument();
  expect(screen.getByLabelText("Willing to relocate")).toBeInTheDocument();
  expect(screen.getByLabelText("Willing to travel")).toBeInTheDocument();
  expect(screen.getByLabelText("Earliest start note")).toBeInTheDocument();
});
