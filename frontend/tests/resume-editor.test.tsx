import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { ResumeEditor } from "@/components/jobs/resume-editor";
import {
  createMasterResumeDraft,
  getDocuments,
  queueResumePreview,
  updateMasterResumeDraft,
} from "@/lib/jobs-api";

const APPROVED = {
  id: "11111111-1111-4111-8111-111111111111",
  application_id: null,
  document_type: "master_resume",
  title: "Main resume",
  family_id: "22222222-2222-4222-8222-222222222222",
  version: 1,
  source_document_id: null,
  content_text: '{"contact":{"name":"Candidate"}}',
  storage_path: null,
  mime_type: "application/json",
  status: "approved",
  is_current: true,
  created_at: "2026-08-13T00:00:00Z",
  updated_at: "2026-08-13T00:00:00Z",
};

const DRAFT = { ...APPROVED, id: "33333333-3333-4333-8333-333333333333", status: "draft", version: 2 };

vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    getDocuments: vi.fn().mockResolvedValue([]),
    createMasterResumeDraft: vi.fn(),
    updateMasterResumeDraft: vi.fn(),
    queueResumePreview: vi.fn().mockResolvedValue({ task_id: "task" }),
    getDocumentPdf: vi.fn(),
  };
});

function pressSave() {
  fireEvent.keyDown(window, { key: "s", ctrlKey: true });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getDocuments).mockResolvedValue([]);
  vi.mocked(queueResumePreview).mockResolvedValue({ task_id: "task" });
});

test("Ctrl+S on the approved resume creates the working draft", async () => {
  vi.mocked(createMasterResumeDraft).mockResolvedValue({ ...DRAFT });
  vi.mocked(updateMasterResumeDraft).mockClear();

  render(
    <ResumeEditor accessToken="approved" onClose={() => {}} onSaved={() => {}} source={APPROVED} />,
  );

  fireEvent.change(screen.getByLabelText("Resume JSON"), {
    target: { value: '{"contact":{"name":"Updated"}}' },
  });
  pressSave();

  await waitFor(() => expect(createMasterResumeDraft).toHaveBeenCalledWith("approved", {
    contact: { name: "Updated" },
  }));
  expect(updateMasterResumeDraft).not.toHaveBeenCalled();
  await waitFor(() => expect(queueResumePreview).toHaveBeenCalled());
});

test("a second Ctrl+S updates the same draft instead of stacking versions", async () => {
  vi.mocked(createMasterResumeDraft).mockResolvedValue({ ...DRAFT });
  vi.mocked(updateMasterResumeDraft).mockResolvedValue({ ...DRAFT });
  vi.mocked(getDocuments).mockResolvedValue([]);

  render(
    <ResumeEditor accessToken="approved" onClose={() => {}} onSaved={() => {}} source={APPROVED} />,
  );

  pressSave();
  await waitFor(() => expect(createMasterResumeDraft).toHaveBeenCalledTimes(1));

  pressSave();
  await waitFor(() => expect(updateMasterResumeDraft).toHaveBeenCalledTimes(1));
  expect(createMasterResumeDraft).toHaveBeenCalledTimes(1);
});

test("opening an existing draft edits it in place from the first save", async () => {
  vi.mocked(createMasterResumeDraft).mockClear();
  vi.mocked(updateMasterResumeDraft).mockResolvedValue({ ...DRAFT });

  render(
    <ResumeEditor accessToken="approved" onClose={() => {}} onSaved={() => {}} source={DRAFT} />,
  );

  pressSave();

  await waitFor(() => expect(updateMasterResumeDraft).toHaveBeenCalledTimes(1));
  expect(createMasterResumeDraft).not.toHaveBeenCalled();
});

test("invalid JSON is refused before any request is made", async () => {
  vi.mocked(createMasterResumeDraft).mockClear();

  render(
    <ResumeEditor accessToken="approved" onClose={() => {}} onSaved={() => {}} source={APPROVED} />,
  );

  fireEvent.change(screen.getByLabelText("Resume JSON"), { target: { value: "{ not json" } });
  pressSave();

  await waitFor(() => expect(screen.getByText(/Invalid JSON/)).toBeInTheDocument());
  expect(createMasterResumeDraft).not.toHaveBeenCalled();
});

test("the PDF pane is marked stale while the worker recompiles", async () => {
  vi.mocked(createMasterResumeDraft).mockResolvedValue({ ...DRAFT });
  vi.mocked(getDocuments).mockResolvedValue([]);

  render(
    <ResumeEditor accessToken="approved" onClose={() => {}} onSaved={() => {}} source={APPROVED} />,
  );

  pressSave();

  expect(await screen.findByText("Recompiling…")).toBeInTheDocument();
});
