# Worker current state

Last verified: 2026-08-22

- [x] Use `os hunt` as the local Jobs command surface.
- [x] Authenticate through Google PKCE and keep FastAPI as the only database client.
- [x] Drive a dedicated native Windows Chrome profile over CDP from WSL.
- [x] Deterministically sync the full approved listing feeds.
- [x] Default listing display to technical role families while retaining `--all-roles`.
- [x] Support listing limits through 300 and reject larger CLI values cleanly.
- [x] Build and watch the canonical JSON resume with atomic one-page PDF output.
- [x] Make canonical unchanged preparation the default and Claude tailoring explicit.
- [x] Keep deterministic no-submit autofill as the default.
- [x] Manage the hosted reusable answer library with CLI list/add/edit commands.
- [x] Add an optional user-triggered Lentra handoff followed by value-free Playwright audit.
- [x] Pass Ruff, strict mypy, and 88 tests; one loopback bind test skips in sandbox.
- [x] Compile the real canonical resume to one page and open its live Windows Chrome preview.
- [x] Persist a real 415-row hosted sync and verify 320 technical-default rows.
- [x] Live-test `--limit 300` against 323 current technical rows.
- [ ] Exercise the Lentra handoff with the extension installed on a real application.

The canonical resume is `backend/src/personal_os/main_resume.json`; the stable
local PDF defaults to `~/.local/state/personal-os/resume/main.pdf`. Invalid JSON,
LaTeX errors, or a two-page result leave the previous PDF untouched.

The hosted answer library currently has three approved rows: current US work
authorization, salary deflection without an invented range, and career goals.
Archived STEM OPT wording was deliberately rejected as stale.
