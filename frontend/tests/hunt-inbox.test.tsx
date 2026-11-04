import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { HuntInbox } from "@/components/hunt-inbox";
import type { ComputedFact, Fact, Inbox } from "@/lib/api";

vi.mock("@/lib/device", () => ({ freshAccessToken: vi.fn(async () => "token") }));

const FACTS: Fact[] = [
  { key: "contact.phone_extension", label: "Phone extension", value: "", value_type: "text", verified_at: "2026-10-20", updated_at: "2026-10-20" },
  { key: "education.certifications", label: "Certifications", value: [], value_type: "list", verified_at: null, updated_at: "2026-10-20" },
  { key: "contact.email", label: "Email", value: "me@example.com", value_type: "text", verified_at: "2026-10-20", updated_at: "2026-10-20" },
];
const COMPUTED: ComputedFact[] = [
  { key: "education.graduation_date", label: "Graduation date", source: "career", reads: ["career.education"] },
  { key: "education.graduation_term", label: "Graduation term", source: "derived", reads: ["education.graduation_date"] },
  { key: "education.gpa", label: "GPA", source: "career", reads: ["career.education"] },
];

const INBOX: Inbox = {
  questions: [{ question: { id: "q1", sample_label: "Your extension?" }, answers: [] }],
  candidates: [], checks: [], unsolved: [],
  facts: FACTS.slice(0, 2).map((fact) => ({ ...fact, reason: "unverified", question: null, missing_fact: null, issue_id: null, check: null })),
};

async function stubFetch(input: string) {
  if (input.endsWith("/prose")) return Response.json([{ id: "pr2", question_id: null, question_label: "Career goals", application_id: null, company_name: "Acme", text: "I build useful tools.", approved: true, origin: "manual", submitted: true, created_at: "2026-10-01", updated_at: "2026-10-01" }]);
  if (input.endsWith("/questions")) return Response.json([{ id: "q2", sample_label: "Your email?", disposition: "pattern", form: "short", pattern_id: "pat1" }]);
  if (input.endsWith("/facts")) return Response.json(FACTS);
  if (input.endsWith("/facts/computed")) return Response.json(COMPUTED);
  if (input.includes("/changes?")) return Response.json([]);
  if (input.includes("/patterns?")) return Response.json([{ id: "pat1", question_id: "q2", question_label: "Your email?", expression: null, fact_key: "contact.email", formatter: null, status: "retired" }]);
  return Response.json({});
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("fetch", vi.fn(stubFetch));
});

test("Inbox separates questions from server-selected facts and preserves None values", async () => {
  const onRefresh = vi.fn();
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={onRefresh} />);
  expect(screen.getByRole("button", { name: "Questions 1" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByText("Cause tags are not available yet.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 2" }));
  expect(screen.queryByRole("button", { name: "Review Email" })).not.toBeInTheDocument();
  expect(screen.getAllByText(/None/)).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Review Phone extension" }));
  expect(screen.getByLabelText("Value")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Save verified fact" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/facts/contact.phone_extension", expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: "", verified: true, origin: "inbox" }) })));
  await waitFor(() => expect(onRefresh).toHaveBeenCalled());
});

test("Library reads saved records beyond the Inbox and shows empty history explicitly", async () => {
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(await screen.findByRole("button", { name: "View Your email?" }));
  expect(screen.getByText("Linked to a pattern")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "All items" }));
  fireEvent.click(screen.getByRole("button", { name: /^Patterns/ }));
  fireEvent.click(screen.getByRole("button", { name: "View Your email?" }));
  expect(screen.getByText("Retired")).toBeInTheDocument();
  expect(await screen.findByText("No changes recorded.")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("status=retired"), expect.anything());
  fireEvent.click(screen.getByRole("button", { name: "All items" }));
  fireEvent.click(screen.getByRole("button", { name: /^Writing/ }));
  const row = screen.getByText("Career goals").closest(".review-row");
  expect(row).toHaveClass("review-row-static");
  expect(row).toHaveTextContent("Acme: Approved");
  expect(screen.queryByRole("button", { name: "Review Career goals" })).toBeNull();
});

