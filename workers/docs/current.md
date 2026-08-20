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
- [x] Drive Windows Chrome instead of WSLg Chrome, verified over CDP from WSL.

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
- Verified 2026-08-19: mirrored networking is active (`loopback0` present, `eth0`
  carries the real LAN address), and `curl http://127.0.0.1:9333/json/version`
  from WSL returns Chrome 151's CDP payload.
- The CDP port moved from 9222 to 9333. Chrome's default port is routinely taken
  by an already-running default-profile browser, which both 404s on `/json/*`
  and forces our dedicated-profile instance onto `[::1]`, where mirrored mode
  does not reach it. A dedicated port avoids that collision permanently.
- `set_input_files` is resolved: Playwright reads the file on the WSL side and
  streams its bytes over CDP, so it wants the **WSL path**. A Windows path raises
  `FileNotFoundError`. Verified by uploading the 114,364-byte resume into a file
  input and reading back the exact name and size. `browser.py` needs no change
  and artifacts do not need staging under `/mnt/c`.

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


## CLI sign-in, verified 2026-08-19

`login` and `whoami` work. The CLI now holds a real Google session instead of a
second credential system, so the backend accepts exactly the token it already
accepted from the browser and `auth.py` needed no change.

- `auth.py` implements the RFC 8252 pattern: a loopback listener on
  `127.0.0.1:54321`, a PKCE `s256` challenge, and a code exchange against
  `/auth/v1/token?grant_type=pkce`. An intercepted code is useless without the
  verifier, which never leaves the process.
- The fixed callback port is deliberate. Supabase documents redirect globbing for
  paths, not for ports, so a wildcard port may silently fail to match.
- Sign-in opens the dedicated Windows Chrome profile, which already holds the
  Google session, so the flow completes without a prompt.
- `session.json` is mode 600 and stores the refresh token. Access tokens last one
  hour, matching the project's `jwt_exp`, and `active_session` refreshes with a
  two-minute margin so a request never races the expiry.
- `cli.json` holds `api_url`, `supabase_url`, and the publishable key. The key is
  public by design and ships in browser bundles, but it stays machine-local
  rather than in the repository.
- Verified: `whoami` prints the account, and an authenticated listings call
  returned 392 rows through the local API.
- Worker Ruff, mypy, and all 30 tests pass, including 8 new auth tests covering
  the PKCE challenge, expiry margin, session round-trip, and failure paths.

Installed on PATH, 2026-08-19:

- The console script is `hunt`, installed with `uv tool install --editable
  ./workers` into `~/.local/bin`, which is already on PATH and is also uv's tool
  bin directory. Editable means source changes take effect without reinstalling.
- The name is not `jobs`. `jobs` is a POSIX shell builtin and builtins are
  resolved before PATH, so the executable was installed correctly and still
  unreachable from any interactive shell. `hunt` has no builtin or coreutils
  collision, and `~/.local/bin` precedes `/usr/games`, where the only similarly
  named program lives.
- The API installs the same way from `./backend` and exposes `personal-os-api`,
  which defaults to `127.0.0.1:8000` and accepts `--reload`.
- `Settings` previously read `.env` relative to the process working directory, so
  the API only started from `backend/`. It now also resolves the file from the
  package directory, and a missing file is ignored, which is how the container
  runs. Verified by starting the API from `/tmp` and running `hunt whoami` from
  the home directory.

## Legacy pairing removed and services self-start, 2026-08-19

The second credential system is gone and the CLI manages its own dependencies, so
the whole workflow is `hunt` and nothing else.

- `pair`, `WorkerConfig`, and the token file are deleted. `run` and `doctor` use
  the Google session, so the command surface is `login`, `whoami`, `run`, `doctor`.
- Every command that talks to the API goes through `cli.connected()`, which loads
  configuration, guarantees the API is up, and returns a fresh access token.
- `service.ensure_running` starts `personal-os-api` detached with
  `start_new_session=True`, so it outlives the CLI invocation and the next command
  reuses it. Logs go to `~/.local/state/personal-os/api.log`.
- `service.ensure_chrome` does the same for the automation browser, so `run` no
  longer depends on `run.sh`. `run.sh` is now a one-line wrapper kept only for the
  PowerShell entry point.
- Measured: cold start is about 4 seconds and prints what it is starting; a warm
  call is about 0.4 seconds and prints nothing.
- Verified from the home directory with nothing running: one `hunt run --once`
  started the API and Chrome and exited cleanly, and `hunt doctor` then reported
  all six checks green, including `Local API: accepted the session`, which is the
  proof that the collapsed worker endpoints take the Google token.

## The `os` entry point and read commands, 2026-08-19

The CLI is now `os`, a single entry point for every local domain, with Jobs under
`os hunt`. `hunt` as a standalone binary is gone.

- `os` was verified free before adopting it: not POSIX, not coreutils, no shell
  builtin, no `/usr/bin/os`, and absent on macOS. Python's `os` is a module, not
  an executable.
- Surface: `status`, `stop [api|chrome|all] [--force]`, `api`, `chrome`, `login`,
  `whoami`, `doctor`, and `hunt {listings,apps,prep,run}`. Domains live in
  `commands/`, so `learning` and `life` can be added without claiming more names
  on PATH.
- Services are tracked in `~/.local/state/personal-os/services/*.json` with pid
  and port, so stopping never has to guess. `--force` additionally resolves
  whatever holds the port, which is what recovers a stale or missing record.
- **Chrome cannot be stopped with a Linux signal.** The pid from `Popen` belongs
  to the WSL interop stub, not to `chrome.exe`, so signalling it leaves the
  browser running. `stop_chrome` asks Windows for `chrome.exe` processes whose
  command line contains the dedicated profile and runs `taskkill /PID /T /F` on
  those. Matching on the profile is what protects the user's own windows;
  `taskkill /IM chrome.exe` would close all of them.
