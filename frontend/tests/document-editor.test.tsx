import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { DocumentEditor } from "@/components/document-editor";
import { askClaude } from "@/lib/device";

const DEVICE = {
  id: "d1", name: "desk", url: "https://desk.tail.ts.net", commit: "abc",
  local_changes: false, ready: true, problem: null, active_run_id: null,
  run: null, last_seen_at: "2026-10-30", online: true,
};

vi.mock("@/lib/device", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/device")>()),
  askClaude: vi.fn(async () => ({ proposal: "A revised answer.", summary: "Tightened the first paragraph." })),
  freshAccessToken: vi.fn(async () => "token"),
}));

function renderEditor(onSave = vi.fn(async () => "Saved."), claude = { surface: "library" as const, onAccept: vi.fn() }) {
  const onChange = vi.fn();
  render(
    <DocumentEditor
      claude={claude}
      label="Answer"
      onChange={onChange}
      onSave={onSave}
      saveLabel="Save answer"
      value="The original answer."
    />,
  );
  return { onChange, onSave, onAccept: claude.onAccept };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([DEVICE])));
});

test("Ask Claude is disabled with a reason when no device is online", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([{ ...DEVICE, online: false }])));
  renderEditor();
  fireEvent.click(screen.getByRole("button", { name: "Ask Claude" }));
  expect(await screen.findByText("No device is online to run Claude; start one with os device serve.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Propose a revision" })).toBeDisabled();
});

test("a caller-given disabled reason shows instead", async () => {
  render(
    <DocumentEditor
      claude={{ surface: "library", onAccept: vi.fn(), disabledReason: "Write a first draft; Ask Claude only revises your words." }}
      label="Answer"
      onChange={vi.fn()}
      onSave={vi.fn(async () => "Saved.")}
      saveLabel="Save answer"
      value=""
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask Claude" }));
  expect(await screen.findByText("Write a first draft; Ask Claude only revises your words.")).toBeInTheDocument();
});

test("a proposal shows its summary and diff; Accept calls onAccept", async () => {
  const { onAccept } = renderEditor();
  fireEvent.click(screen.getByRole("button", { name: "Ask Claude" }));
  fireEvent.change(screen.getByPlaceholderText("What should change?"), { target: { value: "Tighter" } });
  const propose = await screen.findByRole("button", { name: "Propose a revision" });
  await waitFor(() => expect(propose).toBeEnabled());
  fireEvent.click(propose);
  expect(await screen.findByText(/Tightened the first paragraph/)).toBeInTheDocument();
  expect(askClaude).toHaveBeenCalledWith(
    expect.objectContaining({ url: DEVICE.url }),
    { surface: "library", instruction: "Tighter", document: "The original answer." },
  );
  const diff = screen.getByLabelText("Proposed changes");
  expect(diff.querySelector(".diff-removed")).not.toBeNull();
  expect(diff.querySelector(".diff-added")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(onAccept).toHaveBeenCalledWith("A revised answer."));
  await waitFor(() => expect(screen.queryByLabelText("Proposed changes")).not.toBeInTheDocument());
});

test("Discard drops the proposal without saving", async () => {
  const { onAccept, onSave } = renderEditor();
  fireEvent.click(screen.getByRole("button", { name: "Ask Claude" }));
  fireEvent.change(screen.getByPlaceholderText("What should change?"), { target: { value: "Tighter" } });
  const propose = await screen.findByRole("button", { name: "Propose a revision" });
  await waitFor(() => expect(propose).toBeEnabled());
  fireEvent.click(propose);
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  expect(screen.queryByLabelText("Proposed changes")).not.toBeInTheDocument();
  expect(onAccept).not.toHaveBeenCalled();
  expect(onSave).not.toHaveBeenCalled();
});

test("a device error shows inline", async () => {
  vi.mocked(askClaude).mockRejectedValueOnce(new Error("The device is busy"));
  renderEditor();
  fireEvent.click(screen.getByRole("button", { name: "Ask Claude" }));
  fireEvent.change(screen.getByPlaceholderText("What should change?"), { target: { value: "Tighter" } });
  const propose = await screen.findByRole("button", { name: "Propose a revision" });
  await waitFor(() => expect(propose).toBeEnabled());
  fireEvent.click(propose);
  expect(await screen.findByRole("alert")).toHaveTextContent("The device is busy");
});

test("a save refusal shows inline and the notice shows on success", async () => {
  const onSave = vi.fn(async () => { throw new Error("resume must stay within 1 page(s); generated 2"); });
  render(
    <DocumentEditor label="Record" monospace onChange={vi.fn()} onSave={onSave} saveLabel="Save record" value="{}" />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Save record" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("1 page");
});
