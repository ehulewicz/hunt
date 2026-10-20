import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import HuntPage from "@/app/(workspace)/hunt/page";
import { openDeviceSession, startRun } from "@/lib/device";

vi.mock("@/lib/device", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/device")>(),
  freshAccessToken: vi.fn(async () => "token"),
  openDeviceSession: vi.fn(() => ({ send: vi.fn(), close: vi.fn() })),
  startRun: vi.fn(),
}));

const DEVICE = {
  id: "d1", name: "DESKTOP-HOME", url: "https://home.tail1234.ts.net", commit: "abcdef123",
  local_changes: false, ready: true, problem: null, active_run_id: null, run: null,
  last_seen_at: "2026-10-20T12:00:00Z", online: true,
};
const APPLICATION = {
  id: "a1", posting_id: "p1", company_name: "Acme", title: "Software Engineer Intern",
  location: "New York", application_url: "https://jobs.example/apply", status: "applying",
  applied_at: null, next_action: null, next_action_at: null, notes: null,
  unanswered_required_questions: ["Date of birth"], created_at: "2026-10-20T12:00:00Z",
  updated_at: "2026-10-20T12:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.mocked(startRun).mockResolvedValue({ run: null } as never);
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.endsWith("/devices")) return Response.json([DEVICE]);
    if (input.endsWith("/applications")) return Response.json([
      APPLICATION,
      { ...APPLICATION, id: "a2", company_name: "Orbit", status: "rejected", applied_at: "2026-10-19T12:00:00Z" },
      { ...APPLICATION, id: "a3", company_name: "Linear", status: "interview", applied_at: "2026-10-18T12:00:00Z" },
    ]);
    if (input.endsWith("/inbox")) return Response.json({ candidates: [], questions: [], prose: [] });
    if (input.endsWith("/facts")) return Response.json([]);
    return Response.json({ items: [], total: 42, limit: 1, offset: 0 });
  }));
});

test("Hunt opens an overview with real totals without connecting to Chrome", async () => {
  render(<HuntPage />);
  const totals = await screen.findByRole("group", { name: "Application totals" });
  await waitFor(() => expect(within(totals).getByLabelText("Applied")).toHaveTextContent("2"));
  expect(within(totals).getByLabelText("Pending")).toHaveTextContent("1");
  expect(within(totals).getByLabelText("On the board")).toHaveTextContent("42");
  expect(within(totals).queryByLabelText("Interviews")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Needs attention" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Live view" })).not.toBeInTheDocument();
  expect(openDeviceSession).not.toHaveBeenCalled();
});

test("a successful start opens the workspace and returning to the overview closes its session", async () => {
  render(<HuntPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Next on board" }));
  await waitFor(() => expect(startRun).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }), null, false));
  await screen.findByRole("region", { name: "Live view" });
  expect(openDeviceSession).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Back to overview" }));
  expect(screen.queryByRole("region", { name: "Live view" })).not.toBeInTheDocument();
  expect(vi.mocked(openDeviceSession).mock.results[0].value.close).toHaveBeenCalled();
});

test("a failed start stays on the dashboard with the entered reference", async () => {
  vi.mocked(startRun).mockRejectedValueOnce(new Error("This device is already busy"));
  render(<HuntPage />);
  const input = await screen.findByRole("textbox", { name: "Apply to" });
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "https://jobs.example/role" } });
  fireEvent.click(screen.getByRole("button", { name: "Start application" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This device is already busy");
  expect(input).toHaveValue("https://jobs.example/role");
  expect(openDeviceSession).not.toHaveBeenCalled();
});

test("a pending application resumes by posting id, never by the discovery URL", async () => {
  render(<HuntPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Resume application at Acme" }));
  await waitFor(() => expect(startRun).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }), "p1", false));
});

test("an existing terminal run opens without starting another application", async () => {
  const fetcher = vi.mocked(fetch);
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (...args) => String(args[0]).endsWith("/devices")
    ? Response.json([{ ...DEVICE, active_run_id: "r1", run: { company_name: "Acme", title: "SWE Intern" } }])
    : original(...args));
  render(<HuntPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Open application at Acme" }));
  await screen.findByRole("region", { name: "Live view" });
  expect(startRun).not.toHaveBeenCalled();
});

test("a confirmed ended run clears a stale busy heartbeat immediately", async () => {
  const fetcher = vi.mocked(fetch);
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (...args) => String(args[0]).endsWith("/devices")
    ? Response.json([{ ...DEVICE, active_run_id: "r1", run: { company_name: "Acme", title: "SWE Intern" } }])
    : original(...args));
  render(<HuntPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Open application at Acme" }));
  const handlers = vi.mocked(openDeviceSession).mock.calls[0][2];
  act(() => handlers.onMessage({ type: "run", run: {
    key: "k1", run_id: "r1", phase: "ended", company: "Acme", title: "SWE Intern", outcome: "submitted",
    origin: "page", reference: null, note: null, remote: true, letter: null, gaps: [], tab: null,
  } }));
  fireEvent.click(screen.getByRole("button", { name: "Back to overview" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Next on board" })).toBeEnabled());
  expect(screen.queryByRole("button", { name: "Open application at Acme" })).not.toBeInTheDocument();
});

test("identical company and role names do not make Resume attach to a different posting", async () => {
  const fetcher = vi.mocked(fetch);
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (...args) => String(args[0]).endsWith("/devices")
    ? Response.json([{ ...DEVICE, active_run_id: "other-posting-run", run: { company_name: APPLICATION.company_name, title: APPLICATION.title } }])
    : original(...args));
  render(<HuntPage />);
  const resume = await screen.findByRole("button", { name: "Resume application at Acme" });
  expect(resume).toBeDisabled();
  expect(openDeviceSession).not.toHaveBeenCalled();
  expect(startRun).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Open application at Acme" })).toBeEnabled();
});

test("an unavailable overview does not pretend its totals are zero", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => input.endsWith("/devices")
    ? Response.json([DEVICE])
    : Response.json({ detail: "History is unavailable" }, { status: 503 })));
  render(<HuntPage />);
  expect(await screen.findByText(/History is unavailable/)).toBeInTheDocument();
  expect(screen.getByLabelText("Applied")).toHaveTextContent("—");
  expect(screen.getByRole("button", { name: "Next on board" })).toBeEnabled();
});

test("reviewing prose saves an explicit approval through the API", async () => {
  const fetcher = vi.mocked(fetch);
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (...args) => {
    if (String(args[0]).endsWith("/inbox")) return Response.json({
      candidates: [], questions: [], prose: [{ id: "prose1", question_label: "Why Acme?", company_name: "Acme", text: "I build useful tools.", approved: false }],
    });
    return original(...args);
  });
  render(<HuntPage />);
  fireEvent.click(await screen.findByRole("button", { name: /Prose.*1/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review Why Acme?" }));
  const editor = screen.getByRole("textbox", { name: "Answer" });
  fireEvent.change(editor, { target: { value: "I build tools people use." } });
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Approve for reuse" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/prose/prose1"), expect.objectContaining({
    method: "PATCH", body: JSON.stringify({ text: "I build tools people use.", approved: true }),
  })));
});
