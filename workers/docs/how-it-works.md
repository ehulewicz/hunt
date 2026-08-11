# How workers work

The Jobs worker is a foreground Python 3.13 process for the user's own Windows/WSL
machine. `run.ps1` launches a dedicated visible Windows Chrome profile with a CDP
port, then starts the WSL worker. Pairing stores an expiring, revocable API token in
the local user's configuration with mode `0600`; the worker never connects to
PostgreSQL or Supabase directly.

For a preparation task the worker opens the direct company/ATS URL in visible
Chrome, captures the posting and visible form labels, fetches the approved `main`
resume/profile/answers from FastAPI, and calls subscription-authenticated
`claude -p`. Claude must return a minimal JSON Patch, not a replacement resume.

The guard allows only summary, experience/project bullet, and skill-text edits.
It preserves contact data, education, employers/projects, dates, section shape,
bullet counts, and skill categories; rejects more than a 10 percent word change;
and requires added keywords to be grounded in both the listing and known facts.
The worker compiles a one-page PDF, uploads it through FastAPI, and reports an
approval-required draft with a diff audit.

There is deliberately no submit action. Generic form autofill is a later step and
must use only approved answers/documents, remain visible, and stop before Submit.
