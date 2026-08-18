# Worker current state

Last verified: 2026-08-18

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
- [x] Compile a draft main resume as a `preview_resume` task, reusing the
  one-page verification, without a browser or an application.
- [ ] Run the first paired worker against a hosted application task.
- [ ] Complete the first hosted prepare/review/approve/visible-autofill flow.
- [x] Generate the resume from the real canonical LaTeX template rather than a
  generic one, verified by matching page geometry exactly.
- [ ] Drive Windows Chrome instead of WSLg Chrome (files staged; needs
  `wsl --shutdown` to activate mirrored networking).

Chrome, Claude, `uv`, `latexmk`, and `pdfinfo` are installed and locally
verified, and pairing is complete. A standalone `doctor` run reports `Chrome CDP`
as failed whenever Chrome is not already listening on `127.0.0.1:9222`; `run.sh`
launches that Chrome first, so the check passes under the normal launcher. Doctor
failures are advisory: `run.sh` warns and still starts the worker so hosted
outages can recover on their own.


## Local CLI migration, 2026-08-18

Jobs is moving to a local CLI-first shape. The design is recorded in
`~/.claude/plans/alright-great-lets-finalize-greedy-bumblebee.md`. Phase 1 files
and Phase 5's template work are done; the rest is blocked on a WSL restart.

Windows Chrome, staged but not yet active:

- `C:\Users\eahtr\.wslconfig` now sets `networkingMode=mirrored`. This does not
  take effect until `wsl --shutdown` is run from Windows.
- `run.sh` launches `/mnt/c/Program Files/Google/Chrome/Application/chrome.exe`
  with the Windows profile `C:\Users\eahtr\personal-os-chrome`, and fails with a
  pointed message if CDP is unreachable.
- Verified during design against Chrome 151: a dedicated `--user-data-dir` serves
  CDP correctly, while the default profile returns 404 on `/json/*` because
  Chrome 136+ blocks remote debugging there. The dedicated profile is required.
- Not yet verified: whether `set_input_files` wants the WSL or the Windows path
  for resume upload. `wslpath -w` is the conversion tool, as in the legacy
  `archived/jobs-legacy/build-resume.sh`.

Resume template, done and verified:

- `build_latex` now emits the canonical template recovered from commit `3fe5db1`
  (`jobs/resume/Resume.tex`), with the margin and spacing values from
  `archived/jobs-legacy/web/tests/fixtures/resume-base.tex`, which is what the
  real resume was actually built with.
- Verified objectively: the generated PDF matches the reference geometry to the
  decimal (`xMin` 14.400000, `xMax` 597.604560, top 14.996431) and is one page.
  The previous generic template rendered 540pt wide and spilled to two pages.
- `bin/Resume.pdf` is a stale generated artifact, not the current resume. Do not
  treat it as the source of truth.
- `main_resume.json` now matches the current resume: 17 bullets, four skill
  categories, `Example Nuclear Group (iFAN) Laboratory`, and
  `Personal Agent OS`. The degree stays `Bachelor of Science in Computer Science`
  by explicit decision, and graduation is `May 2028`.
- The resume contact email is `ehulewicz3@gatech.edu`, matching the sent resume,
  not the Google account address used for authentication.
- Worker Ruff, mypy, and all 22 tests pass.
