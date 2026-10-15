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
as needed. It never accesses Postgres directly and never submits an application.
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
With `apply --repair`, `AutofillSession.repair_in_place` retries each field that
stopped at a format or a strategy on the live page, right after that step's
fill, on any site. `repairer.py` retries one failed field — every applicable
strategy for a `strategy` failure, every other rendering of the fact for a
`format` failure — and returns the first that reads back. `guards.py` holds the
two independent submit guards installed before a repair or validation page
loads: a page script that cancels submissions and submit-labelled clicks, and a
network route that aborts any non-GET request naming a submission; the repairer
also refuses to operate a control that reads as submitting — the only guard in
place on your own live tab, which you go on to submit. `commands.py` is
`os hunt repair [--watch]`, for failures from runs without `--repair`, and
`os hunt validate`. A validated strategy reaches
live fills through the run context as a control preference that
`browser.apply_autofill` tries first.

## How a field is filled

**Which value.** Every value comes from a fact. `jobs/resolve.py` builds the
run's `Knowledge` — stored facts over career-derived ones, then the derived and
posting facts (`employment.worked_for` for this company) — and answers a field
in a fixed order: the question's active binding, then active expression
patterns (exactly one fact among those that match). The engine
answers its own fields first: consent and attestation take the agreeing option,
the resume and transcript upload, "how did you hear" ranks the employer's site,
a job board, then Other, and no value that declines to answer is ever chosen.
A pattern whose fact is missing or fits none of the options leaves the field,
and the run remembers why (`Miss`). Whatever is still empty goes to one
bounded model pass (`short_answers.py`) that only names a fact — `fits_fact`,
`no_fact` with the missing fact's name, or `unsure` — and `VerdictResolver`
renders that fact like any other. Sensitive facts reach the model by key and
label only.

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

**What is left.** After every tier, each empty field — required first, then
optional — is outlined on the page and listed with why it is empty (no fact yet,
a fact that fits no option, a fact the model said is missing). You own
Next and Submit; what you type is captured as `manual` and, once the
application goes in, waits in the Inbox.

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
until one operates, then the value is read back. `events.py` turns every attempt
into a fill event (control, strategy, resolver, fact, formatter, the active
pattern, value hash, whether the question wants prose, and where it stopped)
that the step sends to FastAPI; a dry run sends one not-attempted event per
question. A choice is refused, and left to you, only while an interactive captcha
is on the page; an invisible-mode reCAPTCHA badge does not count.
