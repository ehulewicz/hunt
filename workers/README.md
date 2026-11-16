# Personal OS local CLI

Install the Python 3.13 CLI in editable mode:

```bash
cd /home/eahtr/personal-os
UV_CACHE_DIR=/tmp/personal-os-uv-cache uv tool install --python 3.13 --editable ./backend
UV_CACHE_DIR=/tmp/personal-os-uv-cache uv tool install --python 3.13 --editable ./workers
```

Name the interpreter: without `--python 3.13`, uv may build the tool on a newer
system Python. On Python 3.14 Playwright's sync API never frees the task it
makes for each call, so a run's memory climbs about 1.5 GB an hour; `os doctor`
fails its Python check there and `os hunt apply` will not start. Every HTTP
client shares one TLS context (`core/tls.py`), since a fresh context's first
handshake keeps about half a megabyte for good.

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

The resume is the career record in the database, edited at /resume/edit or
with `os hunt resume get` (the document as JSON on stdout) and
`os hunt resume put <file>` (validates and saves it; a refusal prints the API's
reason). The backend builds the one-page PDF on each save — and when a header fact changes — and serves it at
/resume; a run downloads that PDF once for every upload in it. Facts the resume
determines — school, degree, graduation, GPA, coursework, the high school's
name, city and graduation year (from a `high_school` education row), years of
experience, the competition robotics teams, the current employer, every
organization, every skill — are read
from the record by `jobs/facts/career.py`. Most are static, a value as the
record states it, like the GPA; years of experience, completed co-op rotations,
a completed internship, and the robotics teams are computed from its entries.
Every stored entry counts,
printed on the resume or not, and an activity never counts as employment. The
totals count experience and research months, overlaps once, in half years —
two months past a half year rounds up — and the same total answers
full-time, work and industry experience questions. A single entry's duration
is never rounded: it is answered with its
dates or its months. These
facts are never stored: a stored row under one of their keys is
ignored, `os hunt facts` marks it, and `os hunt facts delete KEY` removes it.

The narrative library is the writing long-form answers are composed from,
edited at /library or with `os hunt library get` (its entries as JSON) and
`os hunt library put FILE` (one entry or a list; `put` drops the read-only
`id`, timestamps and `human_percent` so a get, edit, put round trip works).
`os hunt library list` shows each entry's word count and its GPTZero human
share, banded green, yellow, red, with a checked/unchecked summary;
`os hunt library score CODE PERCENT` records one. `os hunt library import FILE`
loads the question-bank markdown once into an empty library (`--dry-run` parses
and links without writing); `jobs/library.py` parses it and links each
experience and project entry to its career entry by organization or title.

The composer (`jobs/compose.py`) writes one answer per question from the
library's answered entries, with the career record, facts, and the posting as
evidence, and names the question's family and the library's coverage (full,
composable, partial, missing). The shared style rules the composer, the
drafter and the cover-letter prompt all carry live in `answers.PROSE_STYLE`;
the words the guards hard-reject are `answers.CLICHES`. Its guards refuse a
reply that cites an entry it
was not given, a non-interest entry as the interest, an answer when coverage is
missing or none when it is not, a semicolon, colon or dash, a cliché, markup, a
number in no cited source, or a reply over the question's stated limit. A
rejected reply is retried once with the failures listed; two failures leave the
question unanswered. At fill time every long-form field is composed this way
first; a question the library does not cover — an empty library, coverage
missing, an empty answer, or two failed attempts — goes to the fallback
drafter, which writes it from the facts and the whole career record with the
run's stored prose as reference only, and is flagged for review.
`os hunt prose try --library MD --questions TSV --out
REPORT.md` composes an answer for every prose question in an audit TSV against a
bank file and writes a report; it fills and saves nothing. `os hunt prose
coverage` lists the open gaps by family, ranked by companies affected; `--scan`
runs the composer classify-only over the stored prose questions, records a gap
for partial or missing coverage, and resolves the open gap once the library
covers the question. Both bound their model calls with `--max-calls`.

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
from. It never accesses Postgres directly, and it submits an application only
on a run you start with `--submit` (below).
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
  or a run starts. `os doctor` also checks the fixer (`jobs/readiness.py`): the
  checkout is on `main`, ruff, mypy and pytest run under the workspace
  interpreter, `git ls-remote origin HEAD` answers within 10 seconds with
  prompts off, and `claude` is on `PATH`. That check is advisory: a failure
  prints `WARN`, leaves the exit code and the device's readiness alone, and a
  run fills as before but fixes no code.
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
  viewer at a time. `WS /drive` is the drive channel (`drive.py`, below), and
  `GET`/`POST /drive` read and set its switch; `GET /status` carries `drive_on`.
  The agent never runs a command it was sent. It re-executes
  itself when its own source changes, between runs only; a run counts until its
  process is reaped, not just until it says it ended, so no run outlives the
  agent's restart as a zombie.
