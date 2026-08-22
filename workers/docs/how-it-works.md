# How workers work

`os` is the local Python 3.13 entry point. It holds a Google PKCE session,
starts local FastAPI on demand, and sends authenticated requests to that API.
FastAPI is the only process that accesses hosted Postgres.

`os hunt sync` parses the approved SpeedyApply feeds deterministically. The
database retains every valid row and its role-family tags; `os hunt listings`
applies the user's technical-family view by default.

The resume compiler reads the canonical JSON, emits the recovered LaTeX
template, runs `latexmk`, and verifies exactly one page with `pdfinfo`. Watch
mode compiles beside the stable output and atomically replaces the PDF only after
success so an open viewer never loses its last good document.

Preparation captures the real application form in visible native Windows Chrome.
The default candidate is the canonical resume unchanged. `--tailor` alone calls
`claude -p`; the existing fact, structure, 10 percent, and one-page validators
still decide whether the result is usable.

Autofill has two providers. `personal-os` fills only deterministic Profile facts,
the chosen resume, and exact approved answers. `lentra` pauses for the user to
trigger the Chrome extension and then audits only field presence. Both leave the
headed page open and have no submit behavior.
