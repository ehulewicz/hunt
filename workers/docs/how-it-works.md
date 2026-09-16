# How workers work

`os` is the local Python 3.13 entry point. It holds a Google PKCE session,
starts local FastAPI on demand, and sends authenticated requests to that API.
FastAPI is the only process that accesses hosted Postgres.

`os hunt sync` parses the approved SpeedyApply feeds deterministically. The
database retains every valid row and its role-family tags; `os hunt list`
applies the user's technical-family view by default.

The resume compiler reads the canonical JSON, emits the Jake Gutierrez "Jake's
Resume" template as adapted for `bin/Sam_Kim_resume_dec2027.pdf` (11pt newtx
Times, small-caps section rules, role-first two-row entries), runs `latexmk`, and
verifies exactly one page with `pdfinfo`. Sections render in the fixed order
Education, Experience, Research, Projects, Leadership, Technical Skills, and a
section holding no entries emits neither label nor rule. That is what lets one
generator serve the canonical resume, which has Research and no Leadership, and
the Sam Kim reference fixture, which is the other way round. Experience,
Research, Projects, and Leadership keep separate structured lists so both
rendering and the reference fixture preserve the distinction. Watch mode compiles beside the stable output
and atomically replaces the PDF only after success so an open viewer never loses
its last good document.

There is no task worker any more. `os hunt queue add <rows>` records listings as
`queued` applications; `os hunt queue empty` runs `os hunt apply` through them
in order and stops after any that is not submitted, which stays queued. An
application that goes in is marked `applied`, and `os hunt apps <row> <status>`
carries it through `oa`, `oa_completed`, `interview`, `offer`, or `rejected`.
Resume tailoring was retired: the canonical resume already carries the breadth
it needs, and the effort goes into answers instead.

The regular, Today, and Easy listing views share backend row numbers assigned
after listings tagged `data_science` are removed from the board. Those source
rows remain stored and can still be addressed by ID prefix.

`os hunt apply <listing-ref>` attaches the tracked PDF as it is (compiling only
if it is missing; `os hunt resume build|watch` is where it changes), opens the stored
direct URL in native Windows Chrome, and then runs without a single terminal
prompt. Greenhouse, Ashby, and Lever adapters use fresh locators, native events,
strict option matching, readback, resume-first rerender handling, and same-origin
iframe inspection. Ashby renders a boolean question as two `aria-pressed`
buttons plus a hidden checkbox with no usable label; the worker reports it as
a `yesno` control with the sibling question text and `Yes`/`No` options,
clicks the matching button, and verifies `aria-pressed` rather than the
checkbox, plus `artifacts/career/Transcript.pdf` for a file control asking for a
transcript, when that file exists. Profile facts and exact library answers fill deterministically,
including selects, comboboxes, and radio groups matched by their question text.
Labels beginning with `Your` reuse a profile alias only when the remainder is
already known, so the three basic contact aliases work without broad fuzzy matching.
For a How did you hear choice, the deterministic planner selects the first
offered match in the user's standing order: Company Website, Job Board, then
Other, even if an older exact answer says something else.
A control that already holds a value is never planned by any tier: a react-select
counts as filled when its rendered choice is present even though its search
input is empty, and a free-entry typeahead with no option for the typed text is
committed with Enter.

`os hunt today` shows listings at most a day old; on it and on the board, `--ats greenhouse|ashby|lever|workday|generic`
narrows the view to one form type, detected from the direct application URL,
and the board shows that type in its `ats` column. `os hunt apply` with no row
applies the top board row on a supported ATS, or on the `--ats` given;
`os hunt apply today` does the same over today's rows.
The worker never selects a “Prefer not to answer,” “Decline,” or equivalent
choice; when no factual answer is available, that question remains for the user.
Consent, terms, privacy, and attestation controls are answered affirmatively
(decided 2026-09-14): a lone checkbox is checked, a choice group or select takes
its one agreeing option, and a typeahead is opened and its agreeing option picked
(`affirmative_option`), echoed as `consent <question> -> <option>`. A signature
stays manual. The same open-then-rank path answers a "How did you hear" typeahead
(`source`), and an employer's own site under any name ("DV Website", "Careers
page") counts as the company website. Custom radio groups recognize and preserve a
manually checked answer. Plain text controls receive focus and a real blur after
their input/change events so controlled ATS forms commit the displayed value.

Ashby's composite Education History control is read at the nested-field level:
School is searched by typing the profile value, Start/End Month and Year remain
distinct controls, and the education end date comes from the profile graduation
date. Empty Month... and Year... select placeholders are never treated as
answer options.

Before exact-answer reuse, a metadata-aware standing-policy tier can inspect
the control kind and visible options. Salary requirements use `$35/hour` (`35`
numeric), annual requirements use `$72,800 USD` (`72800` numeric), and acceptance
of a stated compensation range is always `Yes`. It distinguishes `Fall 2027`
from `December 2027`, maps 4.5 completed co-op terms and a two-week notice into
unique visible buckets, and selects a citizenship/nationality authorization
basis. Visa/work-permit expiration stays empty without a factual date. These
policies outrank obsolete exact answers; ordinary built-ins remain below profile
and exact answers.