test("a failed Library load offers retry instead of empty records", async () => {
  let failed = false;
  vi.mocked(fetch).mockImplementation(async (input) => !failed && String(input).endsWith("/questions")
    ? (failed = true, Response.json({ detail: "Library unavailable" }, { status: 503 }))
    : stubFetch(String(input)));
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Library unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: "View Your email?" })).toBeInTheDocument();
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
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/facts/education.certifications", expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: [], verified: true, origin: "inbox" }) })));
});

const CHANGE = {
  id: "change1", subject: "pattern" as const, subject_id: "pat1", action: "update" as const, before: { status: "active" }, after: { status: "candidate" },
  origin: "model" as const, run_id: "run1", question_key: null, failure_issue_id: "issue1", status: "failed" as const,
  sampled: 8, failed: 2, created_at: "2026-10-21T12:00:00Z", verified_at: "2026-10-21T12:01:00Z",
};

test("Inbox uses server fact reasons, failed checks first, and unsolved notes", () => {
  const pattern = { id: "pat1", question_id: null, question_label: "Email?", expression: null, fact_key: "contact.email", origin: "model", formatter: null, status: "candidate" };
  render(<HuntInbox facts={FACTS} inbox={{ ...INBOX,
    checks: [{ pattern, answers: [], check: CHANGE }], candidates: [{ pattern: { ...pattern, id: "pat2", question_label: "Other?" }, answers: [], check: null }],
    facts: [{ reason: "missing", key: null, label: null, value: null, value_type: null, question: "When can you start?", missing_fact: "start date", issue_id: "issue1", check: null },
      { ...FACTS[2], reason: "contradicted", question: null, missing_fact: null, issue_id: null, check: CHANGE }],
    unsolved: [{ issue_id: "issue2", category: "widget", question: "Portfolio", control_kind: "file", note: "Upload still unreadable", occurrence_count: 3, last_seen_at: "2026-10-21T12:00:00Z" }],
  }} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Pattern checks 2" }));
  expect(screen.getAllByRole("button", { name: /^Review/ })[0]).toHaveAccessibleName("Review Email?");
  expect(screen.getByText("replay changed 2 of 8 accepted answers")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 2" }));
  expect(screen.getByText(/When can you start/)).toBeInTheDocument();
  expect(screen.getByText("Needed: start date")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review Phone extension" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Unsolved 1" }));
  expect(screen.getByText("Upload still unreadable")).toBeInTheDocument();
});

test("Library renders item verification history and code fix details", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (...args) => {
    const url = String(args[0]);
    if (url.includes("/changes?subject=code")) return Response.json([{ ...CHANGE, subject: "code", subject_id: "abc1234", after: { commit: "abc1234", cause: "widget", question: "Portfolio", summary: "Read the upload", attempts: 2, files: ["upload.py"], probe: { ok: true }, pushed: true, diff: "+read_upload()" } }]);
    if (url.includes("/changes?")) return Response.json([CHANGE]);
    return original(...args);
  });
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(await screen.findByRole("button", { name: /^Patterns/ }));
  fireEvent.click(await screen.findByRole("button", { name: "View Your email?" }));
  expect(await screen.findByText("replay changed 2 of 8 accepted answers")).toBeInTheDocument();
  expect(screen.getByText(/model · update/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Code/ }));
  expect(await screen.findByText("Read the upload")).toBeInTheDocument();
  expect(screen.getByText("abc1234")).toBeInTheDocument();
  expect(screen.getByText("+read_upload()")).toBeInTheDocument();
});

test("a missing card for an existing typed fact preserves its stored metadata", async () => {
  const fact: Fact = { ...FACTS[0], key: "work_auth.sponsorship", label: "Needs sponsorship", value: false, value_type: "boolean" };
  render(<HuntInbox facts={[fact]} inbox={{ ...INBOX, facts: [{ reason: "missing", key: fact.key, label: null, value: null, value_type: null, question: "Need sponsorship?", missing_fact: null, issue_id: "issue1", check: null }] }} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 1" }));
  fireEvent.click(screen.getByRole("button", { name: /^Review/ }));
  expect(screen.getByLabelText("Value")).toHaveValue("false");
  expect(screen.queryByLabelText("Type")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save verified fact" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/facts/work_auth.sponsorship", expect.objectContaining({ body: JSON.stringify({ value: false, verified: true, origin: "inbox", failure: { issue_id: "issue1" } }) })));
});

