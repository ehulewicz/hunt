# Worker current state

Last verified: 2026-09-09

- [x] Use `os hunt` as the local Jobs command surface.
- [x] Authenticate through Google PKCE and keep FastAPI as the only database client.
- [x] Drive a dedicated native Windows Chrome profile over CDP from WSL.
- [x] Deterministically sync the full approved listing feeds.
- [x] Default listing display to technical role families while retaining `--all-roles`.
- [x] Keep listing limits unbounded above; reject only values below 1.
- [x] Make displayed listing titles clickable and add `os hunt open <row>` for
  direct application URLs, with `--source` for discovery URLs.
- [x] Build and watch the canonical JSON resume with atomic one-page PDF output.
- [x] Match the Sam Kim reference layout with 11pt newtx Times, role-first
  headings, small-caps section rules, and a separate Research section.
- [x] Rebuild the generator directly on the supplied Sam Kim LaTeX source, and
  verify it against `bin/sam-kim.json` at zero rendered pixel difference.
- [x] Support four blank bullet slots for Northwind and three for every other
  entry while preserving variable counts through validation and tailoring.
- [x] Make canonical unchanged preparation the default and Claude tailoring explicit.
- [x] Run saved Greenhouse, Ashby, and Lever listings synchronously in headed
  Windows Chrome with exact non-submission navigation and no submit actions.
- [x] Manage the hosted reusable answer library with CLI list/add/edit commands.
- [x] Add exact/contextual answer reuse, local-Claude 20 percent validation, and
  explicit final webpage-answer approval.
- [x] Add deduplicated failure capture plus list/show/diagnose/draft/apply/verify commands.
- [x] Render `**keyword**` bullet emphasis as a dedicated sans type and omit Skills when empty.
- [x] Pass Ruff, strict mypy, and 114 tests; one loopback bind test and five
  headed browser tests skip in the restricted sandbox. Pass the five headed
  synthetic ATS fixtures outside the restricted sandbox.
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
is `artifacts/resume/main.pdf`. The pre-redesign comparison is retained as
`artifacts/resume/old.pdf`. Invalid JSON, LaTeX errors, or a two-page result
leave the previous PDF untouched. The current JSON intentionally has blank
bullet text while retaining visible slots: four for Northwind and three for
each other Experience, Research, or Project entry.

The active resume carries no bullet-level keyword emphasis. `skills` remains a
required and populated list and renders as Technical Skills. The parser retains
legacy `**...**` compatibility for older drafts, and escaping still runs first
so authored text cannot inject LaTeX. En dashes in heading dates normalize to
TeX range ligatures.

The hosted answer library currently has three approved rows: current US work
authorization, salary deflection without an invented range, and career goals.
Archived STEM OPT wording was deliberately rejected as stale.
