# Portable artifacts

This directory contains durable user-facing files that should move with the
private Personal OS Git repository.

`resume/main.json` is the canonical structured resume. `resume/Alex_Example.pdf` is its
deterministically generated, one-page projection. Run `os hunt resume watch
--open` while editing the JSON to keep the PDF current. Rebuilding unchanged
JSON is byte-stable, so Git only sees a PDF change when rendered content changes.

`Transcript.pdf`, when present, is uploaded to any file control the autofill
worker recognizes as asking for a transcript (label or field name containing
"transcript"), the same way the resume is. Optional: its absence just leaves
that field for you.
