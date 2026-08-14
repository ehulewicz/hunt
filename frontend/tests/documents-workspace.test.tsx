import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { DocumentsWorkspace } from "@/components/jobs/documents-workspace";
import { getDocuments, queueResumePreview } from "@/lib/jobs-api";

const MASTER_RESUME = {
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

vi.mock("@/components/workspace", () => ({ useAccessToken: () => "approved" }));
vi.mock("@/lib/jobs-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/jobs-api")>();
  return {
    ...original,
    getDocuments: vi.fn(),
    createMasterResumeDraft: vi.fn(),
    updateMasterResumeDraft: vi.fn(),
    queueResumePreview: vi.fn(),
    getDocumentPdf: vi.fn(),
  };
});

test("the canonical resume opens in the side-by-side editor", async () => {
  vi.mocked(getDocuments).mockResolvedValue([MASTER_RESUME]);

  render(<DocumentsWorkspace documentType="resume" />);

  await waitFor(() => expect(screen.getByText("Main resume")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /View & edit/ }));

  const editor = screen.getByLabelText("Resume JSON") as HTMLTextAreaElement;
  expect(editor.value).toContain('"name": "Candidate"');
  expect(screen.getByText("Compiled PDF")).toBeInTheDocument();
});

test("only draft resumes offer a standalone preview", async () => {
  vi.mocked(getDocuments).mockResolvedValue([
    { ...MASTER_RESUME, status: "draft", version: 2 },
  ]);
  vi.mocked(queueResumePreview).mockResolvedValue({ task_id: "task" });

  render(<DocumentsWorkspace documentType="resume" />);

  await waitFor(() => expect(screen.getByText("Main resume")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /Generate preview/ }));

  await waitFor(() => expect(queueResumePreview).toHaveBeenCalled());
});