`AnswerResolver.resolve(field)` is the common value-and-provenance interface
used by the planner. It returns `AnswerResolution(value, source, resolver_id)`
for source choices, profile facts, priority policies, exact library answers,
and dynamic fallback. The exact index is built once per plan; dynamic policies
still evaluate current profile, resume, company, and control options. The
result travels on `FormFill` through browser readback. Verified deterministic
and short-model fills retain provenance by step and control fingerprint, so
repeated labels do not overwrite each other's resolver. Verified long-form
seeds also store this per-control result before observation. Legacy string callbacks
remain supported; production pattern and short-model callbacks return typed
results. Long-form seeding still uses its existing exact/draft path.

Whatever is still unfilled after that goes through two more tiers, in order.
Library rows with `reuse_policy = 'pattern'` are case-insensitive regular
expressions matched against the question, holding standing policy answers
("consider me for other roles?" → Yes). Built-in pattern rules answer from
profile facts or the canonical resume for questions of a general shape
(preferred name, LinkedIn, school, degree, authorization, sponsorship, a
country-of-citizenship split from a sanctioned-country check, and "have you
worked here before" from whether the company appears on the resume). Exactly
one matching rule answers a question; zero or several leave it for the next
tier. A question about technologies, languages, frameworks, tools, or skills
you have experience with answers with the resume's whole Skills section; on a
checkbox group that means every box whose name is in the list (by name, never
by prefix, so `Go` does not match `Google Cloud`), and the boxes checked are
saved back as one comma-joined answer. Choice groups match the answer to the single option that equals,
starts with, is a prefix of, or contains every word of it, then a number to
the one range containing it, then a gender identity to its synonyms (`Male`
picks `Man`, `Cisgender man`, or `Man (he/him)`); several "Yes - …" options
against a bare "Yes" stay unfilled. Every pattern fill is echoed as
`pattern: <question> -> <value>`.

Whatever is still unfilled and not a textarea, consent, or file control goes
through one bounded Claude pass per page step: every remaining required text
input, select, typeahead (its menu is opened to read the options), and
unchecked radio/checkbox group becomes its own `claude -p` call, and the calls
run concurrently, each alongside the posting, the canonical resume, the
profile, and the full approved library (patterns included). Only `high`
confidence rows are kept, a choice answer must be one of the visible options
verbatim, and a free-text answer must pass the same fabrication guard used
for textarea drafts — which also rejects a draft carrying leaked tool-call
markup (`</answer>`, `</invoke>`, and the like), something `claude -p
--json-schema` occasionally leaves inside the schema's own string field
(`TAG_LEAK` in `answers.py`; `draft_slots` in `cover_letter.py` checks its
slots the same way). Kept answers fill the same way as the other tiers and
are echoed as `claude: <question> -> <value>`. `os hunt apply --no-claude`
skips this pass entirely.

The posting text these tiers see is the direct-URL page's visible text,
captured once at the start of `apply_posting` (the same capture already used
to verify the live page matches the intended company and role) — not
`jobs_postings.description_text`, which nothing populates. The canonical
December 2027 resume is always the one attached; graduation-date tailoring
(a posting that requires graduating later, and the May 2028 variant) is
parked in `grad_date/` with its own README and is not called.

Every long-form textarea is seeded directly on the page: an exact library
answer when one exists, otherwise a local `claude -p` draft grounded in the
posting, the canonical resume, the profile, and the most similar library
answers. A textarea that already holds text is kept, and one with nothing to
offer is left alone. Each is echoed as `long-form <question> -> <text> (library|claude draft)`,
and the step ends with a **Review before submitting** list of the drafted ones
so you can edit them in the browser. Claude is only ever called for a textarea
without an exact answer.

A cover letter is the one thing that opens an editor. When a page has a
cover-letter control (a textarea or file input whose label, name, or id says
"cover letter"), the worker loads `artifacts/career/cover_letter.md` — fixed
prose with `{{slots}}` plus named proof modules written from the resume —
fills `{{date}}`, `{{company}}`, and `{{role}}` from the listing, and makes one
bounded `claude -p` call for the posting-dependent slots: `hiring_manager`
(when the posting names one), `hook`, `why_this`, `tie`, and which module(s)
to use. Under `--no-claude`, or when the answer is unusable, the slots stay as
bracketed prompts. The rendered letter is written to
`state_dir()/cover-letters/<run>/cover_letter.md` and opened with `code --wait`;
closing the tab is the signal. Whatever the file then holds fills the textarea
(recorded for this application without automatic reuse approval) and, for a file input, is compiled
through the resume's LaTeX preamble and header into a one-page
`Alex_Example_Cover_Letter.pdf` and uploaded. The draft happens once per run;
a re-rendered step reuses the edited text.

The worker then polls explicit `form`, `overview`, `submitted`, `loading`, and
`unknown` states. Ashby's exact saved Overview URL is a standby state: one
message is printed and returning to the saved Application URL resumes the same
step without another fill or Claude pass. React rerenders and genuine next-step
transitions receive a five-second stabilization window. Submission is checked
before form controls because Ashby's success panel retains an invisible
reCAPTCHA textarea; a bare Submit button or the words submit/submission are not
success evidence. It polls once a second and snapshots every non-empty
control with its question and fingerprint, echoes anything the user typed or chose as `picked up`,
and reports when it sees a visible Submit control. It only reads: a `picked up`
line is the value the control held at that tick, so a field edited by hand
echoes once per intermediate value the poll happens to catch. A step ends when
the page's signature — its URL plus every control's fingerprint of tag, type,
name, label, and role — changes, which is why a label must carry the question
alone: Lever nests a structured question's whole field inside its label, so
reading that label wholesale would fold the typeahead's live suggestions into
the fingerprint and start a new step on every keystroke. Required-field completeness,
Next, and Submit are left to the user in the browser. Answer observations are
recorded for the application when the page moves on after Submit, when the user
presses Ctrl+S (or `s`) in the terminal, or when Ctrl+C stops the run. No run
action approves a reusable library answer. Ctrl+D (or `d`) skips the listing — that marks its application `skipped`, which
the board hides and a resync leaves alone; `os hunt skipped restore <n>` archives
the row so the listing comes back. Repeated questions and changed values stay
distinct in the observation log. The terminal is put in cbreak
mode with XOFF disabled for the run so Ctrl+S reaches the worker. For detected
submission, Ctrl+A, Ctrl+D, and Ctrl+C, answers and lifecycle changes persist
before only the application tab opened by this command closes. Chrome and
unrelated tabs remain; dry runs and unexpected worker errors keep the tab for
inspection. Closing Chrome or the last usable application tab also
ends the run cleanly and preserves answers captured so far. Approved library
answers can reuse exactly next time; generated text remains application-specific
until approved. The worker never clicks submission, signature, or
captcha controls; consent and attestation are filled deterministically and are
never put to Claude or drafted. After all tiers run, every required control still
empty is listed under **Left for you**. Failure fixes need a replay test and
headed live confirmation.

Application-question history has one observed row per changed control value,
identified by run and event index, plus final rows at run completion. Each row
records its step, page, control fingerprint, source, proposed value, and stable
resolver ID where one exists. Clearing a previously filled control is recorded
as an empty observation and removes it from the final values. A failed read
preserves the last known value. Manual edits retain the resolver and its mapped
proposal. These are last-observed values; the polling loop cannot guarantee it
sees a last-moment edit before submission. Model and pattern
answers in this history are not approved for future reuse.

`os hunt answers review --limit 50 --offset 0` reads a paginated review view of
changed proposals, model drafts, and manual answers. Each record includes its
resolver, control, proposal, observed value, and matching run-completion value
where available. Suggested actions ask for contextual review; they do not
classify an edit as a proven rule defect. This view never approves answers or
changes history. Persistent review dispositions are not implemented yet.
Automation failures remain available through `os hunt apply failures list/show`.

### Autofill phase timings

The loop prints content-free phase timings in milliseconds with step and
completion/interruption status. Phases cover form waiting, deterministic fill,
short drafting, long-form seeding, cover-letter review, user review, history
checkpoints, and evidence uploads. Samples remain available on
`session.timings.samples`; they are not persisted to the database. Form waiting,
user review, and cover-letter review may include human time. Upload and
checkpoint timings can be nested inside a larger phase, so totals must not add
parent and child durations. Instrumentation uses a monotonic clock and does not
change the session outcome or invoke a model.

## Host prerequisites

Two dependencies live on the host rather than in this repository. `uv sync`
resolves neither, and both fail in ways that do not name the cause, so check
them first on a new machine.

`os hunt` reaches native Windows Chrome over CDP at `http://127.0.0.1:9333`. WSL
defaults to `networkingMode=nat`, where WSL's localhost is not the Windows
localhost and every CDP call fails. Confirm with `wslinfo --networking-mode`. To
fix it, put the following in `C:\Users\<user>\.wslconfig` and then run
`wsl --shutdown` from PowerShell; the file alone changes nothing until WSL
restarts. A fresh Windows install has no `.wslconfig`, so this recurs on every
new device.

```ini
[wsl2]
networkingMode=mirrored
```

The resume compiler needs a system TeX installation. Beyond `latexmk` and the
base TeX Live packages it requires `newtx`, which Debian and Ubuntu ship in
`texlive-fonts-extra` rather than `texlive-fonts-recommended`. Without it
`pdflatex` stops at `File 'newtxtext.sty' not found`, and build and watch report
`Resume not updated` while leaving the previous PDF in place. Verify with
`kpsewhich newtxtext.sty`. The template is pinned to 11pt newtx Times to hold its
verified match against `bin/Sam_Kim_resume_dec2027.pdf`, so installing the font
is the fix and switching fonts is not.

Terminal persistence uses the API's atomic `/finish` operation for history and
application status. A missing endpoint or persistence conflict leaves the owned
application tab open for recovery. The worker no longer sends a second lifecycle
request after saving history.