- `drive.py` is `WS /drive`, where a driver of the user's own on the tailnet
  fills an application through this Chrome, one leased tab at a time. It takes
  the token only as a bearer header, refuses any request with an Origin, and
  needs the drive switch on. The first message is
  `{"type": "lease", "listing_id", "url"}`; the lease is refused while the
  switch is off, another lease or a page run is active, the URL is not on
  `ATS_HOSTS` (Lever, Greenhouse, Ashby and their subdomains), the listing is
  locked, or the API has its application at `applied` or later. The agent opens
  its own browser connection, finds the posting's tab or opens one, and attaches
  to it alone with `Target.attachToTarget {flatten: true}`, never
  `setAutoAttach`, so out-of-process frames (hCaptcha) are never attached.
  Commands `{"id", "method", "params"}` reach that tab only when `ALLOWED` names
  them: Page reads, `navigate` (lease host only) and `reload`, Input, DOM reads,
  Accessibility, `DOM.setFileInputFiles` (only on the last file chooser's node,
  only with files in `drive/files/`), and `Runtime.evaluate` only in an
  isolated world the lease created, by value. `Runtime.enable`, Network,
  Storage, Target, Browser, Emulation and Fetch never pass. `navigate`, `reload`
  and Input are refused on a tab whose URL matches a locked listing. Only the
  tab's load, main-frame navigation and file-chooser events reach the driver.
  The lease ends, with `{"type": "ended", "reason"}` and the socket closed, on
  the driver's `release`, the Hunt page's Stop, any live-view pointer, wheel,
  key or text, the switch turning off, 10 idle or 45 total minutes, or the tab
  closing or leaving the lease host. A `submit` message is refused until a
  press route plugs into `DriveManager.on_submit`.
