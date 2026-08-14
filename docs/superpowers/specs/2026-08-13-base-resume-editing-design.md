# Base resume viewing and editing

Date: 2026-08-13

## Problem

The canonical `master_resume` document is listed on the Resumes page but shows
no content and offers no actions. Its text determines every tailored resume, the
generated one-page PDF, and the approved-fact corpus the tailoring guard uses to
decide which keywords are grounded. The user cannot see or change it.

## Goal

See the canonical resume, edit it as raw JSON, preview the compiled PDF, and
approve the result as the new canonical version.

## Constraints

- `app.protect_approved_document()` rejects content changes to approved
  documents. Editing must create a new version, never an update in place.
- The backend image has no LaTeX. Only the worker can compile a PDF.
- The LaTeX template fixes section order as Education, Experience, Projects,
  Skills. Ordering control means order within a section.
- Personal OS stays single-user and hosted-only. No new local stack.

## Decisions

- **Raw JSON editor.** The user wants full control over the text that becomes the
  PDF, accepting that a wrong-shaped document is possible.
- **Worker-compiled preview.** Saving a draft does not compile; an explicit
  preview queues a worker task that compiles and verifies one page.
- **Draft to preview to approve.** Saving creates a draft version that does not
  affect tailoring. Approving makes it canonical.

## No migration required

- `worker_tasks.task_type` is free text with no check constraint, so
  `preview_resume` needs no DDL.
- The claim query filters only on domain and status, so the worker picks up
  preview tasks unchanged.
- `approve_document` already clears `is_current` across the family and already
  exempts `master_resume` from the storage-path requirement.

## Design

### Backend

- `POST /v1/jobs/documents/master-resume` validates the payload against a model
  mirroring what the LaTeX template reads, then inserts `version = max + 1` with
  `status = 'draft'` into the existing master-resume family.
- `POST /v1/jobs/documents/{document_id}/preview` queues a `preview_resume` task
  for a master-resume draft, at most one active per document.
- `GET /v1/jobs/worker/documents/{document_id}` returns the draft JSON to a
  paired worker.
- The worker PDF upload endpoint derives its storage path from `application_id`,
  which a preview task does not have. Preview PDFs are stored at
  `{user_id}/resumes/{document_id}/{task_id}.pdf`.
- `complete_task` gains a `preview_resume` branch that writes `storage_path` onto
  the draft document.

### Worker

`process_task` gains a `preview_resume` branch: fetch the document JSON, call
`compile_resume`, upload the PDF, and complete. `compile_resume` already enforces
the one-page limit, so the failure surfaces before approval. No new dependencies
and no browser.

### Frontend

`documents-workspace.tsx` renders `content_text` for master-resume rows and adds
Edit, Save draft, Generate preview, View PDF, and Approve. The editor blocks
invalid JSON before sending.

## Consequences

- Tailoring resolves the master resume with `status = 'approved' and is_current`,
  so drafts cannot affect in-flight applications.
- Old versions stay intact, so previous tailoring runs still resolve their
  `source_document_id`.
- Because the guard builds its fact context from the current approved resume,
  adding a true bullet widens what tailoring may legitimately say.

## Risks

- Valid JSON with the wrong shape is the main failure mode. Backend schema
  validation mitigates it but cannot catch everything a structured form would.
- Preview requires a running worker. Otherwise the task stays queued.
- Editing the base resume does not retroactively change existing tailored
  resumes.

## Testing

- Backend: schema validation for valid and malformed payloads, preview queueing,
  and the `preview_resume` completion branch.
- Worker: preview compiles and uploads; malformed JSON fails cleanly.
- Frontend: invalid JSON blocked, and save, preview, and approve wiring.
