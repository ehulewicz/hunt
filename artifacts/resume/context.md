# Resume writing pass — working context

Handoff for continuing the base-resume writing pass on another machine. Pairs
with `defense.md` (interview exposure per entry) and `main.json` (canonical
content). Update this file as decisions land.

Last verified: 2026-09-11, after the exact-width bullet spacing fix.

## State

`main.json` now has written Northwind and Example Public Schools bullets. The
other tracked entries retain blank slots for the ongoing writing pass. The
previous rendered resume is preserved at `artifacts/resume/old.pdf`.

The generator started from the supplied Sam Kim LaTeX source directly rather
than a layout reverse-engineered from the PDF. `bin/sam-kim.json` remains the
typography and structure regression fixture. It now differs intentionally at
the global bullet and company-to-first-bullet spacing points documented below.
Compare with:

```bash
os hunt resume build --input bin/sam-kim.json --output bin/sam-kim.pdf
```

Font inspection on 2026-09-11 confirmed that both PDFs embed
TeXGyreTermesX Regular, Bold, and Italic version 2.004 with the same effective
25pt name, 12pt section, 11pt heading, and 10pt body hierarchy. The same
regular-weight word rasterized from both files at 600 DPI produced identical
pixels and ink density. Apparent weight differences in a viewer are therefore
rendering scale/antialiasing or page-density effects, not a template font
mismatch; do not substitute another Times clone to compensate.

Structure is Education, Experience, **Research**, Projects, Leadership, Technical
Skills, in that fixed order, and a section with no entries emits neither label
nor rule. The canonical resume has Research and no Leadership; the reference
fixture is the other way round. DREAM and iFAN moved out of Experience into
Research. Example Public Schools stays in Experience.

Bullet allocation is **settled**: every entry gets 3 bullets, except Northwind
which gets 4 because it is the frontline experience.

| Section | Entry | Bullets | Written |
|---|---|---|---|
| Experience | Northwind | 4 | yes |
| Experience | Northhealth AI | 3 | no |
| Experience | Example Public Schools | 3 | yes |
| Research | Sample Lab @ GT Europe | 3 | no |
| Research | Example Lab @ GT | 3 | no |
| Projects | AlphaResearch | 3 | no |
| Projects | Personal Dynamics Engine | 3 | no |

## Hard constraints

- **One page is enforced at build time.** `MAXIMUM_PAGES = 1` in
  `workers/src/personal_os_worker/resume_pdf.py`. Exceeding it raises
  `Resume must stay within 1 page(s)` and the build fails. This is a gate, not a
  warning, so the writing pass is budget-constrained from the first bullet.
- **Every bullet is exactly one rendered line.** This is the operating rule for
  the whole page, stricter than the two-line ceiling in `AGENTS.md`, and it is
  what makes 22 bullets fit at all. Write to roughly 122 characters and verify
  against the rendered PDF, never against the character count alone.
- No semicolons in bullets.
- No keyword bolding inside bullets. Skills section is the only place
  technologies are listed.
- Entry headings separate role from organization with `|`. A comma introduces a
  campus, city, or award qualifier. Never an em dash.
- Do not restate a point another bullet in the same entry already makes. Prefer
  the wording that shows ownership.
- `main.json` is the only canonical resume file. `main.pdf` is tracked beside it.

## Line budget

Re-measured on the 11pt newtx layout (2026-09-10), by binary search against the
count of distinct rendered text baselines:

- A bullet's first line holds **125 characters** of ordinary lowercase prose.
- Treat **<=122 characters as a safe one-liner.**
- Character count is only a proxy. Capitals, digits, and wide letters cost more
  than average lowercase, so number-heavy bullets wrap earlier. The reference's
  own longest bullet measures 126 characters and still sets on one line, because
  its letter mix is narrower than the proxy sentence used to find the limit.

Re-measure after any layout change:

```bash
pdftotext -layout artifacts/resume/main.pdf - | grep "•" \
  | awk '{s=substr($0,index($0,"•")); print length(s)}' | sort -rn | head
```

**Page budget is tight.** Filling all 22 bullet slots with two-line bullets
compiles to 2 pages and fails the gate. Measured on 2026-09-11 with only
Northwind written, Example Public Schools could fit just **two** rendered lines
before the page broke, which is what forced the one-line-per-bullet rule. That
measurement predates the formatting work in progress on the template, so
re-measure before treating it as the current ceiling.