- `drive_state.py` is what the drive keeps in `state_dir()/drive/`. The switch
  (`switch.json`) is off by default; `os device drive on|off|status` sets it,
  through the agent when it runs, so off ends a lease at once. The per-listing
  locks (`locks.json`: `pending`, `submitted`, `unconfirmed`, `burned`) are keyed
  by listing id and by the posting URL without scheme, query, trailing slash,
  `/apply` or `/thanks`; a lease's listing becomes `submitted` when any tab
  reaches a confirmation URL (Lever's `/thanks`) or the Hunt page's Submitted
  arrives during a lease. Both files are written whole and renamed into place.
  `audit.jsonl` has one line per driver message: the time, the lease's listing,
  the method, a summary of its parameters (lengths for typed text and keys,
  never the values; the URL for `navigate`) and whether it was forwarded or why
  it was refused; it rotates at 5 MB like `memory.log`.
- `stream.py` is the live view: a raw CDP connection (`cdp.py`) to the browser
  endpoint, separate from the run's Playwright, with the layout, text fit and
  input mapping in `pane.py`. It attaches only to the tab being shown,
  and lays it out at the viewer's panel (`Emulation.setDeviceMetricsOverride`):
  the panel's size, never narrower than 1024 CSS pixels, at the viewer's pixel
  density. Chrome sizes a tab in a minimized window as it likes (2560×1271 for
  one, 1065×1751 for the next), which made the panel scroll or shrank the page.
  The layout is never narrower than the panel, so the page fills it and is
  never shown past 1x, and a thin form keeps the page's whitespace. Once the
  page settles, on every load, in-page step, and panel resize, the stream
  measures it. A page that scrolls sideways is laid out wider until it fits,
  zoomed out to no less than half size. Text a clipped box cuts off does not
  scroll the page, so it zooms nothing out. A panel narrower than a desktop
  shows the desktop layout smaller, and the stream narrows that layout toward
  the panel until 16 CSS pixels are left either side of the page's text and
  controls, never past the panel and never below 800. A frame at any other size
  than the layout is never shown. The run's full-page evidence screenshot lays the tab out at the
  whole page while it shoots, and Chrome restores the override's size itself
  once it has (at density 1). Chrome keeps one override per tab, not per client,
  so another client clearing one leaves the tab at its window's size, the shape
  of whichever monitor it is on. Frames still at another size after a second say
  so, and the override is put back.
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
the page's Save, Submitted, and Skip reach the run, and Stop is the same
KeyboardInterrupt as Ctrl+C. `--remote` is the agent's own run: the page edits its
cover letter reviewed on the page, and its fact gaps are answered there;
`AutofillSession.set_fact_and_fill` is the one body behind both that and
`--ask-facts`. A record or computed fact — one the career record, other facts,
or the posting decides — is never asked for on either path; the gap names what to
change instead. With no agent listening a run behaves exactly as it always has.

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
`replay`, `prose`, and `archive` — `patterns --check` lists recorded questions
decided by expressions of different facts and expressions matching a question
bound to another; `jobs/history.py` is the read-only `os hunt
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
A fact set without its replay (the web Library, or the CLI when the sightings
could not be read) is recorded pending. A new `os hunt apply` run (not a
reload) and `os hunt replay` (not `--json`, the fixer's check, which writes
nothing) first replay every pending fact change
(`knowledge.resolve_pending_facts`): each from the stored facts with that fact
as it was just before the change, so a sighting with no saved inputs still has
its baseline, and record the result on the change. They print one line when
any now disagrees; a disagreeing fact reaches the Inbox as contradicted. A
change whose sightings cannot be read, or that the API will not resolve, stays
pending for the next run.

A fact change is replayed too (`replay_fact`): the sightings filled from the
fact, or answered by a pattern that names it, are answered with each attempt's
original facts and again with the fact changed, so only the change can make an
accepted answer differ. Your fact is saved either way; the counts go with it,
what disagrees is printed, and a failed replay puts the fact in the Inbox as
contradicted. `facts set`, `facts review`, the Inbox, and a fact given mid-run
all replay what they set.

**Stored prose.** A run saves no long-form text: `jobs_prose` holds old
reference rows, and the narrative library is the only approved writing
(`os hunt library get`, `/library`). The fallback drafter sees up to five
pieces of stored prose on similar questions — a wording comparison, best
first, cached for the run — as reference only; stored prose is never filled
as it stands. `os hunt prose list` and `os hunt prose delete` view and prune
the old rows.

## Repair

`jobs/repair/` is the repair lane (`docs/jobs-cli.md` describes using it).
On every apply (`--no-repair` turns it off), `AutofillSession.repair_in_place`
retries each field that
stopped at a format or a strategy on the live page, right after that step's
fill, on any site. `repairer.py` retries one failed field — every applicable
strategy for a `strategy` failure, every other rendering of the fact for a
`format` failure — and returns the first that reads back. `jobs/safety.py` holds
everything that keeps the worker from submitting: the two independent guards, a
page script (`core/page_scripts/submit_guard.js`, installed only from `safety.py`)
that cancels submissions and submit-labelled clicks and a network
route that aborts any non-GET request naming a submission, and the worker's own
refusal to operate a control that reads as submitting. A repair or validation
page is guarded from before it loads. Your own tab is guarded only while the
worker operates a field on it (`armed`): the script acts only while armed, and
the route exists only for the operation, so your Submit works the moment the
worker stops. Reaching the form (the Apply click, a gate, the account wall) is
never armed, since "Apply now" reads as submitting. The one press that is not
guarded is `jobs/submit.py`'s, below. `commands.py` is
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
(`jobs/registry/routes/`, one module per site, each registering itself with
`@route("site.com")`), then a link whose text is the role, the site's search box
with the full title and then shorter parts of it (without the parenthetical,
what follows a separator, the term), and a link into the site's open roles, a
few hops at most and never off the site. A link scores by the role's words,
and loses for missing the role's kind ("Intern"), the title's term, or for
words the title lacks, so a category link or another role is never taken.
What it cannot find is reported as `posting_not_found` with what the page
offered, and the run watches the browser until you open the posting, so a run
never ends because its URL was a careers home.

A posting that is gone ends the run instead (`PostingClosed`). That takes the
site's word: a closed notice on the page, read as the page first loads so a toast
that fades still counts and again while watching, or a search that proves it,
meaning every title query went in and stayed in the box, and the whole title's
search made the page say it found nothing where it had not before. A search that
failed, a box that dropped the query, or a board that already said "0 jobs" proves
nothing, and the row stays. A gone posting fills nothing and leaves the board as
`skipped`, so a resync cannot bring it back and `os hunt skipped restore` undoes it.

## How the run fixes itself

`jobs/fix/` is the fixer. A failure the lanes without code cannot settle — a
widget no kind captures, a strategy or rendering the repair lane ran out of,
an unreadable question, a wall, a posting the navigator cannot find — is one
problem, one failure-issue row, handed to a background thread (`queue.py`;
the thread is `background.py`, its attempts and records `recording.py`, over
the typed state in `base.py`).
Each attempt is one tool-less model call (`lane.py`) that answers with one new
file declaring one thing for the failing site under
`jobs/registry/<category>/learned/`, written only against `registry/api.py`
and named for what it declares — a readable snake_case stem
(`aria_slider.py`), site and goal for a route (`acme_com_form.py`). When a
file of the same structure for other sites already exists, the fixer first
tries just adding this site to its `sites=` (`source.py`) — checked, then
probed at the safe point like any candidate, no model call — and asks the
model to revise it only when that fails; a revision keeps every earlier site
and example.
The worker checks the file against `registry/policy.py`, then ruff, mypy, and
the file's embedded examples (`fix/examples.py`) — plus replay for formats and
question readers — in one subprocess (`gates.py`), tries it on the live page
(`probe.py`), and keeps it only on a readback: `keep.py` commits exactly that
file and its sanitized fixtures (`fix/sanitize.py` — the page's HTML and
controls, each file prefixed with its site, never the screenshot) on `main`,
pushes, records it with the failure it fixed, and `load_file` puts it into the
running run — no reload.
A fix you revert on the Hunt page is recorded as retired: from then on the
registry never loads its file, and a device that is online removes the file and
its fixtures in one commit on `main` and pushes (`fix/trial.py revert_retired`,
at the start of each run), skipping paths you have edited.

You can point it at a fix during the run. A direction (Send direction on a Live
fixes row, or a line typed at the terminal and ended with Enter, at any point in the
run) goes to `FixQueue.guide`, which adds it to that problem's
prompt, marked as coming from you rather than the page, and gives the problem three
more attempts. It reaches a problem under way, one left unsolved by this or an
earlier run, and one past the brake; a typed line goes to the latest problem. Typing
is all the terminal takes besides Ctrl+C, so no keystroke can mark or skip a run.
The prompt carries the page's text on arrival and now, and its trimmed markup,
because the call has no tools and cannot open the fixture files it is told about.

For a posting it cannot find, the model may answer `closed` with the page's exact
words. The worker checks they are on the page, and takes the row off the board only
if they are a closed notice or you sent a direction; its own reading of "0 jobs" is
never enough. The problem row is archived, and one row covers a site and company,
so one board's closed posting does not settle another's on the same host.

A problem spends at most three attempts in total across runs, plus three per
direction (the row's verification notes carry "attempts N of 3"); there is no cap
on problems, but three fully-unsolved problems in a row brake the fixer for the
rest of the run. An unsolved problem row stays unsolved until `os hunt fixes --retry`
puts it back on the queue or a new fixer API version reopens them all
(`registry.reopen_for_version`); before the reuse step and each later attempt
the queue asks the run to re-check the problem on the live page at a safe
point, on the run thread: answered becomes fixed, while gone — and anything
the run ends before its three attempts finish — is marked "later" and waits
for the next time it is seen. Only three real attempts make a problem
unsolved. `os hunt fixes` lists the rows (problem rows marked, attempts
shown); `os doctor` checks the fixer's readiness (`jobs/readiness.py`: ruff
and mypy under the workspace interpreter, `git ls-remote` without a prompt,
claude on PATH).

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

## Submitting: `--submit`

`os hunt apply <row> --submit` has the run press Next and Submit itself, through
`jobs/submit.py`, the only code that may. It is off by default, the device agent
and the Hunt page never pass it, and it refuses `--dry-run`, `--no-claude` and
`--remote`, since a run with no audit must not submit. It implies
`--accept-letter`, and a letter with an open `[slot]` holds the application.

On each step it waits for the fixer to settle, serving safe points (up to 15
minutes a problem), audits again, saves what the page holds, and presses the one
button that reads as Next or Submit; more than one, none, or a disabled one holds
the application. `blocking` decides what stops the press: every finding except an
optional short question with no fact (listed, never held), an optional widget
with no question, and an error the page shows on a field that holds an answer,
which waits for the press limit. A model that could not audit is never a pass.
The Fall 2027 date warning stops nothing.

| After the press | The run | Exit |
|---|---|---|
| A confirmation (`classify_application_page`) | Finishes as usual, recorded as applied | 0 |
| The next step of the form | Fills it, audits, presses again | |
| Validation errors | Audits again, presses again, three presses a step | 10 |
| A mailed code | Reads it through `jobs/mail.py`, enters it, three tries | 10 |
| An image puzzle | Parks; nothing ever touches one | 11 |
| An "I'm not a robot" box, before the press | Parks with the form filled; the tick is yours | 11 |
| Nothing changes, after Next | Holds | 10 |
| Nothing changes, after Submit | Unconfirmed | 12 |
| Moves on with no confirmation | Unconfirmed | 12 |

Exit 13 is a closed posting and 14 a run with no other ending. A wall in front of
the form, or a posting the navigator cannot find, holds once nothing is working
on it. Every ending but a confirmation
stops the run as `abandoned`, so the listing stays on the board, with no schema
change. A held run's tab closes and a retry refills it. A parked run keeps its
tab, and a rerun reuses it. An unconfirmed one keeps its tab too and is never
pressed again by itself: it may have gone in, so look, then `--done`.

Answers from a `--submit` run go into answer history like ones you submitted, so
replay treats them as accepted.

## The account wall

`jobs/account_inspect.py` recognizes an ATS email-first step (iCIMS: an email
box alone in its form, with Next or Continue), sign-in, create-account, forgot,
new-password, code or check-your-email page — Workday's `data-automation-id`s
first, wording elsewhere — and `jobs/account_page.py` clicks, fills and ticks
it. `page_state` reports it as `ACCOUNT` only when no
application form is on the page. `jobs/account.py` then runs a deterministic
chain, each step at most once: the email step (the email and its consent box,
then Next), Google (the `contact.email` tile in the
automation profile; any challenge falls through), sign in, create (a refused
sign-in creates first, because Workday says "wrong email or password" when
there is no account; "already exists" leads to the reset), verify (when the
sign-in page says to verify first, from the mail create sent, or after "Resend
Account Verification" for an account made earlier), and reset. Sign-in counts
only once the page has stayed off the wall for about two seconds, because a
tenant redirecting from create to its sign-in page shows nothing in between.
A Google popup closing itself is the site taking the sign-in, not a failure:
the flow waits for the page to leave the wall. An Apply click that opens a
sign-in ends the wait for a form at once and goes straight to this flow, so it
is never reported as a wall.
`jobs/mail.py` reads the verification or reset mail over IMAP, read-only
(EXAMINE and `BODY.PEEK[]`), taking only mail that arrived after the click and
names this tenant or company. Because a sender's name can be forged, the flow
follows a mailed link only to the exact host the wall is on (plain `https`, with
nothing in it that Python and the browser would parse differently), and types
nothing once the page has moved to another host; it never runs on Google's own
pages, and it
operates account controls only while `inspect_account` sees an account page. A
refused password, a mail timeout or a missing secret parks the run as an
`account_wall` failure, and the flow does not run again in that session. So does
an open captcha puzzle, in any frame, except that it is yours only while it is
open: the flow never solves or clicks one, and goes on once you have cleared it.
A visible checkbox widget counts as open for as long as it shows, so that sign-in
is yours. An invisible hCaptcha shows no puzzle unless the site asks for one, so
its badge alone stops nothing. There is no model call and nothing is stored.

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
never permission for the worker to skip it. Give useful information from
facts, writing composed from the narrative library, or a grounded draft. No
and N/A are valid answers only when the facts or the answer above establish
them; missing facts never imply No.
An unanswered field remains an outlined, actionable gap even when optional.

**Which value.** Every value comes from a fact. `jobs/resolve.py` builds the
run's `Knowledge` — the record facts and the computed career, derived, and
posting facts always win, while a stored row under one of their keys is ignored and stored
facts answer every other key: the derived ones in `jobs/facts/derived.py`
include the LGBTQ+
flag from transgender and orientation, the U.S.-person export statuses from
citizenship, the degree level, program level, and highest completed degree from
the degree type and enrollment, and the full-time start year after graduating.
`jobs/facts/career.py` derives the years of experience — experience and
research months, overlaps counted once, in half years — the same total
answers full-time questions. The resolver answers a field
in a fixed order: the question's active binding, then active expression
patterns (exactly one fact among those that match). Consent and "how did you
hear" are patterns too (R68), each an active expression reading a fact the
formatter knows: `consent.agrees` takes the one option that agrees, and
`preferences.application_source` ranks the employer's site, a job board, then
Other (a referral first under `--referred`; a free-text box gets the fact's
value, or "Employee referral" when referred). A typeahead nobody has opened is
chosen against its live menu by the same rule. A consent box is never put to a
model, and a password is never captured. The engine answers three kinds of
field itself (R68): the resume and transcript upload; a typed signature, signed
with `identity.legal_name` through its formatter (without that fact it is a gap
for you, never N/A); and a typed follow-up — "If yes…", "If you answered No…",
"If so…", required or optional — gets "N/A" when the question just above it
holds or is planned to hold the other answer. No value that declines to answer
is ever chosen.

**Posting facts.** Some facts belong to the posting, not to you, and are derived
per run in `jobs/facts/posting.py`: its title, whether you worked at or applied
to its company before, its term ("Summer 2027" in the title), your earliest start
(two weeks from accepting, your notice period, or the term's start if later:
winter January 4, spring January 11, summer May 18, fall August 31) and a
twelve-week window from it, whether that window runs into your last semester
(apply warns), whether your graduation date falls after the window's end
(`education.enrolled_through_internship`), and whether you were referred
(`apply --referred [NAME]`).
A pattern whose fact is missing or fits none of the options leaves the field,
and the run remembers why (`Miss`). A question no pattern decides goes to one
bounded model pass (`short_answers.py`) — shown every fact, with the facts whose
labels are closest to the question by embedding listed first (`rank-facts`;
labels go to the API, never values) — that only names a fact — `fits_fact`,
with one new expression branch for the wording where one fits, `no_fact` with the
missing fact's name, or `unsure`. The model never writes a value (R67):
`jobs/learn.py` checks what it named — the fact must fit the control, and a
branch must compile, nest no repetition in another, match the question or its
label without the trailing marks ("Gender *" is "Gender"), be anchored with
`^` and `$` when that stripped label is four words or fewer, decide the
question as that fact among the active patterns, and match every other
recorded question only where that question is answered from the same fact or
from nothing yet — a binding counts even where it would hide the branch —
else the question is bound on its own; a model never replaces a binding —
then replays it (above). If no
accepted answer changes for the worse, the session saves it active with origin
`model`, and the ordinary pattern pass fills the field in the same run. An
expression that changes one falls back, once, to binding the question alone,
replayed in turn; a lesson that still changes one is saved as a candidate with
its verification, waits in the Inbox as a pattern check, and is saved once a
run. The pattern records the run and question it answers, and the API keeps
the change in `jobs_changes`. An expression already waiting in the Inbox also
falls back to a checked binding of this question alone. If the answer check
finds a wrong worker answer but historical answers disagree with the correction,
the binding stays a candidate for review and is used only for this run; when
it does pass, the new binding replaces the one a model taught (the same check
never re-asks a pattern answer equal to one you already submitted). A
user-authored pattern or an existing binding is still never overridden.
The API refuses, too, any such pattern that would undo a decision of yours: one
you rejected or retired, a binding over an existing one, or a question you
marked one-off or long-form; and an expression already waiting in the Inbox. A question or gate a pattern decides never reaches
the model, even when its fact is missing. Every call sees every fact with its value.
A textarea whose question asks a fact — a name, a link, a date, a yes/no —
goes to that same fact pass (`is_factual_box`), not the prose drafter, and one
the pass leaves empty is still drafted. A question asking for a story —
`is_narrative_box`, a long-form box whose question reads as narrative — is
never filled by an expression, whose wording learned another question's;
a binding still fills it.

**How it is written.** `jobs/registry/formats/` renders a fact for the control in
front of it and names the formatter it used: a date as "December 2027", "Fall
2027" ("Winter 2027" for November and December), "December", "2027", ISO, US, or
month input, by the wording and the input type, or into a month-range option
("Jan - April 2027"), and a yes/no question naming a date range is checked
against the date itself; the $40/hour minimum as `$40/hour`, `40`, `$83,200 USD`,
or `83200`, in a menu of currencies as the fact's currency, in a menu of
periods as the fact's period, and against bands only at the magnitude they
count ("81-85k" is annual, never hourly); yes
and no as whichever option says them ("No, I am not a protected veteran");
numbers into the range that contains them ("3.75—4.0", "26-30k", "less than
25k", ">=2.74"), and a fractional number into the option its floor would get
when a whole-only menu matches it neither exactly nor as a range; the
half-year experience total also floors in a number input whose step is
whole or unset; lists as text, or one box per item.
A state is written as its postal code in a menu of codes ("Georgia" -> "GA")
and as its name in a menu of names; a yes/no question naming a state, your
school (by its full name or "Example University"), a country, or a term is answered
by comparing it to the fact, and a status menu for a different country takes
its None option.
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
seeded by the application. `jobs/focus.py` does the same for
`preferences.engineering_focus`, an unordered set: a question bound to it takes
the option the posting's title names, else one of your interests the menu
offers, else any real option, drawn with the application's seed; a "second
choice" takes the next of the same draw, and a free-text box gets that
interest's name.

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
give also binds the question to it and closes its issue. A record or computed
fact is never asked for; the refusal names what to change instead. Before any model call the audit runs the
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
logging their prompt, answer, or raw error payload: a failure names only its exit
code and the envelope's codes (`subtype`, `api_error`, the HTTP status), or says
no envelope came back. It also counts each call
under its lane: `pattern` (`short_answers.py`, the gate pass included), `code`
(the fixer), `audit` (`answer_check.py`) and `draft` (prose and the letter's
slots). Every step the run stores carries its pattern plus code calls so far as
`model_calls` and each lane's calls and tokens as `model_usage`, which the Hunt
Inbox's Learned tab shows per run; the run's closing lines print all four with
their tokens. No lane is capped, only each problem: two pattern asks per
question, one draft per question, one audit per answer, three code attempts per
problem. A usage limit still stops every call.
Only learning (pattern and code) counts toward `os hunt sites` marking a site `met`;
auditing and drafting run on every application.
`jobs/answer_check.py` is the other half: it reads back the answers the page
already holds and asks whether a fact contradicts one, because an answer can be
confidently filled and wrong. A wrong answer a pattern filled has its question
bound to the fact the model named and is refilled through the pattern path,
but only when that answer came from a pattern a model taught (never one you
made) and the fact renders differently; one the engine chose, and one you
typed, is reported and left alone. The audit signs a typed signature the way
the planner does and never from a pattern, and its pattern pass replays only
answers from a fact, never an upload or a consent click. Before and after each repair the audit squares every
follow-up with the answer just above it as the page holds it: an N/A the worker
wrote is taken back once that answer no longer rules the follow-up out (it says
otherwise, or its fill never landed), and an empty follow-up it rules out gets
N/A; an N/A you typed is yours. The audit looks, repairs, and looks again at most
twice, budgeted at 25 operations a question and 150 a run, then outlines exactly
the questions it lists — only what no lane could fix, each line naming its cause.
An error the form shows (`aria-invalid` and the message it names, a `role=alert`
beside one control, a browser constraint) is read by
`core/page_scripts/field_errors.js` and joins the audit in the page's own words:
a format error for a field a fact filled, a draft error for long-form, otherwise
a control error, so it takes those lanes and reaches the fixer. When the form
newly shows one, the audit runs again.

You own Next and Submit, unless the run was started with `--submit`; what you
type is captured as `manual` and, once the application goes in, waits in the
Inbox. A choice you make that a fact
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

**The registry layout.** `jobs/registry/` is where built-ins and learned
fixes share one layout, one package per category: `kinds/` classifies controls
and `kinds/custom.py` holds kinds a site taught; `strategies/` holds the
operations that operate each kind by family (native, combobox, upload) beside the candidate tables and `OPERATIONS`; `formats/` renders a
fact per family (dates, money, phone, choices, places, text) behind
`render`/`renderings`; `questions/` and `routes/` hold question readers and
per-site posting routes. `api.py` is the only module a learned file may import:
it declares what a fix can say (one frozen declaration per file) and the two
handles it sees a page through, both of which refuse to submit or sign in.

**Learned files.** A category's `learned/` package holds one file per
declaration, named for what it declares and shared across every site in its
`sites=` — `acme_com_form.py` for a route, `aria_slider.py` for a kind — kept
on trial until a later run proves it. `registry/policy.py` checks a file before it is
ever imported — only `re` and the API import, no private names, no escape
builtins, exactly one declaration naming its site — and `learned(category,
site)` returns a site's passing declarations in filename order, refused ones
kept in `refused()`. `load_file` checks and imports one kept fix without a
restart. Built-ins always go first: learned kinds capture after the custom
kinds in `inspect_fields`, learned question readers name radio groups with no
question, learned strategies run in `apply_autofill` and the repair lane only
once every built-in candidate has failed, learned formats render only when the
built-ins return None for a fact they name, and learned routes reach a posting
or form after the navigator and its routes give up. Every learned file carries
`examples` proving itself on HTML fixtures
(`workers/tests/fixtures/learned/<category>/<module>/`, each file prefixed
with the site it was captured from), run in the protected
`jobs/fix/examples.py` and kept honest by `tests/test_learned.py`; the
reference declarations the fixer's prompt shows live in
`fixtures/learned_examples/` and are run through the same policy.

**How it is operated.** `jobs/registry/` names what the fill path does. `kinds.py` classifies every
detected control into one of seven kinds — `text_entry`, `native_select`,
`choice_group`, `multi_choice_group`, `toggle`, `dynamic_choice`, `file` — from
generic HTML and ARIA alone, or into a custom kind. A custom kind
(`registry/kinds/learned/`, one module each, usually written by the fixer) teaches a
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
is open in any frame (its image or puzzle frame showing); a checkbox widget or an
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
