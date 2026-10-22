import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { HuntLibraryPanel } from "@/components/hunt-library";
import { ReviewForm } from "@/components/hunt-review";
import type { Fact } from "@/lib/api";

vi.mock("@/lib/device", () => ({ freshAccessToken: vi.fn(async () => "token") }));

const FACT: Fact = { key: "education.gpa", label: "GPA override", value: 3.78, value_type: "number", verified_at: "2026-10-22", updated_at: "2026-10-22" };
let facts: Fact[];
let deletes: string[];
let failDelete: boolean;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  facts = [FACT];
  deletes = [];
  failDelete = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      deletes.push(input);
      if (failDelete) return Response.json({ detail: "Could not delete fact" }, { status: 503 });
      facts = [];
      return new Response(null, { status: 204 });
    }
    return Response.json(input.endsWith("/facts") ? facts : []);
  }));
});

async function openFact() {
  fireEvent.click(screen.getByRole("button", { name: /^Facts/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Review GPA override" }));
}

test("Library confirms deletion, can cancel, and refreshes its rows and the Inbox", async () => {
  const onRefresh = vi.fn();
  render(<HuntLibraryPanel onRefresh={onRefresh} />);
  await openFact();
  fireEvent.click(screen.getByRole("button", { name: "Delete fact" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Delete GPA override?");
  expect(deletes).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("button", { name: "Confirm delete" })).not.toBeInTheDocument();
  expect(deletes).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Delete fact" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
  expect(deletes).toEqual(["https://api.test/v1/jobs/facts/education.gpa"]);
  expect(fetch).toHaveBeenCalledWith(deletes[0], expect.objectContaining({ method: "DELETE", headers: { Authorization: "Bearer token" } }));
  expect(await screen.findByText("Deleted.")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Facts 0" })).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Review GPA override" })).not.toBeInTheDocument();
});

test("a failed delete preserves the fact and supports a deliberate retry", async () => {
  failDelete = true;
  const onRefresh = vi.fn();
  render(<HuntLibraryPanel onRefresh={onRefresh} />);
  await openFact();
  fireEvent.click(screen.getByRole("button", { name: "Delete fact" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  expect(await screen.findByText("Could not delete fact")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Value" })).toHaveValue("3.78");
  expect(onRefresh).not.toHaveBeenCalled();
  failDelete = false;
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
});

test("facts without a stored row never offer Delete", () => {
  render(<ReviewForm allowDelete facts={[]} onBack={vi.fn()} onDone={vi.fn()} review={{ kind: "fact", item: { ...FACT, key: "education.school_name", label: "School" } }} />);
  expect(screen.queryByRole("button", { name: "Delete fact" })).not.toBeInTheDocument();
  expect(screen.getByText(/os hunt resume edit/)).toBeInTheDocument();
  expect(screen.getByText(/code/)).toBeInTheDocument();
});
