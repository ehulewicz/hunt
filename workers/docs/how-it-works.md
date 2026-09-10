# How workers work

`os` is the local Python 3.13 entry point. It holds a Google PKCE session,
starts local FastAPI on demand, and sends authenticated requests to that API.
FastAPI is the only process that accesses hosted Postgres.

`os hunt sync` parses the approved SpeedyApply feeds deterministically. The
database retains every valid row and its role-family tags; `os hunt listings`
applies the user's technical-family view by default.

The resume compiler reads the canonical JSON, emits a TeX Gyre Termes template
with Education, Experience, Research, Projects, and Technical Skills, runs
`latexmk`, and verifies exactly one page with `pdfinfo`. Experience, Research,
and Projects keep separate structured lists so both rendering and explicit
tailoring preserve the distinction. Watch mode compiles beside the stable output
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