- Verified live: automation Chrome went from 12 processes to 0 while the user's
  own 11 Chrome processes were untouched, and both ports were released.
- Verified live: a cold `os hunt listings` started the API, recorded pid 49212,
  and `os stop api` then released the port with no `--force`.

Read commands, verified against real data:

- `os hunt listings` renders 392 open listings; `--faang` narrows to 105 and
  `--quant` to 39. `--location` is repeatable and maps to the existing query
  parameters, so no backend change was needed.
- `os hunt apps` renders applications with preparation and autofill status.
- Columns are `no_wrap` with ellipsis overflow. Without that, titles wrapped over
  five lines each and the table stopped being scannable.
- Rows are referenced by number or id prefix, cached in
  `~/.local/state/personal-os/last-{listings,apps}.json`. Ambiguous prefixes are
  refused rather than guessed, and every failure prints a sentence rather than a
  traceback.

Worker Ruff, mypy, and all 62 tests pass, including reference resolution and the
Chrome stop path faked against a Windows process query.

`os hunt run` drains the queue and exits, 2026-08-19:

- The old loop polled every five seconds forever, because the producer used to be
  a hosted UI this process could not see. The producer is now `os hunt prep` in
  the same terminal, so finishing the queue and returning the prompt is the
  useful default. This is the same reasoning that removed the leases.
- `--once` stops after a single task and, in `claim_with_backoff`, also declines
  to retry transient API errors. `--watch` restores the long-running poller.
  The two are mutually exclusive.
- `process_task` now returns `done`, `failed`, or `lost` instead of `None`. It
  reports failures to the API rather than raising, so without a return value a
  drain summary would have been guesswork.
- Progress prints per task and ends with a count; a drain with any failure exits
  non-zero so it composes in a shell.
- Verified live: `os hunt run` against an empty queue prints `Queue is empty` and
  exits 0 instead of hanging.

Still to do: `os hunt prep` has not been run against live data, so no new
application has been created through the CLI yet.


## Silent preparation failure, found 2026-08-19

The first live `os hunt prep` plus `os hunt run` reported `1 done, 0 failed`
while accomplishing nothing. Chrome had landed on Zipline's `open-roles` index
rather than the posting, so there was no application to fill in.

Root cause: `capture_application` returned whatever page it reached and nothing
checked it. A careers index answers 200 and yields plenty of body text, so it is
indistinguishable from a real posting. Tailoring then ran against the roles list
as if it were a job description, the resume compiled, the PDF uploaded, and the
task completed. Every step succeeded; the outcome was worthless.

Fixed now: `looks_like_application_form` requires either a file input or at least
three identifying contact inputs, and preparation raises
`ApplicationFormNotFound` otherwise. A wrong link now fails loudly, records
`preparation_error`, and leaves the page open in Chrome. The stored URL for that
listing was `zipline.com/open-roles?gh_jid=7886501003`; the `gh_jid` deep link did
not resolve to the posting even though both matching roles are visible on the
page.

Still to design, in the user's order of preference:

1. When no form is found, search the careers page for the role title before
   giving up. Zipline's own search finds both `Enterprise Systems Software
   Engineer Intern` postings for Spring and Summer 2027.
2. When the search returns several plausible postings, ask which to apply to
   rather than guessing. Two variants of one role is the normal case, not an edge
   case.
3. When the search finds nothing, stop and hand the task back with the page open,
   rather than reporting success or failing silently.

An agentic fallback that drives the page directly was raised as the mechanism for
step 1. That is a design decision, not a detail: it widens what the runner is
allowed to do on a company site, so it needs its own brainstorm before any code.

Also fixed: tables set `expand=True` with a flexible title column. Fixed
`max_width` values truncated titles even on a wide terminal.

## Autofill isolated and tested, 2026-08-19

`os hunt autofill [--url URL] [--dry-run] [--resume PDF]` exercises filling on its
own. With no `--url` it uses the tab already in the foreground, so a form reached
by hand can be tested. `--dry-run` prints the plan without touching the page.

Autofill never used the active tab, contrary to expectation: it always called
`new_page()` and navigated to a stored URL, so it could not be pointed at
anything. `autofill_application` is now split into `plan_autofill`, which only
inspects, and `apply_autofill`, which mutates. The task path composes the two, so
both paths share one implementation.

Three defects surfaced immediately, all invisible before isolation:

- **Autofill had no form guard.** The Bestow application read
  `autofill_status=completed`, but the Ashby posting exposes zero controls, so it
  filled nothing and reported success. Same silent-success class as the Zipline
  preparation bug, in the path that guard did not cover.
- **Ashby renders the posting and the application as separate views.** The job
  page has no controls after 13 seconds; waiting never helps. The form is behind
  an Apply step, and the same page yields 46 controls once it is requested.
  `reveal_application_form` now clicks an Apply link or button and waits for
  controls, and runs in both capture and autofill.
- **Two profile labels were unmapped.** Ashby labels the fields exactly `Name` and
  `Address Line 1`; `PROFILE_LABELS` had `full name` and `address` but neither of
  those. Both were reported as unanswered required questions. Matching is an exact
  lookup on the normalized label, so adding them cannot make `Company Name` match.

The Bestow form now plans 7 fills with 0 unanswered required questions, up from 5
fills with 2 unanswered. Applying to a real form has not been run yet; only the
dry run has.

`www.zipline.com/open-roles?gh_jid=7886501003` remains unresolvable. The
equivalent Greenhouse URL redirects to `?error=true`, so the stored link is dead
rather than merely indirect.
