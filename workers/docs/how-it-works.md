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

`os hunt apply <listing-ref>` attaches the tracked PDF as it is (compiling only
if it is missing; `os hunt resume build|watch` is where it changes), opens the stored
direct URL in native Windows Chrome, and then runs without a single terminal
prompt. Greenhouse, Ashby, and Lever adapters use fresh locators, native events,
strict option matching, readback, resume-first rerender handling, and same-origin
iframe inspection. Ashby renders a boolean question as two `aria-pressed`
buttons plus a hidden checkbox with no usable label; the worker reports it as
a `yesno` control with the sibling question text and `Yes`/`No` options,
clicks the matching button, and verifies `aria-pressed` rather than the
checkbox. Profile facts and exact library answers fill deterministically,
including selects, comboboxes, and radio groups matched by their question text.
A control that already holds a value is never planned by any tier: a react-select
counts as filled when its rendered choice is present even though its search
input is empty, and a free-entry typeahead with no option for the typed text is
committed with Enter.

Whatever is still unfilled after that goes through two more tiers, in order.
Library rows with `reuse_policy = 'pattern'` are case-insensitive regular
expressions matched against the question, holding standing policy answers
("consider me for other roles?" → Yes). Built-in pattern rules answer from
profile facts or the canonical resume for questions of a general shape
(preferred name, LinkedIn, school, degree, authorization, sponsorship, a
country-of-citizenship split from a sanctioned-country check, and "have you
worked here before" from whether the company appears on the resume). Exactly
one matching rule answers a question; zero or several leave it for the next
tier. Choice groups match the answer to the single option that equals,
starts with, is a prefix of, or contains every word of it; several "Yes - …"
options against a bare "Yes" stay unfilled. Every pattern fill is echoed as
`pattern: <question> -> <value>`.

Whatever is still unfilled and not a textarea, consent, or file control goes
through one bounded Claude pass per page step: every remaining required text
input, select, and unchecked radio/checkbox group becomes one question in a
single `claude -p` call alongside the posting, the canonical resume, the
profile, and the full approved library (patterns included). Only `high`
confidence rows are kept, a choice answer must be one of the visible options
verbatim, and a free-text answer must pass the same fabrication guard used
for textarea drafts. Kept answers fill the same way as the other tiers and
are echoed as `claude: <question> -> <value>`. `os hunt apply --no-claude`
skips this pass entirely.

Every long-form textarea becomes a Markdown file under the state directory and
opens as its own VS Code tab through `code --wait`. The text above the divider is
the seed: an exact library answer when one exists, otherwise a local `claude -p`
draft grounded in the posting, the canonical resume, the profile, and the most
similar library answers, otherwise blank. Below the divider sit the question, the
company and role, where the seed came from, and the similar answers for
reference. Closing the tab fills the textarea with whatever is above the divider;
an empty top section leaves the field alone. Claude is only ever called for a
textarea without an exact answer.

After the tabs close the worker polls the page. It prints the required fields
still empty and waits for the user to fill them on the page. Once none remain it
follows the single safe Next/Continue control if one exists, and otherwise keeps
watching until the user presses Submit and the page leaves the form. On every
poll it snapshots every non-empty control on the page by its question and echoes
anything the user typed or chose as `picked up`. Answers are saved to the
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
