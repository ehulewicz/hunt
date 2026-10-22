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
key still wins until `os hunt facts delete KEY` removes that override, and
`os hunt facts` marks each one that disagrees with the record.

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
- `memory.py` samples, every 30 seconds, the resident memory of the agent, every
  process under it, and a terminal run by the pid its hello gave, and appends one
  line to `memory.log` in the state directory (rotated at 5 MB). A process past
  4 GiB is stopped with SIGTERM, the line names it, and the run's log says so;
  the agent itself past the limit restarts once no run is active. A leak that
  reaches the kernel's OOM killer otherwise leaves no record of what it was.
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
  Once the page settles, on every load, in-page step, and panel resize, it
  measures where the page's text and controls sit and narrows the layout until
  16 CSS pixels are left either side of them (never below 800), rendering at the
  matching higher density so the zoomed text stays sharp. Chrome keeps one
  override per tab, not per client, so the run's own connection or any other
  client clearing one leaves the tab at its window's size, the shape of
  whichever monitor it is on. A frame at any other width says so, and the
  override is put back, at most once a second.
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
  A run that announces a reload keeps its place while its socket is closed, and
  its next process attaches to it again by its run id.
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
Current, permanent, and mailing address questions all use the same `address.*`
facts; their labels explicitly tell the model that these meanings coincide.

`jobs/inbox.py` is `os hunt inbox`; `jobs/knowledge.py` is `os hunt patterns`,
`replay`, `prose`, and `archive`; `jobs/history.py` is the read-only `os hunt
changes`, `fixes`, and `sites`.

**Replay (R69).** A learned change is checked offline before it is kept.
`jobs/replay.py` replays it against sightings, the short answers submitted with
applications (`/v1/jobs/sightings`), each rebuilt as the control it was, with
the options it offered when they were kept: the resolver and formatters run
once as things stand and once with the change, using each attempt's original
fact values, types, and seed from its fill event. Today's dates, posting facts,
or edited facts therefore cannot count as code regressions. It replays the sightings the
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
fill events. Legacy sightings without snapshots can check a pattern only when
the unchanged resolver still produces the accepted answer. Code replay lists
them as unchecked; its summary reports unavailable baselines explicitly.
The command exits 0 for a passing check, 1 for changed accepted answers, and 2
when authentication, transport, or API errors prevent the check from completing. `--json`
ends the output with the counts as one JSON line, which the fixer records.

A fact change is replayed too (`replay_fact`): the sightings filled from the
fact, or answered by a pattern that names it, are answered with each attempt's
original facts and again with the fact changed, so only the change can make an
accepted answer differ. Your fact is saved either way; the counts go with it,
what disagrees is printed, and a failed replay puts the fact in the Inbox as
contradicted. `facts set`, `facts review`, the Inbox, and a fact given mid-run
all replay what they set.

**Prose attribution.** `jobs/provenance.py` marks matching stretches of a final
long-form answer as `previous` or `model`; unmarked text is yours. Previous
writing wins when both sources match, and matches shorter than 16 characters
are ignored. Spans use character offsets in the final text, are ordered, and
never overlap. Every run saves its prose for review, including runs not submitted:
a long-form answer goes to the API the moment its text changes, a draft as it is
written and your edits as you make them, so a killed run loses none of it.
For a new question, the worker asks FastAPI for semantically similar questions
with approved prose, caches that result for the run, and supplies up to five
pieces to the existing drafting call. Exact approved questions reuse their prose
directly. If semantic search is unavailable, it reports that and uses the
existing wording comparison; no embedding key is kept on the device.

## Repair

`jobs/repair/` is the repair lane (`docs/jobs-cli.md` describes using it).
On every apply (`--no-repair` turns it off), `AutofillSession.repair_in_place`
retries each field that
stopped at a format or a strategy on the live page, right after that step's
fill, on any site. `repairer.py` retries one failed field — every applicable
strategy for a `strategy` failure, every other rendering of the fact for a
`format` failure — and returns the first that reads back. `jobs/safety.py` holds
everything that keeps the worker from submitting: the two independent guards, a
page script that cancels submissions and submit-labelled clicks and a network
route that aborts any non-GET request naming a submission, and the worker's own
refusal to operate a control that reads as submitting. A repair or validation
page is guarded from before it loads. Your own tab is guarded only while the
worker operates a field on it (`armed`): the script acts only while armed, and
the route exists only for the operation, so your Submit works the moment the
worker stops. Reaching the form (the Apply click, a gate, the account wall) is
never armed, since "Apply now" reads as submitting. `commands.py` is
`os hunt repair [--watch]`, for failures left by `--no-repair` runs, and
`os hunt validate`. A validated strategy reaches
live fills through the run context as a control preference that
`browser.apply_autofill` tries first.

