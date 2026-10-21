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
os hunt replay
os hunt facts review
os hunt resume
os hunt apps
os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply --ats greenhouse
os hunt repair
os hunt validate
os hunt apply failures list
os device install
os device status
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
as needed. Chrome is launched only when nothing holds its debugging port; one that
holds it but is slow to answer is waited for, because launching it again only opens
another window in the running browser. `VisibleBrowser.application_page` reuses the tab already on this
application (`tab_continues`: the application URL is a prefix of wherever the
tab has reached) rather than opening a new one, so a re-run continues on a form
that is already part-filled; `--fresh` overrides it. `rendered_body_text` polls
for the page's text instead of reading it once, because a board that fills its
page from script answers `domcontentloaded` with an empty root — and that text
is both what identifies the posting and the context its drafts are written
from. It never accesses Postgres directly and never submits an application.
See `../docs/jobs-cli.md` for the deterministic mapping, contextual Claude, and
failure-learning workflows.

## The device agent

`device/` makes this machine a Hunt device the Vercel page can see and drive.
`os device install` does it once (the setup is in `../docs/deployment.md`);
`os device serve` is the agent, which a Windows logon task starts hidden in this
distro; `os device status` says what it is doing.

- `identity.py` is who the device is: an id generated once and kept in
  `~/.config/personal-os/device.json`, the Windows computer name, and the
  `https://<machine>.<tailnet>.ts.net` address from `tailscale.exe status --json`.
- `update.py` keeps the checkout current without touching work in progress. A
  clean checkout of `main` that is behind fast-forwards (`git merge --ff-only
  origin/main`) and runs `uv sync --all-packages --python 3.13`, and reinstalls
  the `uv tool` installs when a dependency file changed. A checkout with edits,
  commits of its own, or another branch is never stashed, reset, or merged: it runs
  as it is and the device says "running local changes at <commit>". The fetch runs
  with prompts off, so a passphrase never hangs the agent. It runs when the agent
  starts and before every run the page starts.
- `heartbeat.py` sends the device's row through the local API every 30 seconds.
  Readiness is `os doctor`, run every ten minutes; a closed automation Chrome does
  not count against it, because the agent starts Chrome itself when a page connects
  or a run starts.
