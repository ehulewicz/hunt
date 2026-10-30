import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { HuntInbox } from "@/components/hunt-inbox";
import type { Inbox } from "@/lib/api";

vi.mock("@/lib/device", () => ({ freshAccessToken: vi.fn(async () => "token") }));

const GAPS: Inbox["gaps"] = [
  {
    id: "g1", question_id: "q1", question: "Why this company?",
    application_id: "a1", company_name: "Acme", title: "Backend Intern",
    family: "why_company", coverage: "missing", missing_detail: "No Acme notes yet",
    created_at: "2026-10-30", resolved_at: null,
  },
  {
    id: "g2", question_id: "q2", question: "Proudest work?",
    application_id: null, company_name: null, title: null,
    family: "proudest_work", coverage: "partial", missing_detail: "",
    created_at: "2026-10-30", resolved_at: null,
  },
];

const INBOX: Inbox = {
  questions: [], candidates: [], checks: [], unsolved: [],
  facts: [],
  prose: [{ id: "pr1", question_label: "Why Acme?", company_name: "Acme", text: "Draft.", approved: false, origin: "model", provenance: null, submitted: false }],
  gaps: GAPS,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ id: "g1", resolved_at: "2026-10-30T12:00:00Z" })));
});

test("open gaps list under Writing and a resolve posts to the gap", async () => {
  const onRefresh = vi.fn();
  render(<HuntInbox facts={[]} inbox={INBOX} onRefresh={onRefresh} />);
  fireEvent.click(screen.getByRole("button", { name: "Writing 3" }));
  expect(screen.getByText("Why this company?")).toBeInTheDocument();
  expect(screen.getByText("Acme · Backend Intern")).toBeInTheDocument();
  expect(screen.getByText("Not covered · why company")).toBeInTheDocument();
  expect(screen.getByText("No Acme notes yet")).toBeInTheDocument();
  expect(screen.getByText("Partly covered · proudest work")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Write in Library" })[0]).toHaveAttribute("href", "/library");
  fireEvent.click(screen.getAllByRole("button", { name: "Resolve" })[0]);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    "https://api.test/v1/jobs/prose/gaps/g1/resolve",
    expect.objectContaining({ method: "POST" }),
  ));
  await waitFor(() => expect(onRefresh).toHaveBeenCalled());
});

test("library spans render plain with the entry code as their title", () => {
  const inbox: Inbox = {
    ...INBOX,
    gaps: [],
    prose: [{
      id: "pr1", question_label: "Why?", company_name: "Acme", approved: false,
      origin: "model", submitted: false, text: "mine model library",
      provenance: [
        { start: 0, end: 4, source: "previous" },
        { start: 5, end: 10, source: "model" },
        { start: 11, end: 18, source: "library", entry: "2.2.11" },
      ],
    }],
  };
  render(<HuntInbox facts={[]} inbox={inbox} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Writing 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Why?" }));
  expect(screen.getByText("model", { selector: "mark" })).toHaveClass("prose-model");
  expect(screen.getByText("mine", { selector: "mark" })).toHaveClass("prose-previous");
  const span = screen.getByTitle("Library 2.2.11");
  expect(span.tagName).toBe("SPAN");
  expect(span).not.toHaveClass("prose-library");
  expect(screen.getByText(/Your text and library writing are plain/)).toBeInTheDocument();
});