## Reaching the posting

A listing's URL sometimes lands on a careers home or a job list. The page is the
posting only when it holds the whole title or a heading is the title
(`navigate.is_posting`); a page that already shows an application form with the
company on it is taken as it is. Otherwise `jobs/navigate.py` looks for it the
way you would, each step read back: the route written for that site
(`jobs/routes/`, one module per site, each registering itself with
`@route("site.com")`), then a link whose text is the role, the site's search box
with the full title and then shorter parts of it (without the parenthetical,
what follows a separator, the term), and a link into the site's open roles, a
few hops at most and never off the site. A link scores by the role's words,
and loses for missing the role's kind ("Intern"), the title's term, or for
words the title lacks, so a category link or another role is never taken.
What it cannot find is reported as `posting_not_found` with what the page
offered, and the run watches the browser until you open the posting, so a run
never ends because its URL was a careers home.

## How the run fixes itself

`jobs/fix/` is the fixer. When the audit is done with a page step, what only
code can settle goes to it (`AutofillSession.escalate`): a widget no kind
captures, a question that cannot be read, a format or control error the repair
lane ran out of renderings or strategies for, a wall the Apply control, gates
and account flow did not pass (never the account flow itself, which is yours),
and a posting the navigator could not find. A missing fact, a pattern gap and
prose never are. The run keeps filling while the fixer works, one failure at a
time in a background thread (`queue.py`):

1. `scratch.py` clones the repository (`git clone --shared`) under the state
   directory and writes the live page's evidence in as fixtures.
2. `lane.py` asks a model, with file tools only and the scratch clone as its
   working directory, for the smallest code change and a regression test
   against those fixtures. It may change `jobs/` code and add tests; it may not
   edit an existing test, delete a file, or touch the denylist (`safety.py`,
   the account and mail, the model call sites, `learn.py`, `replay.py`, the
   fixer).
3. The worker checks the paths, then runs ruff, mypy and the whole worker suite
   on the scratch code (the workspace interpreter, the scratch source first on
   `PYTHONPATH`), then `os hunt replay --json` against the answers you
   accepted.
4. At the run's next safe point (between polls, after an audit) `probe.py`
   runs in its own process on the scratch code, attaches to the run's tab over
   CDP, arms the submit guards, and operates only the target: the field from
   its fact, read back (a control it now captures that no fact answers counts
   as detected), or the route, read back as the posting or the form.
5. `keep.py` commits exactly the fix's hunks on `main` from a temporary index,
   moving `main` only if it has not moved, applies them to the working tree
   without disturbing anyone's staged or unstaged work, and pushes; a refused
   push leaves the commit local and says so. The change is recorded with its
   parent, diff, attempts, gates, replay counts and probe, and the issue is
   `fixed`.

Any step that fails goes back to the model with what failed; after three
attempts, or when the model names a missing fact or calls it impossible, the
issue is `unsolved` and waits in the Inbox. A model outage stops the fixer for
the run. Once a fix is kept and no other is mid-flight, the run saves its
observations, writes its state (`SessionState.export`) and re-executes itself
(`apply --continue-run`, the same PID), so the device agent keeps the run: it
announces `reload`, and the agent keeps its place for the new process. The new
process reads the facts and patterns as they are now, continues on the same
tab, restores what the run already asked, spent and decided, and fills only
what is still empty. Each fix's state goes to the page as `fixes`. `--no-claude`
and `--dry-run` run no fixer.

For a repeatable local run, serve the static Staged Robotics fixture from the
repository root:

```bash
python3 -m http.server 8765 -d workers/tests/fixtures/staged_site
```

Open `http://localhost:8765/index.html` in the device's Chrome (Windows reaches
WSL through localhost forwarding), and use **Software Engineering Intern
(Summer 2027)** at **Staged Robotics** as the target. The careers link leads to
a query-string job search, then posting `jobs/101.html` and its Apply link.
The form has ordinary fields and two deliberately unsupported widgets: Years
of programming (an ARIA slider) and Preferred team (a div-based radio group).
Both work with clicks and keys and expose their values through ARIA. There is
no submit endpoint; Submit application does nothing. Uploaded files stay in
the tab. `uv run pytest -q tests/test_staged_site.py` from `workers/` checks
the navigation, unknown-control audit, widgets, and inert submit using a
temporary loopback server and headless Playwright. Stop the manual server with
Ctrl+C when finished.