- `server.py` is the agent: Starlette on uvicorn, on 127.0.0.1:9340 only, published
  to the tailnet by Windows `tailscale serve`. Every request carries the user's
  Supabase token, which the agent checks by asking the local API for `/v1/me`; a
  browser request must also come from the Vercel origin (preflights answer
  Chrome's private-network check), and websockets from any other origin never
  open. `GET /status` is the device and its run; `POST /runs` starts `os hunt apply
  <reference> --remote` (409 while a run is active; a reference is a board number,
  an id prefix, or a URL, never a flag); `WS /session` is the live session, one
  viewer at a time. The agent never runs a command it was sent. It re-executes
  itself when its own source changes, between runs only.
- `stream.py` is the live view: a raw CDP connection to the browser endpoint,
  separate from the run's Playwright. It attaches only to the tab being shown,
  and lays it out at the viewer's panel (`Emulation.setDeviceMetricsOverride`):
  the panel's size, never narrower than 1024 CSS pixels, at the viewer's pixel
  density. Chrome sizes a tab in a minimized window as it likes (2560×1271 for
  one, 1065×1751 for the next), which made the panel scroll or shrank the page.
  A tab gets its own size back once the viewer leaves it or disconnects, so a
  run nobody watches fills at Chrome's size. It screencasts as quality-90 JPEG at
  the panel's device-pixel width, up to 2560 pixels. Each frame is acknowledged once delivered, which is
  Chrome's own flow control. Neither websocket compresses: frames are already
  JPEG. Chrome answers a wheel event only at its next paint, about 60 a second,
  so turns that arrive while one is in Chrome are summed into the next rather
  than queued behind it. The stream follows the run's tab and any popup it opens
  (Google sign-in), un-minimizes the window, and maps the page's fractional
  positions and DOM key fields to CDP input against the last frame's metadata.
- `runs.py` holds the one run the agent knows about. A page run's output is the log
  until the run says hello on the socket; from then its console arrives there.
- `link.py` is that socket: JSON lines over `state_dir()/device.sock`, and the
  ANSI-to-styled-spans conversion the page draws from.

On the apply side, `jobs/remote.py` registers `os hunt apply` with the agent when
one is listening, in any terminal: its console is teed to the page line by line,
it reports its tab's CDP target id, the audit's gaps go out as structured items,
the page's Save, Submitted, and Skip join the terminal keys, and Stop is the same
KeyboardInterrupt as Ctrl+C. `--remote` is the agent's own run: the page edits its
cover letter instead of VS Code, and its fact gaps are answered on the page;
`AutofillSession.set_fact_and_fill` is the one body behind both that and
`--ask-facts`. With no agent listening a run behaves exactly as it always has.

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
`replay`, `prose`, and `archive`.

**Replay (R69).** A learned change is checked offline before it is kept.
`jobs/replay.py` replays it against sightings, the short answers submitted with
applications (`/v1/jobs/sightings`), each rebuilt as the control it was, with
the options it offered when they were kept: the resolver and formatters run
once as things stand and once with the change. It replays the sightings the
change can affect (those its question or expression matches, and those filled
from its fact), the 25 newest, and 25 more at random, seeded by the change. A
sighting fails when the change gives it a different answer that is not the one
you accepted, or takes that one away; a sighting it leaves as it was never
fails, right or wrong. An answer is the accepted one when its fact can be
written that way for the control, whichever writing the rebuilt control picks,
since a sighting from before options were kept rebuilds a menu without them.
`os hunt replay` runs the code as it stands the same way; a code change cannot
be run as it was, so its before is what the worker filled then, known only when
that was the accepted answer or nothing, and unknown for an answer from before
fill events.

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

## The account wall

`jobs/account_page.py` recognizes an ATS sign-in, create-account, forgot,
new-password, code or check-your-email page — Workday's `data-automation-id`s
first, wording elsewhere — and `page_state` reports it as `ACCOUNT` only when no
application form is on the page. `jobs/account.py` then runs a deterministic
chain, each step at most once: Google (the `contact.email` tile in the
automation profile; any challenge falls through), sign in, create (a refused
sign-in creates first, because Workday says "wrong email or password" when
there is no account; "already exists" leads to the reset), verify (when the
sign-in page says to verify first, from the mail create sent, or after "Resend
Account Verification" for an account made earlier), and reset. Sign-in counts
only once the page has stayed off the wall for about two seconds, because a
tenant redirecting from create to its sign-in page shows nothing in between.
`jobs/mail.py` reads the verification or reset mail over IMAP, read-only
(EXAMINE and `BODY.PEEK[]`), taking only mail that arrived after the click and
names this tenant or company. Because a sender's name can be forged, the flow
follows a mailed link only to the exact host the wall is on (plain `https`, with
nothing in it that Python and the browser would parse differently), and types
nothing once the page has moved to another host; it never runs on Google's own
pages, and it
operates account controls only while `inspect_account` sees an account page. A
captcha, a refused password, a mail timeout or a missing secret parks the run as
an `account_wall` failure. There is no model call and nothing is stored.

Two secrets live in GCP Secret Manager (project `example-gcp-project`) and are
read through `gcloud` into memory by `core/secrets.py`: `jobs-ats-password`, the
one reusable ATS password, and `jobs-gmail-app-password`, the Gmail app password
IMAP signs in with. Neither is written to disk, logged, stored as a fact or sent
to the API. `page_evidence` empties and masks password boxes and replaces every
secret read so far, raw or escaped, in each captured file.

One-time setup, on the machine that runs `os hunt apply`:

1. Run `os chrome` and sign into `alex.example@example.com` at
   accounts.google.com in that window, so Google sign-in has its tile.
2. `os hunt facts set contact.email alex.example@example.com`.
3. Turn on 2-Step Verification for that account, and create an app password at
   myaccount.google.com/apppasswords.
4. Store each secret without it touching shell history (the ATS password: 16
   characters with upper, lower, digit and symbol; `jobs-ats-password` already
   exists):

   ```bash
   read -rs P && printf %s "$P" | gcloud secrets create jobs-gmail-app-password \
     --data-file=- --replication-policy=automatic --project=example-gcp-project; unset P
   ```

5. `os doctor` shows "Account secrets" OK.

## How a field is filled

**Which value.** Every value comes from a fact. `jobs/resolve.py` builds the
run's `Knowledge` — stored facts over career-derived ones, then the derived and
posting facts — and answers a field
in a fixed order: the question's active binding, then active expression
patterns (exactly one fact among those that match). Consent and "how did you
hear" are patterns too (R68), each an active expression reading a fact the
formatter knows: `consent.agrees` takes the one option that agrees, and
`preferences.application_source` ranks the employer's site, a job board, then
Other (a referral first under `--referred`; a free-text box gets the fact's
value, or "Employee referral" when referred). A typeahead nobody has opened is
chosen against its live menu by the same rule. A consent box is never put to a
model; a signature or password is yours. The engine answers two kinds of field
itself (R68): the resume and transcript upload, and a typed follow-up — "If
yes…", "If you answered No…", "If so…", required or optional — gets "N/A" when
the question just above it holds or is planned to hold the other answer. No
value that declines to answer is ever chosen.

**Posting facts.** Some facts belong to the posting, not to you, and are derived
per run in `jobs/facts/derived.py`: whether you worked at or applied to its
company before, its term ("Summer 2027" in the title), your earliest start
(two weeks from accepting, your notice period, or the term's start if later:
winter January 4, spring January 11, summer May 18, fall August 31) and a
twelve-week window from it, whether that window runs into your last semester
(apply warns), and whether you were referred (`apply --referred [NAME]`).
A pattern whose fact is missing or fits none of the options leaves the field,
and the run remembers why (`Miss`). A question no pattern decides goes to one
bounded model pass (`short_answers.py`) that only names a fact — `fits_fact`,
with an expression for every wording where one fits, `no_fact` with the
missing fact's name, or `unsure`. The model never writes a value (R67):
`jobs/learn.py` checks what it named — the fact must fit the control, and an
expression must compile, nest no repetition in another, match the question,
decide it as that fact among the active patterns, and leave every other
recorded question it matches answered as before, else the question is bound on
its own; a model never replaces a binding — then replays it (above). If no
accepted answer changes for the worse, the session saves it active with origin
`model`, and the ordinary pattern pass fills the field in the same run. An
expression that changes one falls back, once, to binding the question alone,
replayed in turn; a lesson that still changes one is saved as a candidate with
its verification, waits in the Inbox as a pattern check, and is saved once a
run. The pattern records the run and question it answers, and the API keeps
the change in `jobs_changes`.
The API refuses, too, any such pattern that would undo a decision of yours: one
you rejected or retired, a binding over an existing one, or a question you
marked one-off or long-form; and an expression already waiting in the Inbox. A question or gate a pattern decides never reaches
the model, even when its fact is missing. Every call sees every fact with its value.

**How it is written.** `jobs/formatters.py` renders a fact for the control in
front of it and names the formatter it used: a date as "December 2027", "Fall
2027", "December", "2027", ISO, US, or month input, by the wording and the input
type; the $40/hour minimum as `$40/hour`, `40`, `$83,200 USD`, or `83200`; yes
and no as whichever option says them ("No, I am not a protected veteran");
numbers into the range that contains them; lists as text, or one box per item.
A fact whose value is the empty value of its type (`""` or `[]`, set with
`facts set KEY --none`) says the answer is none: the planner records a
deliberate blank, nothing is operated, the model is never asked about it, and
the audit lists it only when the form requires an answer, as a fact gap.
`contact.phone_country` is `{country, calling_code}`; a menu whose every option
is a calling code ("+1", "United States (+1)", "🇺🇸 (+1) United States of
America") gets the one with that code, and of several the one naming the
country. A "Country" menu of calling codes beside a phone box is answered the
same way from the country of residence when it is the phone's country.
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
model with the options in front of it (which again only teaches a pattern), and a
`fact_gap` is asked for at the terminal under `--ask-facts`, where the fact you
give also binds the question to it. Before any model call the audit runs the
pattern pass over every open question, so a value a pattern filled that the page
then lost comes back from the pattern; a question the current resolver answers
that is still empty is a `control_error`, never a `pattern_gap`.
`jobs/answer_check.py` is the other half: it reads back the answers the page
already holds and asks whether a fact contradicts one, because an answer can be
confidently filled and wrong. A wrong answer a pattern filled has its question
bound to the fact the model named and is refilled through the pattern path,
but only when that answer came from a pattern a model taught (never one you
made) and the fact renders differently; one the engine chose, and one you
typed, is reported and left alone. The audit never classifies a signature or a
password from a pattern, and its pattern pass replays only pattern answers,
never an upload or a consent click. Before and after each repair the audit squares every
follow-up with the answer just above it as the page holds it: an N/A the worker
wrote is taken back once that answer no longer rules the follow-up out (it says
otherwise, or its fill never landed), and an empty follow-up it rules out gets
N/A; an N/A you typed is yours. The audit looks, repairs, and looks again at most
twice, budgeted at 25 operations a question and 150 a run, then outlines exactly
the questions it lists — only what no lane could fix, each line naming its cause.
You own Next and Submit; what you type is captured as `manual` and, once the
application goes in, waits in the Inbox. A choice you make that a fact
contradicts — the fact an active pattern names, else the one the worker filled
it from — is said at once (`review`), outlined, and never saved, since it is
most often a stray click: put it back, or set the fact if it is right. An answer
you change that makes a follow-up the worker answered N/A apply is said the same
way.

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
since masks reformat it, and an upload by whether you can see it — the input
sits in a rendered part of the page, or the file's name shows. A file input in a
panel nobody opened (JazzHR's "Attach resume") is opened first by the one
attach or upload control in its field, and a file dialog that control raises is
answered with the file, so none waits in your window. A captcha's own response box is never captured as a
control. A choice's question comes from its fieldset's legend, its group's
`aria-label` or `aria-labelledby`, its field container, or the block holding
its options, and only from a container that holds that one question (its
radios share one name, and at most one label or legend in it is no option's
own): a section heading such as "Job specific questions" is never a question,
and a radio group whose question is plain text before its options keeps it, so
a lone "Yes" is never recorded as one. `events.py` turns every attempt
into a fill event (control, strategy, resolver, fact, formatter, the active
pattern, value hash, whether the question wants prose, where it stopped, and the
options the control offered)
that the step sends to FastAPI; a dry run sends one not-attempted event per
question. A choice is refused, and left to you, only while a captcha challenge
is open (its image or puzzle frame showing); a checkbox widget or an
invisible-mode badge waiting for you at Submit does not count (R64). Nothing
solves a captcha. A text box whose suggestions are
plain elements rather than ARIA options (Lever's location) is typed into and the
suggestion naming the same place is clicked (`type_and_pick_suggestion`), the
fallback when `set_value` does not read back.