Verify wrapping by reading the rendered text, not by trusting character counts:

```bash
pdftotext -layout artifacts/resume/main.pdf - | sed -n '/Example Public Schools/,/Research/p'
```

A wrapped bullet shows a continuation line with no bullet marker.

**Blank slots already occupy their space.** An empty bullet still emits an
`\item` and renders as a bare marker, so the current one-page build is already
carrying all 22 bullet lines. Filling a blank slot therefore costs no additional
vertical space, provided the text stays on one line. Confirmed 2026-09-11:

```bash
pdftotext -layout artifacts/resume/main.pdf - | grep -c "•"   # 22
```

## Decisions from the 2026-09-09 session

- **Bullet counts should not be uniform.** Nothing in ATS parsing counts
  bullets. Uniform counts flatten relative weight and make short research stints
  read as equal to the longest role. Variable counts are correct.
- **Contact row is fine as-is.** The "don't put contacts in a row" advice is a
  garbled version of the real rule: don't put contact info in the document
  *header/footer* region, which older parsers (Taleo especially) skip entirely.
  The other genuine failure is contact info inside a table cell, text box, or
  multi-column layout. The current line is centered body text, pipe-separated,
  and extracts cleanly with all five fields intact and in order. No change
  needed. `\input{glyphtounicode}` + `\pdfgentounicode=1` in the preamble is
  what makes extraction work at all — do not remove.
- **The "4 Statements of Work" claim was wrong.** It was carried over from
  ChatGPT output. Truth: **2** customer SOWs, both led, both brought nearly end
  to end, ~93% of the code written solo. The old bullets 1 and 2 were built on a
  contrast (*led four, personally built two*) that no longer exists, so
  Northwind needs re-architecting, not a find-and-replace.
- **Drop the 93% metric from the page.** Bad provenance (93% of what — lines,
  commits, files?), and a number that precise is implicitly a claim about what
  teammates did not do. "Sole engineer" is a scope claim rather than a
  measurement, so it cannot be litigated, and it is stronger.

- **Entry spacing, section-rule geometry, and the Technical Skills spacer start
  from the supplied Sam Kim template rather than per-entry corrections.** Two
  global optical corrections are now deliberate: `\resumeItem` applies its
  `\vspace{-2pt}` after the text group so an exactly full line cannot wrap that
  invisible command onto a phantom baseline, and company headings use
  `\vspace{-6pt}` instead of `-7pt` to open the first-bullet gap by 1pt. The
  rule-to-role gap intentionally remains about 1pt larger than the other
  transitions because the hairline needs optical separation. Do not add
  per-entry spacing exceptions. If spacing looks wrong, rebuild
  `bin/sam-kim.json` and compare against the reference before changing the
  global template:

  ```bash
  os hunt resume build --input bin/sam-kim.json --output bin/sam-kim.pdf
  compare -metric AE -fuzz 5% <(pdftoppm -r 150 -png -gray bin/sam-kim.pdf) ...
  ```

  Two departures from the supplied source are deliberate and documented in
  `resume_pdf.py`: `\PassOptionsToPackage` for `color` (newtx loads it first, so
  a later `\usepackage[usenames,dvipsnames]{color}` raises an option clash), and
  a `\resumeEducation` that takes a prebuilt row block instead of a hard-coded
  pair of degree rows, so one degree renders as well as two.

## Northwind plan

What it needs to convey, in the user's priority order: team lead / project
ownership, end-to-end full-stack build, sheer volume of work, larger projects.

| # | Carries | Lines |
|---|---|---|
| 1 | Led 2 SOWs — customer scoping, cross-team coordination | 1 (hard) |
| 2 | End-to-end build: schema -> API -> business logic -> frontend -> prod | 2 |
| 3 | Volume: 120+ tickets, and the stack | 2 |
| 4 | The larger projects | 2 |

Bullet 1 takes the outward half (customer-facing, coordination), bullet 2 the
inward half (personally built every layer), so they do not restate each other.

Bullet 1 candidates, with character counts against the **old** budget:

```
114  Owned 2 customer Statements of Work end to end, from scoping requirements
     with the customer to production release.
119  Led 2 customer Statements of Work, scoping requirements with the customer
     and coordinating delivery across three teams.
119  Sole engineer on 2 customer Statements of Work, from requirements scoping
     with the customer through production release.
125  Led 2 customer Statements of Work end to end, scoping requirements
     directly with customers and driving delivery across teams.   <- risky
```

