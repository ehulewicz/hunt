import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { ResumeEditor } from "@/components/resume-editor";

vi.mock("@/lib/device", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/device")>()),
  freshAccessToken: vi.fn(async () => "token"),
}));

const DOCUMENT = { contact: { name: "Alex Example" }, education: [{ school: "Example University", primary: true }], experience: [{ title: "Engineer" }] };
const RECORD = { education: {}, schools: [], entries: [], skills: [], document: DOCUMENT };

let saved: Record<string, { status: number; body: unknown }> = {};

beforeEach(() => {
  vi.clearAllMocks();
  saved = {};
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (!input.endsWith("/v1/jobs/career")) return Response.json([]);
    if (init?.method === "PUT") {
      const stored = saved["career"];
      if (stored) return Response.json({ detail: stored.body }, { status: stored.status });
      return Response.json(RECORD);
    }
    return Response.json(RECORD);
  }));
});

test("the record loads as JSON and a parse error shows inline without sending", async () => {
  render(<ResumeEditor />);
  const editor = await screen.findByLabelText("Career record");
  await waitFor(() => expect(editor).toHaveValue(JSON.stringify(DOCUMENT, null, 2)));
  fireEvent.change(editor, { target: { value: "{ not json" } });
  fireEvent.click(screen.getByRole("button", { name: "Save record" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
  expect(fetch).toHaveBeenCalledTimes(1); // only the GET
});

test("a 422 detail shows inline and a save reloads the response", async () => {
  saved["career"] = { status: 422, body: "resume must stay within 1 page(s); generated 2" };
  render(<ResumeEditor />);
  const editor = await screen.findByLabelText("Career record");
  await waitFor(() => expect(editor).toHaveValue(JSON.stringify(DOCUMENT, null, 2)));
  fireEvent.click(screen.getByRole("button", { name: "Save record" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("1 page(s)");

  saved["career"] = { status: 200, body: null };
  const returned = { ...RECORD, document: { ...DOCUMENT, experience: [{ title: "Engineer", id: "new-id" }] } };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "PUT") return Response.json(returned);
    return Response.json(RECORD);
  }));
  fireEvent.change(editor, { target: { value: JSON.stringify({ ...DOCUMENT, changed: true }) } });
  fireEvent.click(screen.getByRole("button", { name: "Save record" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Saved. /resume now serves the new PDF.");
  await waitFor(() => expect(screen.getByLabelText("Career record")).toHaveValue(JSON.stringify(returned.document, null, 2)));
});
