import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { LibraryEditor } from "@/components/library-editor";

vi.mock("@/lib/device", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/device")>()),
  freshAccessToken: vi.fn(async () => "token"),
}));

function entry(overrides: Record<string, unknown>) {
  return {
    id: "id", code: "1.1", section: "story", entry_id: null, subject: null,
    prompt: "Tell the story.", guidance: "", answer: null, display_order: 0,
    created_at: "2026-10-30", updated_at: "2026-10-30", ...overrides,
  };
}

const ENTRIES = [
  entry({ code: "2.1", section: "direction", prompt: "Where are you headed?", answer: "Forward.", subject: "Aim" }),
  entry({ code: "1.1", section: "interest", prompt: "What do you like?" }),
  entry({ code: "3.1", section: "story", prompt: "Tell the story." }),
  entry({ code: "3.2", section: "story", prompt: "Another story.", answer: "Once upon a time." }),
  entry({ code: "4.1", section: "note", prompt: "A technical note." }),
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "PUT" && input.includes("/v1/jobs/library/")) {
      const body = JSON.parse(String(init.body));
      return Response.json({ ...entry({}), ...body, id: "id", created_at: "2026-10-30", updated_at: "2026-10-30" });
    }
    if (input.endsWith("/v1/jobs/library")) return Response.json(ENTRIES);
    return Response.json([]);
  }));
});

test("entries group under their section headings in order", async () => {
  render(<LibraryEditor />);
  await screen.findByLabelText("Answer 1.1");
  const headings = screen.getAllByRole("heading", { level: 2 }).map((node) => node.textContent);
  expect(headings).toEqual(["Interests", "Direction", "Story bank", "Technical notes"]);
  expect(screen.getByText("What do you like?")).toBeInTheDocument();
  expect(screen.getByText("Aim")).toBeInTheDocument();
});

test("the unanswered filter hides answered entries and counts words live", async () => {
  render(<LibraryEditor />);
  const box = await screen.findByLabelText("Answer 3.2");
  expect(screen.getByText("4 words")).toBeInTheDocument();
  fireEvent.change(box, { target: { value: "One two three" } });
  expect(screen.getByText("3 words")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Unanswered only"));
  expect(screen.queryByLabelText("Answer 3.2")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Answer 2.1")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Answer 1.1")).toBeInTheDocument();
});

test("an entry saves its fields with the new answer through the per-code PUT", async () => {
  render(<LibraryEditor />);
  const box = await screen.findByLabelText("Answer 1.1");
  fireEvent.change(box, { target: { value: "My written answer." } });
  fireEvent.click(screen.getAllByRole("button", { name: "Save answer" })[0]);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    "https://api.test/v1/jobs/library/1.1",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        code: "1.1", section: "interest", entry_id: null, subject: null,
        prompt: "What do you like?", guidance: "", answer: "My written answer.",
        display_order: 0,
      }),
    }),
  ));
  expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
});

test("an emptied answer saves null", async () => {
  render(<LibraryEditor />);
  const box = await screen.findByLabelText("Answer 3.2");
  fireEvent.change(box, { target: { value: "   " } });
  fireEvent.click(screen.getAllByRole("button", { name: "Save answer" })[3]);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    "https://api.test/v1/jobs/library/3.2",
    expect.objectContaining({ body: expect.stringContaining('"answer":null') }),
  ));
});

test("Ask Claude is disabled on an empty answer with the first-draft reason", async () => {
  render(<LibraryEditor />);
  await screen.findByLabelText("Answer 1.1");
  const panels = screen.getAllByRole("button", { name: "Ask Claude" });
  fireEvent.click(panels[0]); // interest 1.1 has no answer
  expect(await screen.findByText("Write a first draft; Ask Claude only revises your words.")).toBeInTheDocument();
});

test("an empty library says to import once", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  render(<LibraryEditor />);
  expect(await screen.findByText(/The library is empty/)).toBeInTheDocument();
  expect(screen.getByText(/os hunt library import/)).toBeInTheDocument();
});

test("the saved notice stays visible after a save", async () => {
  render(<LibraryEditor />);
  const box = await screen.findByLabelText("Answer 1.1");
  fireEvent.change(box, { target: { value: "Kept words." } });
  fireEvent.click(screen.getAllByRole("button", { name: "Save answer" })[0]);
  const notice = await screen.findByRole("status");
  expect(notice).toHaveTextContent("Saved.");
  // The editor was not remounted: the notice is still there on the next render.
  fireEvent.change(box, { target: { value: "Kept words, edited again." } });
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

test("the human badge shows the score band, unchecked, or nothing", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.endsWith("/v1/jobs/library")) return Response.json([
      entry({ code: "1.1", prompt: "High.", answer: "Words.", human_percent: 92 }),
      entry({ code: "1.2", prompt: "Mid.", answer: "Words.", human_percent: 65 }),
      entry({ code: "1.3", prompt: "Low.", answer: "Words.", human_percent: 30 }),
      entry({ code: "1.4", prompt: "Unchecked.", answer: "Words." }),
      entry({ code: "1.5", prompt: "Empty.", answer: null }),
    ]);
    return Response.json([]);
  }));
  render(<LibraryEditor />);
  await screen.findByLabelText("Answer 1.1");
  expect(screen.getByText("Human 92%")).toHaveClass("human-high");
  expect(screen.getByText("Human 65%")).toHaveClass("human-mid");
  expect(screen.getByText("Human 30%")).toHaveClass("human-low");
  expect(screen.getByText("Not checked")).toHaveClass("human-unchecked");
  expect(screen.queryByText("Human 0%")).not.toBeInTheDocument();
});

test("the below-80 filter keeps unchecked and low-scoring answered entries", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.endsWith("/v1/jobs/library")) return Response.json([
      entry({ code: "1.1", prompt: "High.", answer: "Words.", human_percent: 92 }),
      entry({ code: "1.2", prompt: "Mid.", answer: "Words.", human_percent: 65 }),
      entry({ code: "1.3", prompt: "Unchecked.", answer: "Words." }),
      entry({ code: "1.4", prompt: "Empty.", answer: null }),
    ]);
    return Response.json([]);
  }));
  render(<LibraryEditor />);
  await screen.findByLabelText("Answer 1.1");
  fireEvent.click(screen.getByLabelText("Below 80% human"));
  expect(screen.getByLabelText("Answer 1.2")).toBeInTheDocument();
  expect(screen.getByLabelText("Answer 1.3")).toBeInTheDocument();
  expect(screen.queryByLabelText("Answer 1.1")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Answer 1.4")).not.toBeInTheDocument();
});

test("saving an answer resets the badge to Not checked", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      return Response.json({ ...entry({}), ...body, id: "id", created_at: "2026-10-30", updated_at: "2026-10-30" });
    }
    if (input.endsWith("/v1/jobs/library")) return Response.json([
      entry({ code: "1.1", prompt: "A prompt.", answer: "Words.", human_percent: 92 }),
    ]);
    return Response.json([]);
  }));
  render(<LibraryEditor />);
  await screen.findByLabelText("Answer 1.1");
  expect(screen.getByText("Human 92%")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Answer 1.1"), { target: { value: "New words." } });
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
  expect(await screen.findByText("Not checked")).toBeInTheDocument();
  expect(screen.queryByText("Human 92%")).not.toBeInTheDocument();
});
