# Jobs CLI

All commands run from any directory after the editable worker install.

## Deterministic commands

```bash
os doctor
os hunt sync
os hunt              # = os hunt list
os hunt 20           # = os hunt list 20
os hunt today
os hunt --ats greenhouse         # one form type: greenhouse | ashby | lever | workday | generic
os hunt 50 --ats workday
os hunt today --ats greenhouse
os hunt list 500  # overshooting returns every available match
os hunt list --family software_engineering --location "Atlanta, GA"
os hunt list --all-roles
os hunt open 12
os hunt open 12 --source
os hunt apps
os hunt inbox        # accept or reject what was learned; bind questions to facts
os hunt patterns     # what fills deterministically (active) and what waits (candidate)
os hunt patterns --all
os hunt prose        # saved long-form writing
os hunt prose add --question "Why this company?" --file ./answer.md
os hunt prose approve 2
os hunt archive      # the old answer library, for the facts session only
os hunt archive rules
os hunt archive prose --markdown ~/archived-answers.md
os hunt facts        # = os hunt facts list; stored and derived facts, sensitive values masked
os hunt facts list --show
os hunt facts review # accept, edit, or delete each unverified fact
os hunt facts set education.gpa 3.78
os hunt facts set work_auth.security_clearance no --type boolean --label "Holds a security clearance"

os hunt resume build      # republish main.json and the PDF from the career record
os hunt resume       # = os hunt resume edit --open
os hunt resume import     # once: load main.json into an empty career record

os hunt queue
os hunt queue add 3 7
os hunt queue empty
os hunt apps
os hunt apps 1 oa
os hunt skipped               # listings passed over with Ctrl+D
os hunt skipped restore 2     # back on the board

os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply                     # top board row, on any site
os hunt apply --ats greenhouse    # top Greenhouse row
os hunt apply today               # first of `os hunt today`, on any site
os hunt apply today --ats greenhouse
os hunt apply 1 --no-claude
os hunt apply 1 --explain    # print every control's whole decision as it fills
os hunt apply 1 --step       # the same, pausing before each control is filled
os hunt apply 1 --dry-run --explain   # every decision, nothing touched
os hunt apply 1 --done       # applied outside the worker; just record it

os hunt apply 1 --repair      # retry failed fields in place, on the page you are filling
os hunt repair                # retry queued failures from runs without --repair
os hunt validate              # watch each repaired rule fill, then accept or reject it
os hunt apply failures list
os hunt apply failures show 1
os hunt apply failures diagnose 1
os hunt apply failures draft-fix 1
os hunt apply failures apply-fix 1
os hunt apply failures verify 1 7
```

Displayed listing titles are clickable when the terminal supports hyperlinks.
A listing's `#` is assigned by the database over one canonical board. Listings
tagged `data_science` are excluded before every other eligible open listing is
numbered, technical families first and newest first, so a row has the same
number in `os hunt`, `os hunt 9`, `os hunt today`, and every filtered view
(filters leave gaps rather than renumbering). Listings you have applied to are
not shown; a queued one carries a `q`. Nothing is cached on
the machine: `open`, `queue add`, and `apply` ask the API to resolve a number
or an id prefix at the moment you run them, and `apps`, `prose`, and
`apply failures …` count rows in the list they just fetched.
`os hunt open <row>` opens the direct company/ATS URL without creating an
application; `--source` opens the discovery URL.
`queue add` puts listings in the queue; `queue empty` runs `apply` through it in
order, one interactive session at a time, and stops after any that is not
submitted. `apps` lists everything past the queue with its lifecycle status
(`applied`, `oa`, `oa_completed`, `interview`, `offer`, `rejected`); `apps
<row> <status>` moves one along.

## What the system learns

Since 2026-10-14 the system learns answers from scratch (design §0). The old
answer library, profile, and rules are the **archive**: frozen, readable with
`os hunt archive`, and used only to get the facts right in the facts session,
after which it is dropped. No application reads it, not even as a suggestion.
A fill the model supplies from a fact records a `candidate` binding from that
question to that fact.

Every site is filled the same way, from its controls: there are no per-site
adapters, and a pattern or strategy learned on one board applies to the same
question or control on any other. `--ats` and a run's ATS are only labels.

`os hunt inbox` is the human step. It walks candidates (accept or reject),
questions you answered that nothing is bound to (bind to a fact — `?` lists
every fact key — creating it from the catalog if needed; keep as a one-off; or
mark long-form), and long-form writing from
submitted applications (approve for reuse, or delete). An accepted pattern is
`active`, and the next application fills that question deterministically;
`os hunt patterns` shows the state of the loop. Prose is reused only once
approved; `prose add` saves writing directly, approved.

The career record's exports and the transcript are portable Git artifacts:

```text
artifacts/career/main.json
artifacts/career/Alex_Example.pdf
artifacts/career/Transcript.pdf
```

