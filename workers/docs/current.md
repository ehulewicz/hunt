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
- [ ] Run the first paired worker against a hosted application task.
- [ ] Pair this machine after deployment and complete the first hosted
  prepare/review/approve/visible-autofill flow.
