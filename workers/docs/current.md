# Worker current state

Last verified: 2026-08-23

- [x] Use `os hunt` as the local Jobs command surface.
- [x] Authenticate through Google PKCE and keep FastAPI as the only database client.
- [x] Drive a dedicated native Windows Chrome profile over CDP from WSL.
- [x] Deterministically sync the full approved listing feeds.
- [x] Default listing display to technical role families while retaining `--all-roles`.
- [x] Keep listing limits unbounded above; reject only values below 1.
- [x] Build and watch the canonical JSON resume with atomic one-page PDF output.
- [x] Make canonical unchanged preparation the default and Claude tailoring explicit.
- [x] Keep deterministic no-submit autofill as the default.
- [x] Manage the hosted reusable answer library with CLI list/add/edit commands.
- [x] Add an optional user-triggered Lentra handoff followed by value-free Playwright audit.
- [x] Render `**bold**` keywords in resume bullets and omit Skills when the list is empty.
- [x] Pass Ruff, strict mypy, and 95 tests; one loopback bind test skips in sandbox.
- [x] Compile the real canonical resume to one page and open its live Windows Chrome preview.
- [x] Persist a real 415-row hosted sync and verify 320 technical-default rows.
- [x] Live-test `--limit 500` returning all 323 current technical rows.
- [ ] Exercise the Lentra handoff with the extension installed on a real application.

The canonical resume is `artifacts/resume/main.json`; the tracked PDF beside it
is `artifacts/resume/main.pdf`. Invalid JSON, LaTeX errors, or a two-page result
leave the previous PDF untouched.

Bullet text may wrap keywords in `**...**` to bold them. Escaping still runs
first, so authored text cannot inject LaTeX, and an unpaired marker stays
literal instead of failing the build. `skills` remains a required list; an empty
one now emits no Skills section, which is how the canonical resume carries its
keywords inside bullets instead.

The hosted answer library currently has three approved rows: current US work
authorization, salary deflection without an invented range, and career goals.
Archived STEM OPT wording was deliberately rejected as stale.