test("an unknown missing fact stays editable and saves typed metadata with Inbox origin", async () => {
  render(<HuntInbox facts={FACTS} inbox={{ ...INBOX, facts: [{ reason: "missing", key: null, label: null, value: null, value_type: null, question: "Available from?", missing_fact: "Start date", issue_id: "issue1", check: null }] }} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Start date" }));
  fireEvent.change(screen.getByLabelText("Fact key"), { target: { value: "availability.start" } });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "date" } });
  fireEvent.change(screen.getByLabelText("Value"), { target: { value: "2027-05-24" } });
  fireEvent.click(screen.getByRole("button", { name: "Save verified fact" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/facts/availability.start", expect.objectContaining({ body: JSON.stringify({ value: "2027-05-24", verified: true, origin: "inbox", label: "Start date", value_type: "date", failure: { issue_id: "issue1" } }) })));
});

test("question disposition sends Inbox origin", async () => {
  render(<HuntInbox facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Review Your extension?" }));
  fireEvent.click(await screen.findByRole("button", { name: "Answer only on the form" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/questions/q1", expect.objectContaining({ body: JSON.stringify({ disposition: "one_off", origin: "inbox" }) })));
});

test("A missing card for a computed fact shows its note and is read-only", async () => {
  const inbox: Inbox = { ...INBOX, facts: [{ reason: "missing", key: "education.graduation_date", label: null, value: null, value_type: null, question: "Graduation?", missing_fact: "Graduation date", issue_id: "i1", check: null }] };
  render(<HuntInbox computed={COMPUTED} facts={FACTS} inbox={inbox} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Facts to verify 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Graduation date" }));
  expect(screen.getByText("Computed from the career record")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Edit the career record" })).toHaveAttribute("href", "/resume/edit");
  expect(screen.queryByLabelText("Value")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save verified fact" })).not.toBeInTheDocument();
});

test("A pattern for a computed fact can be accepted without a stored row", async () => {
  const pattern = { id: "pat9", question_id: null, question_label: "Graduation term?", expression: null, fact_key: "education.graduation_term", origin: "model", formatter: null, status: "candidate" };
  const inbox: Inbox = { ...INBOX, candidates: [{ pattern, answers: [], check: null }] };
  render(<HuntInbox computed={COMPUTED} facts={FACTS} inbox={inbox} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Pattern checks 1" }));
  fireEvent.click(await screen.findByRole("button", { name: "Review Graduation term?" }));
  expect(screen.getByText(/Computed from education.graduation_date/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Accept pattern" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/patterns/pat9", expect.objectContaining({ method: "PATCH" })));
});

test("Linking a computed fact posts the binding without saving a fact", async () => {
  render(<HuntInbox computed={COMPUTED} facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Review Your extension?" }));
  const select = screen.getByLabelText("Fact");
  expect(select.querySelector('optgroup[label="Computed facts"]')).not.toBeNull();
  expect(select.querySelector('option[value="education.gpa"]')).not.toBeNull();
  fireEvent.change(select, { target: { value: "education.gpa" } });
  expect(screen.getByText("Computed from the career record")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Link fact" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/v1/jobs/patterns", expect.objectContaining({ method: "POST", body: expect.stringContaining('"fact_key":"education.gpa"') })));
  expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).includes("/v1/jobs/facts/") && (init as RequestInit | undefined)?.method === "PUT")).toBe(false);
});

test("A typed computed key under Add a new fact blocks the save", async () => {
  render(<HuntInbox computed={COMPUTED} facts={FACTS} inbox={INBOX} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Review Your extension?" }));
  fireEvent.click(screen.getByRole("button", { name: "Add a new fact" }));
  fireEvent.change(screen.getByLabelText("Fact key"), { target: { value: "education.gpa" } });
  expect(screen.getByText("education.gpa is computed; choose it from the list")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save fact and link" })).toBeDisabled();
});
