# Resume writing pass — working context

Handoff for continuing the base-resume writing pass on another machine. Pairs
with `defense.md` (interview exposure per entry) and `main.json` (canonical
content). Update this file as decisions land.

Last verified: 2026-09-10, against commit `5b40cb6`.

## State

`main.json` currently has **every bullet blank**. `5b40cb6` rebuilt the layout on
the Sam Kim reference style and cleared all bullet text for a fresh writing pass.
The previous rendered resume is preserved at `artifacts/resume/old.pdf`.

Structure is now five sections: Education, Experience, **Research**, Projects,
Technical Skills. DREAM and iFAN moved out of Experience into Research. Chicago
Public Schools stays in Experience.

Current empty slot counts:

| Section | Entry | Slots |
|---|---|---|
| Experience | Northwind | 4 |
| Experience | Northhealth AI | 3 |
| Experience | Example Public Schools | 3 |
| Research | Sample Lab @ GT Europe | 3 |
| Research | Example Lab @ GT | 3 |
| Projects | AlphaResearch | 3 |
| Projects | Personal Dynamics Engine | 3 |

These slot counts are placeholders, not a decision. See open questions.

## Hard constraints

- **One page is enforced at build time.** `MAXIMUM_PAGES = 1` in
  `workers/src/personal_os_worker/resume_pdf.py:103`. Exceeding it raises
  `Resume must stay within 1 page(s)` and the build fails. This is a gate, not a
  warning, so the writing pass is budget-constrained from the first bullet.
- No semicolons in bullets. Never exceed two rendered lines.
- No keyword bolding inside bullets. Skills section is the only place
  technologies are listed.
- Entry headings separate role from organization with `|`. A comma introduces a
  campus, city, or award qualifier. Never an em dash.
- Do not restate a point another bullet in the same entry already makes. Prefer
  the wording that shows ownership.
- `main.json` is the only canonical resume file. `main.pdf` is tracked beside it.

## Line budget

A bullet line held **~126 characters** of body text, so a guaranteed one-liner
was <=118 and anything past ~126 wrapped.

**Caveat: that was measured on the retired layout** (mathptmx, pre-`5b40cb6`).
`5b40cb6` switched to TeX Gyre Termes with half-inch side margins, so the figure
needs re-measuring once the first real bullets render. Method:

```bash
pdftotext -layout main.pdf - | grep "•" | awk '{s=substr($0,index($0,"•")); print length(s)}' | sort -rn | head
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
3. **Slot allocation is unresolved.** The user's instinct was 4/3/2/2/3 across
   the old flat Experience list. The counter-proposal was 4/3/2/2/2, on the
   grounds that CPS is pre-college and the oldest item, so giving it three while
   GT research gets two says it outranks the research. That discussion predates
   the Research-section split in `5b40cb6`, so it needs redoing against the new
   structure — research entries now sit in their own section where 2 vs 3 reads
   differently.
4. **Personal Dynamics Engine has no written content at all** and is the least
   defined entry on the page.

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

1. Answer open questions 1 and 2.
2. Write the four Northwind bullets. Build and confirm the page gate passes.
3. Re-measure the per-line character budget on the new Termes layout and record
   it in this file, replacing the stale ~126 figure.
4. Settle slot allocation against the new five-section structure.
5. Work down the remaining entries. Keep `defense.md` in sync as bullets change.

## Commands

```bash
os hunt resume build      # compile main.json -> main.pdf, enforces the 1-page gate
os hunt resume watch      # open main.json in VS Code, rebuild on change
pdfinfo artifacts/resume/main.pdf | grep Pages
pdftotext -layout artifacts/resume/main.pdf -   # what an ATS parser receives
```
