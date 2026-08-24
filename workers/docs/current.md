# Worker current state

Last verified: 2026-08-23

- [x] Use `os hunt` as the local Jobs command surface.
- [x] Authenticate through Google PKCE and keep FastAPI as the only database client.
- [x] Drive a dedicated native Windows Chrome profile over CDP from WSL.
- [x] Deterministically sync the full approved listing feeds.
- [x] Default listing display to technical role families while retaining `--all-roles`.
- [x] Keep listing limits unbounded above; reject only values below 1.
- [x] Make displayed listing titles clickable and add `os hunt open <row>` for
  direct application URLs, with `--source` for discovery URLs.
- [x] Build and watch the canonical JSON resume with atomic one-page PDF output.
- [x] Make canonical unchanged preparation the default and Claude tailoring explicit.
- [x] Run saved Greenhouse, Ashby, and Lever listings synchronously in headed
  Windows Chrome with exact non-submission navigation and no submit actions.
- [x] Manage the hosted reusable answer library with CLI list/add/edit commands.
- [x] Add exact/contextual answer reuse, local-Claude 20 percent validation, and
  explicit final webpage-answer approval.
- [x] Add deduplicated failure capture plus list/show/diagnose/draft/apply/verify commands.
- [x] Render `**keyword**` bullet emphasis as a dedicated sans type and omit Skills when empty.
- [x] Pass Ruff, strict mypy, and 108 ordinary tests; one loopback bind test skips
  in sandbox. Pass five headed synthetic ATS fixtures outside the restricted sandbox.
- [x] Compile the real canonical resume to one page and open its live Windows Chrome preview.
- [x] Persist a real 415-row hosted sync and verify 320 technical-default rows.
- [x] Live-test `--limit 500` returning all 323 current technical rows.
- [ ] Run one real saved listing on each supported ATS and the controlled hosted
  failure lifecycle after the new migration is deployed.
- [ ] Add Workday only after all three v1 adapters pass live acceptance.

API HTTP failures now exit with the response status, request path, and FastAPI
detail instead of exposing an `httpx` traceback. A create-run smoke test succeeds
for listing 12, but that Availity listing resolves to the unsupported generic
adapter rather than Greenhouse, Ashby, or Lever. The exact dry-run command now
stops cleanly without touching the form and records open failure `aa3d51b9`.

Ashby job-description pages are now normal session states. The worker follows a
single exact Apply link/button, then recognizes the form. After field review it
may follow one exact Next/Continue control. A missing, ambiguous, captcha-adjacent,
or blocked transition prints `Hit a wall`, records the occurrence once, and
continues waiting for manual browser navigation. A real H3X dry run reached 16
controls and planned 5 fills without changing the page; the direct row-opening
command also opened H3X in native Windows Chrome.

The canonical resume is `artifacts/resume/main.json`; the tracked PDF beside it
is `artifacts/resume/main.pdf`. Invalid JSON, LaTeX errors, or a two-page result
leave the previous PDF untouched.

Bullet text may wrap keywords in `**...**`. They render through `\resumeKeyword`,
a sans-serif size-matched type kept deliberately lighter than the bold entry
headings so keywords read as texture rather than competing with the hierarchy.
Escaping still runs first, so authored text cannot inject LaTeX, and an unpaired
marker stays literal instead of failing the build. Heading dates render in
typewriter, where en dashes are normalized to hyphens because that face carries
no en-dash ligature. `skills` remains a required list; an empty
one now emits no Skills section, which is how the canonical resume carries its
keywords inside bullets instead.

The hosted answer library currently has three approved rows: current US work
authorization, salary deflection without an invented range, and career goals.
Archived STEM OPT wording was deliberately rejected as stale.
