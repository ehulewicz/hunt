# Worker current state

Last verified: 2026-09-12

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
- [x] Verify the reference and generated PDFs embed the same
  TeXGyreTermesX 2.004 regular, bold, and italic outlines at the same effective
  25pt/12pt/11pt/10pt hierarchy, and pin that stack with a regression test.
- [x] Keep bullet spacing outside the text run so full-width bullets cannot
  create phantom blank lines, and give company-to-first-bullet transitions one
  additional point of optical space.
- [x] Rebuild the generator directly on the supplied Sam Kim LaTeX source and
  retain `bin/sam-kim.json` as the reference fixture, with only the documented
  global spacing corrections as intentional layout differences.
- [x] Retire resume tailoring: `prep --tailor`, the Claude tailor module, and the
  resume guard are gone, and preparation always compiles the canonical resume.
- [x] Run saved Greenhouse, Ashby, and Lever listings synchronously in headed
  Windows Chrome with exact non-submission navigation and no submit actions.
- [x] Manage the hosted reusable answer library with CLI list/add/edit commands.
- [x] Draft every long-form textarea in its own VS Code tab, seeded from an exact
  library answer or a local Claude draft with similar answers as reference, and
  fill the textarea when the tab closes.
- [x] Remove every terminal prompt from `os hunt autofill`: the worker polls the
  page, reports required fields left for the user, follows one safe Next, waits
  for the user's Submit, and saves every visible answer for reuse.
- [x] Reuse saved answers for selects, comboboxes, and radio groups; only
  consent, attestation, and signature labels stay unfilled.
- [x] Handle live Greenhouse job-board markup: trailing-asterisk labels, hidden
  react-select mirror inputs that carry `required`, and resume uploads whose
  only hint is the `resume` id.
- [x] Add deduplicated failure capture plus list/show/diagnose/draft/apply/verify commands.
- [x] Render `**keyword**` bullet emphasis as a dedicated sans type and omit Skills when empty.
- [x] Pass Ruff, strict mypy, and 128 tests with no skips, including the
  headless ATS fixtures and an end-to-end session test that closes a fake tab,
  fills the textarea, snapshots a manual select, and ends on a confirmation page.
- [x] Compile the real canonical resume to one page and open its live Windows Chrome preview.
- [x] Persist a real 415-row hosted sync and verify 320 technical-default rows.
- [x] Live-test `--limit 500` returning all 323 current technical rows.
- [x] Dry-run the real Vercel Greenhouse listing: 28 controls, 8 planned from
  the profile, 14 required left for the user.
- [ ] Complete one real Greenhouse application end to end through VS Code tabs
  (Vercel rows 133/137, Viam 134, Scale AI 205, Schonfeld 201 are the
  candidates), then Ashby and Lever, and confirm the saved answers fill the
  next form.
- [ ] Add Workday only after all three v1 adapters pass live acceptance.

The apply loop lives in `autofill_session.py`. `commands/hunt.py` still owns run
creation and the company/role/closed-posting checks, then hands the page to the
session. Draft files land in `~/.local/state/personal-os/drafts/<run>/step-N/`.
Ctrl+C during a run completes it with whatever answers were on the page rather
than abandoning it. The headless Playwright shell had to be installed on this
machine (`uv run playwright install chromium-headless-shell`) for the fixture
tests to stop skipping. A local Supabase Docker stack was holding port 54321,
which the `os login` callback needs; it was stopped and must not be started here.

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
is `artifacts/resume/Alex_Example.pdf`. The pre-redesign comparison is retained as
`artifacts/resume/old.pdf`. Invalid JSON, LaTeX errors, or a two-page result
leave the previous PDF untouched. Northwind and Example Public Schools have
written bullets; unfinished entries retain visible blank slots.

The active resume carries no bullet-level keyword emphasis. `skills` remains a
required and populated list and renders as Technical Skills. The parser retains
legacy `**...**` compatibility for older drafts, and escaping still runs first
so authored text cannot inject LaTeX. En dashes in heading dates normalize to
TeX range ligatures.

The hosted answer library currently has three approved rows: current US work
authorization, salary deflection without an invented range, and career goals.
Archived STEM OPT wording was deliberately rejected as stale. Every completed
run now adds to it, including short and choice answers, so a wrong one-off
answer propagates until `os hunt answers edit` corrects it.

The worker no longer owns a venv or a lock. It is a member of the uv workspace
declared by the root `pyproject.toml`, resolved through the root `uv.lock` and
installed into the root `.venv`. `uv sync --all-packages --python 3.13` from the
repository root builds that one environment, and `source .venv/bin/activate`
puts the `os` script on `PATH` alongside the backend's `personal-os-api`.
Activating `backend/.venv` and then running `os` previously failed with
`os: command not found`; there is now only one environment to activate. Running
`uv run ruff check .`, `uv run mypy`, and `uv run pytest` from `workers/` still
uses this package's own tool configuration. Ruff, strict mypy over 21 source
files, and 124 tests pass on the shared environment with no skips, so the
earlier restricted-sandbox skips do not apply here.

The resume compiler needs `newtx` from the system TeX tree, which this machine
was missing. `texlive-fonts-extra` is now installed and `newtxtext.sty` and
`newtxmath.sty` resolve, so `os hunt resume build` succeeds again. Rebuilding
the canonical resume on this toolchain reproduces the committed PDF with
identical extracted text and a single line of Experience bullet text differing
only in inter-word spacing, which is a newtx version difference from the machine
that produced the previous PDF rather than a content change.

WSL networking on this machine was `nat` with no `.wslconfig`, which is why CDP
at `127.0.0.1:9333` was unreachable and Chrome-opening commands failed.
`C:\Users\eahtr\.wslconfig` now sets `networkingMode=mirrored`. It does not take
effect until `wsl --shutdown` runs from PowerShell, which had not been done when
this was written, so the mirrored-mode fix is recorded but not yet verified.
