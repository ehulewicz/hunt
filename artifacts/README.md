# Portable artifacts

Durable user-facing files that move with the private Personal OS Git repository.
Everything here is tracked, so a clone is a complete working set.

## How it works

`career/main.json` is the **only** canonical resume. Never create a second
writable resume copy. The deterministic Python LaTeX compiler in `workers/`
renders it to `career/Alex_Example.pdf` with no model call and refuses anything
other than one page. Rebuilding unchanged JSON is byte-stable, so Git sees a PDF
change only when rendered content changes. The backend Docker image copies this
same `main.json` from the repository-root build context, so `POST /v1/bootstrap`
and local resume generation never diverge.

`career/cover_letter.md` is fixed prose with `{{slots}}` and named proof modules.
`os hunt apply` fills the listing facts, asks Claude for only the
posting-dependent slots, opens the result in VS Code, and then fills or uploads
whatever you left in the file. Edit the prose and modules here; its numbers should
match `main.json`.

`career/Transcript.pdf`, when present, is uploaded to any file control the autofill
worker recognizes as asking for a transcript (label or field name containing
"transcript"), the same way the resume is. It is optional — its absence just leaves
that field for you.

## Run and verify

```bash
os hunt resume build              # one-shot rebuild of the PDF
os hunt resume                    # = os hunt resume watch --open
```

Run the watch form while editing the JSON to keep the PDF current. It atomically
replaces the PDF and refreshes the browser preview whenever valid JSON changes; a
failed build leaves the last good PDF open. Resume content rules (no semicolons,
two rendered lines maximum, no keyword bolding, `|` between role and organization)
are in [`../AGENTS.md`](../AGENTS.md).

## Structure

```text
career/main.json              canonical structured resume — the single source
career/Alex_Example.pdf     its generated one-page projection, tracked beside it
career/Alex_Example.tex     build intermediate, untracked
career/cover_letter.md        cover letter template: prose, {{slots}}, proof modules
career/Cover_Letter.pdf       rendered cover letter
career/Transcript.pdf         optional, auto-uploaded to transcript file controls
career/defense.md             interview companion: what each compressed bullet
                              expands to, and what must not be overclaimed
```
