# Portable artifacts

This directory contains durable user-facing files that should move with the
private Personal OS Git repository.

`career/main.json` is the canonical structured resume.
`career/Alex_Example.pdf` is its deterministically generated, one-page
projection. Run `os hunt resume watch --open` while editing the JSON to keep the
PDF current. Rebuilding unchanged JSON is byte-stable, so Git only sees a PDF
change when rendered content changes.

`career/cover_letter.md` is the cover letter: fixed prose with `{{slots}}` and
named proof modules. `os hunt apply` fills the listing facts, asks Claude for
the posting-dependent slots, opens the result in VS Code, and fills or
uploads what you leave in the file. Edit the prose and modules here; the
numbers should match `main.json`.

`career/Transcript.pdf`, when present, is uploaded to any file control the
autofill worker recognizes as asking for a transcript (label or field name
containing "transcript"), the same way the resume is. Optional: its absence just
leaves that field for you.
