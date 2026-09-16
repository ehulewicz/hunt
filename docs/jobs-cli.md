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
os hunt answers      # = os hunt answers list
os hunt answers add --question "Are you authorized?" --text "Yes" --reuse exact
os hunt answers add --question "Why this company?" --file ./answer.md --reuse contextual
os hunt answers add --question "consider me for other (job )?opportunities" --text "Yes" --reuse pattern
os hunt answers edit 1 --file ./revised-answer.md --reuse contextual --approve

os hunt resume build
os hunt resume       # = os hunt resume watch --open

os hunt queue
os hunt queue add 3 7
os hunt queue empty
os hunt apps
os hunt apps 1 oa
os hunt skipped               # listings passed over with Ctrl+D
os hunt skipped restore 2     # back on the board

os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply                     # top board row on a supported ATS
os hunt apply --ats greenhouse    # top Greenhouse row
os hunt apply today               # first of `os hunt today` on a supported ATS
os hunt apply today --ats greenhouse
os hunt apply 1 --no-claude
os hunt apply 1 --done       # applied outside the worker; just record it

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
or an id prefix at the moment you run them, and `apps`, `answers edit`, and
`apply failures …` count rows in the list they just fetched.
`os hunt open <row>` opens the direct company/ATS URL without creating an
application; `--source` opens the discovery URL.
`queue add` puts listings in the queue; `queue empty` runs `apply` through it in
order, one interactive session at a time, and stops after any that is not
submitted. `apps` lists everything past the queue with its lifecycle status
(`applied`, `oa`, `oa_completed`, `interview`, `offer`, `rejected`); `apps
<row> <status>` moves one along. Answer edits update the hosted canonical library
immediately; Git is not a second answer store. `exact` answers are reused
unchanged. `pattern` rows hold a case-insensitive regular expression as the
question and answer any autofill question it matches, standing in for the
user's policy on things like "willing to relocate" without one row per exact
wording. `contextual` and unmatched long-form questions invoke local Claude,
with the 20 percent guard applied whenever a base exists. Preparation without another flag
compiles the canonical resume unchanged.

The editable source and generated PDF are both portable Git artifacts:

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

The command verifies the company/role, saves the tracked run/application context,
attaches the tracked `artifacts/career/Alex_Example.pdf` unchanged, follows one unambiguous Apply
control when the ATS starts on a job-description page, fills the current step,
then watches while you review required fields and use Next or Submit in the
browser. It reports a visible Submit control and rescans after navigation, but
does not gate navigation on a required-field audit. No automated action contains
Next, Submit, signature, or captcha operations; consent and attestation
controls take their agreeing option. Each step ends with a **Left for you**
list of the required controls still empty. A cover-letter control opens the
letter (from `artifacts/career/cover_letter.md`, slots drafted by Claude) in VS
Code; close the tab to fill or upload it. In the
terminal, Ctrl+S saves what has been picked up so
far, Ctrl+A saves and marks the application applied when you have submitted
it yourself, Ctrl+D skips the listing (it leaves the board as `skipped`, a
resync cannot bring it back, and `os hunt skipped restore <n>` can), and
Ctrl+C stops and saves. Whatever profile facts and exact answers leave unfilled goes
through library and built-in pattern rules, then one bounded local Claude pass
per page step over the remaining short and choice questions; `--no-claude`
skips that last pass.

Ashby's Overview tab is a recognized standby state. Return to Application and
the same step continues without refilling or another Claude pass. A submitted
success panel ends automatically. On detected submission, Ctrl+A, Ctrl+D, or
Ctrl+C, persistence finishes before the command closes only the application tab
it opened. `--dry-run` and unexpected worker errors leave that tab open.

Standing salary policy precedes saved exact answers: requirements are USD
$35/hour (or 35 numeric), explicit annual requirements are $72,800 USD (or
72800 numeric), and a stated compensation range is accepted. Graduation uses
`Fall 2027` when a term is requested and `December 2027` otherwise; completed
co-op terms are 4.5. Visa/work-permit expiration stays manual without a date.

Failure diagnosis and fix drafting use subscription-authenticated local Claude.
`apply-fix` requires the literal confirmation `apply`, changes only worker or
worker-test paths, runs the replay suite, and never commits. Commit the reviewed
fix yourself before `verify`; the command records that commit only after live
headed confirmation.
