import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ApplicationsWorkspace } from "@/components/jobs/applications-workspace";
import { deleteApplication, getApplications } from "@/lib/jobs-api";

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    deleteApplication: vi.fn().mockResolvedValue(undefined),
    getApplications: vi.fn().mockResolvedValue([{
      id: "application-1",
      posting_id: "posting-1",
      company_name: "Example Co",
      title: "Software Engineer Intern",
      location: "Atlanta, GA",
      application_url: "https://example.com/jobs/1",
      status: "ready",
      applied_at: null,
      next_action: "Review and submit manually",
      next_action_at: null,
      notes: null,
      created_at: "2026-08-13T00:00:00Z",
      updated_at: "2026-08-13T00:00:00Z",
      preparation_status: "completed",
      preparation_error: null,
      autofill_status: "failed",
      autofill_error: "Required fields still need answers",
      unanswered_required_questions: ["Why this team?"],
    }]),
    queueAutofill: vi.fn().mockResolvedValue({ task_id: "task-1", status: "queued" }),
  };
});

test("applications link the title, show task state, and confirm recoverable deletion", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ApplicationsWorkspace />);

  expect(await screen.findByRole("link", { name: "Software Engineer Intern" })).toHaveAttribute(
    "href",
    "https://example.com/jobs/1",
  );
  expect(screen.getByText("Autofill: failed")).toBeInTheDocument();
  expect(screen.getByText(/Needs answers: Why this team/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));

  await waitFor(() => expect(deleteApplication).toHaveBeenCalledWith("approved", "application-1"));
  expect(getApplications).toHaveBeenCalled();
});