## Example Public Schools plan

**Written on 2026-09-11.** Three one-line bullets, verified unwrapped at one
page. Full interview material is in `defense.md`.

| # | Carries | Lines |
|---|---|---|
| 1 | What it is and its impact: automated the deterministic majority of leave cases across 9 HR and payroll systems, 10 minutes to 3 | 1 |
| 2 | The engineering underneath: unified data model and adapter layer over inconsistent schemas, browser automation where no API existed | 1 |
| 3 | Sustained ownership after launch: integrations rebuilt as upstream systems changed, plus the background-login feature request | 1 |

Two corrections drove this entry and must not be undone:

- **The users were not engineers.** They were the district's Leave Specialists,
  an HR, payroll, and timekeeping function. The resume signal is shipping an
  internal tool to a non-technical operations team in a rule-heavy domain.
- **The tool did automate a large deterministic share of cases outright.** It was
  not merely decision support. The hard cases still required a specialist, and
  for those it consolidated the nine systems into one interface. State the
  automation first and the support second.

Dates are resolved: May to December 2023, development complete in August, four
months of maintenance after that. The earlier May 2023 to December 2024 range in
older notes is wrong.

## Open questions — these block the writing pass

1. **What did the other Northwind teams actually do?** "Coordinated execution
   across frontend, database, and middleware teams" and "wrote ~93% of the code
   myself" pull against each other, and `defense.md` already flags that *led*
   will draw scrutiny. Bullet 1 has to pick a lane: if the other teams had
   nameable deliverables that were sequenced against, the coordination framing
   is honest and keeps the team-lead signal; if it was really solo plus review,
   "sole engineer on 2 customer SOWs" is stronger and the leadership angle has
   to be recovered in bullet 4.
2. **What are the "larger projects"?** Bullet 4 depends on them. The only
   recorded detail is `defense.md` calling the AJAX transition "the ownership
   story" — behavior parity, legacy edge cases, no downtime. Still needed: what
   it replaced, roughly how much surface it touched, and what got better.
3. **What is the Example Public Schools title and employment relationship?** The
   work was done as a subcontractor under a SWE contractor, not as a direct CPS
   employee, so the current "Software Engineer (Contract) | Chicago Public
   Schools" heading implies a relationship that did not exist. The contracting
   entity name is not recorded. See `defense.md`.
4. **Is there any handle on the Example Public Schools error reduction?** The
   engagement was for speed and error reduction, and only speed has a number.
   The error claim is currently unquantified and off the page entirely.
5. **Personal Dynamics Engine has no written content at all** and is the least
   defined entry on the page.

Bullet allocation is no longer an open question. It is settled at 3 per entry,
4 for Northwind, one rendered line each.

## Recovering the old bullet text

All pre-`5b40cb6` bullet text is in git, not lost:

```bash
git show 38edffe:artifacts/resume/main.json          # last version with full bullets
git show 38edffe:artifacts/resume/main.json | python3 -m json.tool
```

`38edffe` still contains the incorrect "Led 4 customer Statements of Work"
wording and three `TODO: write this bullet.` placeholders. Treat it as raw
material for phrasing and recoverable facts, never as correct copy.

## Next actions

1. Answer open questions 1 and 2 and revisit the Northwind bullets. They are
   written but not yet satisfying, and the leadership-versus-solo seam in bullet
   4 is still unresolved.
2. Write Northhealth AI. `defense.md` holds recoverable material from `38edffe`.
3. Work down the research entries, then the projects. Personal Dynamics Engine
   is last because it is the least defined.
4. Keep `defense.md` in sync as bullets change. It is the only place the detail
   a one-line bullet sheds is preserved.

## Commands

```bash
os hunt resume build      # compile main.json -> main.pdf, enforces the 1-page gate
os hunt resume watch      # open main.json in VS Code, rebuild on change

# Sam Kim reference fixture: the regression target for template changes.
os hunt resume build --input bin/sam-kim.json --output bin/sam-kim.pdf
os hunt resume watch --input bin/sam-kim.json --output bin/sam-kim.pdf --open
pdfinfo artifacts/resume/main.pdf | grep Pages
pdftotext -layout artifacts/resume/main.pdf -   # what an ATS parser receives
```
