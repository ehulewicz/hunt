# Personal OS local CLI

Install the Python 3.13 CLI in editable mode:

```bash
cd /home/eahtr/personal-os
UV_CACHE_DIR=/tmp/personal-os-uv-cache uv tool install --editable ./backend
UV_CACHE_DIR=/tmp/personal-os-uv-cache uv tool install --editable ./workers
```

The installed entry point is `os`. Jobs commands live under `os hunt`:

```bash
os doctor
os hunt sync
os hunt            # list
os hunt 20         # list 20
os hunt today
os hunt inbox
os hunt patterns
os hunt facts review
os hunt resume
os hunt apps
os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply --ats greenhouse
os hunt repair
os hunt validate
os hunt apply failures list
```

The resume is the career record in the database. `os hunt resume` (= `resume
edit --open`) opens a working copy as JSON in VS Code and sends each save to the
API; an accepted save republishes the exports `artifacts/career/main.json`
(committed) and `Alex_Example.pdf` (generated, ignored by Git). `resume build` republishes
them from the record, `resume build --input FILE` compiles a file without
touching the record, and `resume import` loads `main.json` into an empty record
once. Facts the resume determines — school, degree, graduation, GPA, whole years
of experience, the current employer, every organization, every skill — are
derived from the record by `jobs/facts/derived.py`; a stored fact with the same
key still wins until the facts session removes it, and `os hunt facts` marks
each one that disagrees with the record.

The CLI launches local FastAPI and the dedicated native Windows Chrome profile
as needed. `VisibleBrowser.application_page` reuses the tab already on this
application (`tab_continues`: the application URL is a prefix of wherever the
tab has reached) rather than opening a new one, so a re-run continues on a form
that is already part-filled; `--fresh` overrides it. `rendered_body_text` polls
for the page's text instead of reading it once, because a board that fills its
page from script answers `domcontentloaded` with an empty root — and that text
is both what identifies the posting and the context its drafts are written
from. It never accesses Postgres directly and never submits an application.
See `../docs/jobs-cli.md` for the deterministic mapping, contextual Claude, and
failure-learning workflows.

## What it learns, and the archive

Questions, patterns, and prose start empty and are learned (design §0). Every
site is filled from its controls — there are no per-site adapters, and a
control learned on one board answers the same control on any other.
`jobs/sites.py` only labels a URL (`greenhouse`, `ashby`, `lever`, `workday`,
`generic`) for querying runs and failures; nothing branches on it.

The old answer library and rules are the archive, kept only for the facts
session and dropped after it: nothing in an application reads it, not even as
a suggestion. `jobs/archive/rules.py` keeps every retired rule verbatim with its
translation — the fact key it read and, where its wording implied one, a
formatter — and `jobs/archive/lookup.py` applies them in the old order, for
`os hunt archive` to show. `tests/fixtures/archive_translations.json` freezes
what every archived question translates to (R30). `jobs/facts/catalog.py` is
every fact key with its label, type, sensitivity, and whether it is stored,
career-derived, or derived.

`jobs/inbox.py` is `os hunt inbox`; `jobs/knowledge.py` is `os hunt patterns`,
`prose`, and `archive`.

## Repair

`jobs/repair/` is the repair lane (`docs/jobs-cli.md` describes using it).
On every apply (`--no-repair` turns it off), `AutofillSession.repair_in_place`
retries each field that
stopped at a format or a strategy on the live page, right after that step's
fill, on any site. `repairer.py` retries one failed field — every applicable
strategy for a `strategy` failure, every other rendering of the fact for a
`format` failure — and returns the first that reads back. `guards.py` holds the
two independent submit guards installed before a repair or validation page
loads: a page script that cancels submissions and submit-labelled clicks, and a
network route that aborts any non-GET request naming a submission; the repairer
also refuses to operate a control that reads as submitting — the only guard in
place on your own live tab, which you go on to submit. `commands.py` is
`os hunt repair [--watch]`, for failures left by `--no-repair` runs, and
`os hunt validate`. A validated strategy reaches
live fills through the run context as a control preference that
`browser.apply_autofill` tries first.

## The gate between a posting and its form

`jobs/gateway.py` captures the ways into an application on a page that offers
no form — an open dialog's choices, else the page's own controls — as one
`choice_group` whose options are the buttons and whose question is the dialog's
heading. `inspect_fields` never sees a button, because on a form a button is
Next or Submit; a gate is captured only where there is no form to fill. The
answer comes from a fact (`preferences.application_start`) through the ordinary
resolver, so patterns, the Inbox and `--explain` need no special case, and the
readback is `form_appeared`. A gate nothing answers is left alone: the capture
installs a click recorder, so the choice you make yourself is recorded as that
question's answer and the Inbox can bind it.

## How a field is filled

