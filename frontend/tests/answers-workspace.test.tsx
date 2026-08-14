import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { AnswersWorkspace } from "@/components/jobs/answers-workspace";
import { createAnswer } from "@/lib/jobs-api";

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    createAnswer: vi.fn().mockResolvedValue({}),
    getAnswers: vi.fn().mockResolvedValue([]),
    updateAnswer: vi.fn().mockResolvedValue({}),
  };
});

test("answers use question and long-form response fields without exposing internal keys", async () => {
  render(<AnswersWorkspace />);

  expect(await screen.findByText("No long-form answers yet.")).toBeInTheDocument();
  expect(screen.queryByText("Stable key")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Question")).toBeRequired();
  expect(screen.getByLabelText("Answer")).toBeRequired();

  fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Why this team?" } });
  fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "The mission fits my work." } });
  fireEvent.click(screen.getByLabelText("Approve for autofill"));
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));

  await waitFor(() => expect(createAnswer).toHaveBeenCalledWith("approved", {
    question: "Why this team?",
    answer_text: "The mission fits my work.",
    approved: true,
  }));
});
