# Portable artifacts

This directory contains durable user-facing files that should move with the
private Personal OS Git repository.

`resume/main.json` is the canonical structured resume. `resume/main.pdf` is its
deterministically generated, one-page projection. Run `os hunt resume watch
--open` while editing the JSON to keep the PDF current. Rebuilding unchanged
JSON is byte-stable, so Git only sees a PDF change when rendered content changes.
