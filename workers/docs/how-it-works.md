# How workers work

`os` is the local Python 3.13 entry point. It holds a Google PKCE session,
starts local FastAPI on demand, and sends authenticated requests to that API.
FastAPI is the only process that accesses hosted Postgres.

`os hunt sync` parses the approved SpeedyApply feeds deterministically. The
database retains every valid row and its role-family tags; `os hunt listings`
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
rendering and explicit tailoring preserve the distinction. Watch mode compiles beside the stable output
and atomically replaces the PDF only after success so an open viewer never loses
its last good document.

Preparation captures the real application form in visible native Windows Chrome.
The default candidate is the canonical resume unchanged. `--tailor` alone calls
`claude -p`; the existing fact, structure, 10 percent, and one-page validators
still decide whether the result is usable.

`os hunt autofill <listing-ref>` rebuilds the canonical PDF and opens the stored
direct URL in native Windows Chrome. Greenhouse, Ashby, and Lever adapters use
fresh locators, native events, strict option matching, readback, resume-first
rerender handling, and same-origin iframe inspection. Exact answers and Profile
facts are deterministic. Contextual or unmatched long-form fields alone may call
local `claude -p`, with fact/voice hygiene and a 20 percent base-answer guard.

The worker may click one unambiguous Apply or Next/Continue control after context
and final field values are captured. It never clicks submission, consent, or
captcha controls. When navigation is missing or ambiguous, it reports the wall
and keeps the session alive for manual advancement. User-approved final textarea
values persist through FastAPI. Failure fixes require explicit worktree mutation,
replay tests, a user-created commit, and headed live confirmation.

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
