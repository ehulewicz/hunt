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

Still to do: the read commands (`listings`, `apps`) are not built yet.