## The gate between a posting and its form

`jobs/gateway.py` captures the ways into an application on a page that offers
no form — an open dialog's choices, else the page's own controls — as one
`choice_group` whose options are the buttons and whose question is the dialog's
heading. `inspect_fields` never sees a button, because on a form a button is
Next or Submit; a gate is captured only where there is no form to fill. The
answer comes from a fact (`preferences.application_start`) through the ordinary
resolver, so patterns, the Inbox and `--explain` need no special case, and the
readback is `form_appeared`. A gate or an account wall that appears after the
first page is passed by the next step the same way. A gate nothing answers is
left alone: the capture
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

**Completion policy.** Every field is answered, including optional short text,
long-form questions, and uploads. Optional is the employer's validation rule,
never permission for the worker to skip it. Give useful information from facts,
approved writing, or a grounded draft. No and N/A are valid answers only when
the facts or the answer above establish them; missing facts never imply No.
An unanswered field remains an outlined, actionable gap even when optional.

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
bounded model pass (`short_answers.py`) — shown every fact, with the facts whose
labels are closest to the question by embedding listed first (`rank-facts`;
labels go to the API, never values) — that only names a fact — `fits_fact`,
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
the change in `jobs_changes`. An expression already waiting in the Inbox also
falls back to a checked binding of this question alone. If the answer check
finds a wrong worker answer but historical answers disagree with the correction,
the binding stays a candidate for review and is used only for this run. A
user-authored pattern or an existing binding is still never overridden.
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
`facts set KEY --none`) says the answer is none: free text gets N/A and a menu
gets its explicit None or Not applicable option. A control that cannot accept
either (a URL, date, number, or menu without such an option) remains an explicit
fact gap, required or optional; the worker never fabricates a value to satisfy it.
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
Empty prose is a `draft_error` and returns to the prose drafter, with at most
two drafting attempts per question per run; it never goes to the fact selector.
A radio whose question was not captured is a `detection_error`, including a
selected radio: Yes and No alone cannot identify a question. A widget no control
kind captures — an ARIA slider, spinbutton, radio group, listbox, switch, or
an editable box with no form input of its own, outside the site's navigation
and any combobox's popup — is found by `inspect_unknown_controls` and is an
`unknown_control` when it is empty, with its role, markup and frame kept as
evidence. A wall between the run and its form (no Apply control, gate, or
account page led to one, or the account flow gave up) is a `wall` finding,
stored with what the page offered (`report_wall`) and sent to the page's gaps.
Missing facts, unknown controls and unreadable questions left open are stored
once a run as failure issues (`persist_open`), with page evidence for the ones
only code can settle, so they outlive the run. A model outage
(including a subscription usage limit) is reported once and stops further model
calls for that run. An answer check that returns no judgement remains a
`model_error` on subsequent audits, so it cannot become a successful check by
being omitted. Every `claude -p` call is built and run by `jobs/model.py`, which
meters it and, through `model_errors.py`, interprets failure envelopes without
logging their prompt, answer, or raw error payload.
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
generic HTML and ARIA alone, or into a custom kind. A custom kind
(`registry/custom/`, one module each, usually written by the fixer) teaches a
widget with no form input of its own: a selector for its root, a script that
reads what it holds, and its strategies. Registering one adds its strategies to
the tables below, `inspect_fields` captures its widgets after the form inputs,
`fresh_control` finds them by position, the readback compares what its script
reads, and the widget is never an `unknown_control` again; a group of `aria-pressed` buttons is found by that
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
the walk continues past an options-only wrapper to the question above it, and
required markers on the question's child spans count too. A question that
cannot be recovered is reported for extraction repair and is never filled as
though an option label were its question. A radio group whose question is plain
text before its options keeps it, so
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
An almost invisible, read-only combobox input is opened with focus and ArrowDown,
so a styled dropdown does not fail while trying to click a fraction of a pixel.
A hidden native select beside the visible combobox is its responsive mirror and
is not counted as another question.
ARIA-owned menus can identify choices with disabled state and keyboard tab stops,
even without `role=option`. Read-only menus only select a matched choice; they
never fall through to typing and Enter, which can commit an unrelated first
option. A radio or checkbox covered by its own label falls back to focused Space
and must read back the requested checked state.
