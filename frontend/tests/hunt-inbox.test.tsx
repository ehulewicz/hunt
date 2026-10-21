import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { HuntInbox } from "@/components/hunt-inbox";
import type { Fact, Inbox } from "@/lib/api";

vi.mock("@/lib/device", () => ({ freshAccessToken: vi.fn(async () => "token") }));

const FACTS: Fact[] = [
  { key: "contact.phone_extension", label: "Phone extension", value: "", value_type: "text", verified_at: "2026-10-20", updated_at: "2026-10-20" },
  { key: "education.certifications", label: "Certifications", value: [], value_type: "list", verified_at: null, updated_at: "2026-10-20" },
  { key: "contact.email", label: "Email", value: "me@example.com", value_type: "text", verified_at: "2026-10-20", updated_at: "2026-10-20" },
];
const INBOX: Inbox = {
  questions: [{ question: { id: "q1", sample_label: "Your extension?" }, answers: [] }],
  prose: [{ id: "pr1", question_label: "Why Acme?", company_name: "Acme", text: "I build useful tools.", approved: false }],
  candidates: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.endsWith("/questions")) return Response.json([{ id: "q2", sample_label: "Your email?", disposition: "pattern", form: "short", pattern_id: "pat1" }]);
    if (input.endsWith("/facts")) return Response.json(FACTS);
    if (input.endsWith("/prose")) return Response.json([{ ...INBOX.prose[0], id: "pr2", question_label: "Career goals", approved: true }]);
    if (input.includes("/patterns?")) return Response.json([{ id: "pat1", question_id: "q2", question_label: "Your email?", expression: null, fact_key: "contact.email", formatter: null, status: "retired" }]);
    return Response.json({});
  }));
});

test("Inbox separates questions from facts and keeps verified none facts visible", async () => {
  const onRefresh = vi.fn();
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={onRefresh} />);
  expect(screen.getByRole("button", { name: "Questions 1" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Cause tags are not available yet.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 2" }));
  expect(screen.queryByRole("button", { name: "Review Email" })).not.toBeInTheDocument();
  expect(screen.getAllByText(/None/)).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Review Phone extension" }));
  expect(screen.getByLabelText("Value")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Save verified fact" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/facts/contact.phone_extension", expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: "", verified: true }) })));
  await waitFor(() => expect(onRefresh).toHaveBeenCalled());
});

test("Library reads saved records beyond the Inbox and leaves pattern history empty", async () => {
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(await screen.findByRole("button", { name: "View Your email?" }));
  expect(screen.getByText("Linked to a pattern")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "All items" }));
  fireEvent.click(screen.getByRole("button", { name: /^Patterns/ }));
  fireEvent.click(screen.getByRole("button", { name: "View Your email?" }));
  expect(screen.getByText("Retired")).toBeInTheDocument();
  expect(screen.getByText("Pattern history is not available yet.")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("status=retired"), expect.anything());
  fireEvent.click(screen.getByRole("button", { name: "All items" }));
  fireEvent.click(screen.getByRole("button", { name: /^Writing/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review Career goals" }));
  expect(screen.getByRole("textbox", { name: "Answer" })).toHaveValue("I build useful tools.");
  expect(screen.getByText("Origin highlights are not available yet.")).toBeInTheDocument();
});

test("a failed Library load offers retry instead of empty records", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "Library unavailable" }, { status: 503 }));
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Library unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: "View Your email?" })).toBeInTheDocument();
});

test("writing can be saved as a draft without approving it", async () => {
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Writing 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Why Acme?" }));
  fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "My revised draft." } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/prose/pr1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ text: "My revised draft.", approved: false }) })));
});

test("invalid JSON stays in the fact editor and an empty list saves as a list", async () => {
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Certifications" }));
  fireEvent.change(screen.getByLabelText("Value"), { target: { value: "not a list" } });
  fireEvent.click(screen.getByRole("button", { name: "Save verified fact" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Value")).toHaveValue("not a list");
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Value"), { target: { value: "[]" } });
  fireEvent.click(screen.getByRole("button", { name: "Save verified fact" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/facts/education.certifications", expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: [], verified: true }) })));
});

test("a rejected writing save keeps edits available for retry", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "Could not save writing" }, { status: 503 }));
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Writing 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Why Acme?" }));
  fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "Keep this draft." } });
  fireEvent.click(screen.getByRole("button", { name: "Approve for reuse" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not save writing");
  expect(screen.getByLabelText("Answer")).toHaveValue("Keep this draft.");
  expect(screen.getByRole("button", { name: "Approve for reuse" })).toBeEnabled();
});