**Which value.** Every value comes from a fact. `jobs/resolve.py` builds the
run's `Knowledge` — stored facts over career-derived ones, then the derived and
posting facts — and answers a field
in a fixed order: the question's active binding, then active expression
patterns (exactly one fact among those that match). The engine
answers its own fields first: consent and attestation take the agreeing option,
the resume and transcript upload, "how did you hear" ranks the employer's site,
a job board, then Other (a referral first under `--referred`; a free-text box
gets "Company website"), a required "If you answered No…" follow-up gets "N/A"
when the choice above was answered the other way, and no value that declines to
answer is ever chosen.

**Posting facts.** Some facts belong to the posting, not to you, and are derived
per run in `jobs/facts/derived.py`: whether you worked at or applied to its
company before, its term ("Summer 2027" in the title), your earliest start
(two weeks from accepting, your notice period, or the term's start if later:
winter January 4, spring January 11, summer May 18, fall August 31) and a
twelve-week window from it, whether that window runs into your last semester
(apply warns), and whether you were referred (`apply --referred [NAME]`).
A pattern whose fact is missing or fits none of the options leaves the field,
and the run remembers why (`Miss`). Whatever is still empty goes to one
bounded model pass (`short_answers.py`) that only names a fact — `fits_fact`,
`no_fact` with the missing fact's name, or `unsure` — and `VerdictResolver`
renders that fact like any other. Every call sees every fact with its value.

**How it is written.** `jobs/formatters.py` renders a fact for the control in
front of it and names the formatter it used: a date as "December 2027", "Fall
2027", "December", "2027", ISO, US, or month input, by the wording and the input
type; the $40/hour minimum as `$40/hour`, `40`, `$83,200 USD`, or `83200`; yes
and no as whichever option says them ("No, I am not a protected veteran");
numbers into the range that contains them; lists as text, or one box per item.
Against options, the first rendering that one option matches wins; none is a
`format` failure. `jobs/office.py` is the §9 office policy, read from
`preferences.location_strategy` when a question is bound to it, with draws
seeded by the application.

**What an answer reveals.** A form can add questions once a choice is made
(race after Hispanic/Latino, a referral name after "were you referred"). After
the deterministic pass the page is read again, and any empty question that was
not there before gets its own pass (`fill_revealed`, at most twice).

**What the audit does about it.** `jobs/audit.py` classifies what is still
wrong once every tier has had its turn, and `AutofillSession.remediate` runs the
lane each cause names: a `format_error` tries the fact's other renderings, a
`control_error` the control's other strategies, a `pattern_gap` goes back to the
model with the options in front of it, and a `fact_gap` is asked for at the
terminal under `--ask-facts`. `jobs/answer_check.py` is the other half: it reads
back the answers the page already holds and asks whether a fact contradicts one,
because an answer can be confidently filled and wrong. A wrong answer the worker
filled is re-rendered from the fact the model named; a wrong answer you typed is
reported and left alone. The audit looks, repairs, and looks again at most
twice, budgeted at 25 operations a question and 150 a run, then outlines and
lists only what no lane could fix, each line naming its cause. You own Next and
Submit; what you type is captured as `manual` and, once the application goes in,
waits in the Inbox.

**How it is explained.** `jobs/explain.py` is `apply --explain` and `--step`:
the planner records a reason for every control (`AutofillPlan.reasons`), each
resolver can `explain` a field without recording a miss, and `apply_autofill`
reports each fill to a `FillObserver` before and after operating it, which
`--step` uses to pause.

**How it is operated.** `jobs/registry/` names what the fill path does. `kinds.py` classifies every
detected control into one of seven kinds — `text_entry`, `native_select`,
`choice_group`, `multi_choice_group`, `toggle`, `dynamic_choice`, `file` — from
generic HTML and ARIA alone; a group of `aria-pressed` buttons is found by that
state, not by any site's class names. `strategies.py` lists each kind's candidate
strategies in order, and `browser.OPERATIONS` performs them: candidates are tried
until one operates, then the value is read back: a select by its chosen
option's text (or the value attribute it was set by), a phone by its digits,
since masks reformat it. A captcha's own response box is never captured as a
control. `events.py` turns every attempt
into a fill event (control, strategy, resolver, fact, formatter, the active
pattern, value hash, whether the question wants prose, and where it stopped)
that the step sends to FastAPI; a dry run sends one not-attempted event per
question. A choice is refused, and left to you, only while a captcha challenge
is open (its image or puzzle frame showing); a checkbox widget or an
invisible-mode badge waiting for you at Submit does not count (R64). Nothing
solves a captcha. A text box whose suggestions are
plain elements rather than ARIA options (Lever's location) is typed into and the
suggestion naming the same place is clicked (`type_and_pick_suggestion`), the
fallback when `set_value` does not read back.
