# Worker current state

Last verified: 2026-08-13

## Milestones

- [x] Implement a foreground Python 3.13 worker launched from PowerShell/WSL.
- [x] Pair through a revocable 30-day API credential stored only in the machine's
  local user configuration.
- [x] Launch installed Linux Chrome visibly through WSLg with a dedicated profile
  and loopback CDP; PowerShell delegates to the WSL launcher.
- [x] Capture the direct company/ATS page and visible form labels without
  submitting anything.
- [x] Use subscription-authenticated `claude -p` for a minimal JSON Patch against
  the canonical `main` resume.
- [x] Restrict edits to summary, bullet, and skill text; preserve identity,
  education, employers/projects, dates, structure, and bullet counts; reject
  changes over 10 percent.
- [x] Require added keywords to be grounded in both the listing and known resume
  facts.
- [x] Generate and verify a one-page PDF, upload it privately, and leave it as a
  draft requiring user approval.
- [x] Add pairing/API/Chrome/Claude/latexmk/pdfinfo doctor checks and exact setup,
  recovery, and revocation instructions.
- [x] Maintain continuous lease heartbeats, stop after cancellation/lost leases,
  and classify only transient failures for retry.
- [x] Autofill deterministic Profile facts, the approved PDF, and exact-match
  approved long-form answers while leaving EEO, consent, unknown, and Submit empty.
- [x] Pass Ruff, mypy, and all 18 tests; the existing canonical PDF smoke path is
  unchanged.
- [x] Exit with a single-line message and status 1, rather than a traceback, when
  the configuration is missing or unreadable.
- [x] Pair this machine against Cloud Run revision `personal-os-api-00013-dg6`;
  `doctor` reports the worker credential accepted.
- [x] Compare tailoring keywords by stem so inflected forms of approved facts are
  not treated as new claims, and skip only ungrounded patches rather than failing
  preparation; report them as `skipped_patches`.
- [ ] Run the first paired worker against a hosted application task.
- [ ] Complete the first hosted prepare/review/approve/visible-autofill flow.

Chrome, Claude, `uv`, `latexmk`, and `pdfinfo` are installed and locally
verified, and pairing is complete. A standalone `doctor` run reports `Chrome CDP`
as failed whenever Chrome is not already listening on `127.0.0.1:9222`; `run.sh`
launches that Chrome first, so the check passes under the normal launcher. Doctor
failures are advisory: `run.sh` warns and still starts the worker so hosted
outages can recover on their own.