## Headed apply

Run `os hunt list`, then pass a displayed row or ID prefix:

```bash
os hunt apply 12
```

The command verifies the company/role, saves the run/application context,
attaches `artifacts/career/Alex_Example.pdf` (published from the career record), follows one unambiguous Apply
control when the ATS starts on a job-description page, fills the current step,
then watches while you review required fields and use Next or Submit in the
browser. It reports a visible Submit control and rescans after navigation, but
does not gate navigation on a required-field audit. No automated action contains
Next, Submit, signature, or captcha operations; consent and attestation
controls take their agreeing option. Each step ends with a **Left for you**
list of every control still empty — required first, then optional — each with
why (no fact yet, a fact that fits no option, a fact the model said is
missing), and the same controls are outlined on the page. A cover-letter control opens the
letter (from `artifacts/career/cover_letter.md`, slots drafted by Claude) in VS
Code; close the tab to fill or upload it. In the
terminal, Ctrl+S records what has been picked up so
far for review, Ctrl+A records answers and marks the application applied when you have submitted
it yourself, Ctrl+D skips the listing (it leaves the board as `skipped`, a
resync cannot bring it back, and `os hunt skipped restore <n>` can), and
Ctrl+C stops and records the application state. A field fills from a fact: the
pattern you accepted for its question (tagged `pattern`), else
one bounded local Claude pass per page step that names which fact answers each
remaining short or choice question (tagged `claude`, also a candidate);
`--no-claude` skips that last pass. A textarea is seeded with approved prose
for the same question, else a Claude draft that leans on your earlier writing.

`--explain` prints, for every control, the five parts of how it was decided:
the **control** (its kind, the attributes that identify it on any site, its
flags, its options), the **question** and the key patterns match on, the
**planner**'s reason (an engine rule, the resolver, the non-answer guard, or a
choice group answered by another option), the **resolver**'s steps (the
binding for this question, the expressions that match, which decided, the fact
and its value, the formatter that wrote it, every other rendering), and the
**operation** (strategies in order, each attempt and its error, the readback).
Controls nothing was planned for are listed after each pass with why.
`--step` does the same and pauses before each fill with the control outlined
on the page: Enter fills it, `s` leaves it for you, `c` stops pausing.

Ashby's Overview tab is a recognized standby state. Return to Application and
the same step continues without refilling or another Claude pass. A submitted
success panel ends automatically. On detected submission, Ctrl+A, Ctrl+D, or
Ctrl+C, persistence finishes before the command closes only the application tab
it opened. `--dry-run` and unexpected worker errors leave that tab open.

Values are rendered from facts at fill time: pay requirements follow the
`compensation.minimum` fact, USD $40/hour (or 40 numeric), and explicit annual
requirements are $83,200 USD (or 83200 numeric). Graduation uses `Fall 2027`
when a term is requested, `December 2027` otherwise, and whichever rendering a
dropdown offers. An office choice bound to `preferences.location_strategy`
follows the §9 tiers. Completed co-op rotations are derived, never stored: the
`coop.anchor` fact says four were complete on 2026-08-01, and one more completes
each May 1, August 1, and November 1 until graduation. Visa/work-permit
expiration stays manual without a date.

Facts are the single source the apply run fills from. `os hunt facts review`
shows each unverified fact beside what the archived answers said to similar
questions, so a contradiction such as a second graduation date is settled once,
on the fact.

## Repair

A field that fails stops at `format` (the fact fit nothing the control offered)
or `strategy` (the widget would not take the value), and the failure says which.
`apply --repair` retries each such field in place, on the page you are filling,
right after the step's fill, on any site: every strategy that applies to the
control for a strategy failure, every other rendering of the fact for a format
failure, each checked by readback. What worked fills the field now, is stored on
the issue as a candidate, and the issue becomes `repaired`; the terminal tags
the line `repaired`. The retry never operates a control that reads as
submitting. `os hunt repair` does the same for failures from runs without
`--repair`, in a new, isolated browser context, where a page script also
cancels every form submission and submit-labelled click and a network guard
aborts any non-GET request that names a submission.

`os hunt validate` walks the repaired issues. It opens a visible page on an
unsubmitted application that showed the question, another posting first, fills
only that field with the candidate, outlines it, and asks. Accepting a strategy
makes it that control's first strategy in every later run; accepting a format
binds the question to the fact with that formatter. Rejecting sends the issue
back to the queue. Pattern-stage failures never come here: no retry invents a
fact, so they go to the Inbox. On account-gated boards (Workday) the isolated
retry cannot reach the form; `apply --repair` can, because it works on your
signed-in page.

Failure diagnosis and fix drafting use subscription-authenticated local Claude.
`apply-fix` requires the literal confirmation `apply`, changes only worker or
worker-test paths, runs the replay suite, and never commits. Commit the reviewed
fix yourself before `verify`; the command records that commit only after live
headed confirmation.
