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
For a How did you hear choice, the deterministic planner selects the first
offered match in the user's standing order: Company Website, Job Board, then
Other, even if an older exact answer says something else.
A control that already holds a value is never planned by any tier: a react-select
counts as filled when its rendered choice is present even though its search
input is empty, and a free-entry typeahead with no option for the typed text is
committed with Enter.

`os hunt today` shows listings at most a day old; on it and on the board, `--ats greenhouse|ashby|lever|workday|generic`
narrows the view to one form type, detected from the direct application URL,
and the board shows that type in its `ats` column. `os hunt apply today` applies
the first listing in that order on a supported ATS, or on the `--ats` given.
The worker never selects a “Prefer not to answer,” “Decline,” or equivalent
choice; when no factual answer is available, that question remains for the user.
Consent stays manual, while custom radio groups now recognize and preserve a
manually checked answer. Plain text controls receive focus and a real blur after
their input/change events so controlled ATS forms commit the displayed value.

Ashby's composite Education History control is read at the nested-field level:
School is searched by typing the profile value, Start/End Month and Year remain
distinct controls, and the education end date comes from the profile graduation
date. Empty Month... and Year... select placeholders are never treated as
answer options.

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
for textarea drafts. Kept answers fill the same way as the other tiers and
are echoed as `claude: <question> -> <value>`. `os hunt apply --no-claude`
skips this pass entirely.

The posting text these tiers see is the direct-URL page's visible text,
captured once at the start of `apply_posting` (the same capture already used
to verify the live page matches the intended company and role) — not
`jobs_postings.description_text`, which nothing populates. The same text
feeds one more bounded call, `resume_selection.requires_later_graduation`:
the canonical resume states graduating December 2027; a posting that
explicitly requires graduating later (a minimum class year, "must graduate
after X") gets `resume.later_graduation_resume_pdf()` attached instead — a
variant with only the graduation date changed, compiled on demand and cached
under `state_dir()/resume-variants/`, rebuilt when the canonical source
changes — and the resume facts passed to the other tiers are patched to
match. `--no-claude` skips this too, since it is a `claude -p` call like the
others. This never changes how an explicit "expected graduation date" form
question is answered; that stays the true date from the profile,
deterministically.

Every long-form textarea is seeded directly on the page: an exact library
answer when one exists, otherwise a local `claude -p` draft grounded in the
posting, the canonical resume, the profile, and the most similar library
answers. A textarea that already holds text is kept, and one with nothing to
offer is left alone. Each is echoed as `long-form <question> -> <text> (library|claude draft)`,
and the step ends with a **Review before submitting** list of the drafted ones
so you can edit them in the browser. Claude is only ever called for a textarea
without an exact answer.

The worker then polls the page. It snapshots every non-empty
control by its question, echoes anything the user typed or chose as `picked up`,
and reports when it sees a visible Submit control. Required-field completeness,
Next, and Submit are left to the user in the browser. Answers are saved to the
library as approved when the page moves on after Submit, when the user presses
Ctrl+S (or `s`) in the terminal, or when Ctrl+C stops the run; a later save of
the same question overwrites the earlier value. The terminal is put in cbreak
mode with XOFF disabled for the run so Ctrl+S reaches the worker. Closing Chrome or the last usable application tab also
ends the run cleanly and preserves answers captured so far. Short answers reuse
exactly next time; textarea answers are stored as contextual so the next draft
can be tailored again. The worker never clicks submission, consent, attestation,
or captcha controls, and those consent labels are the only ones excluded from
filling. Failure fixes still require explicit worktree mutation, replay tests, a
user-created commit, and headed live confirmation.

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
