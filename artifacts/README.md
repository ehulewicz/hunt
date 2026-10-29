# Portable artifacts

Durable user-facing files that move with the private Personal OS Git repository.
Everything here is tracked, so a clone is a complete working set.

## How it works

The resume itself lives in the database as the career record (education,
experience, research, projects, bullets, skills; see `../backend/README.md`).
`career/main.json` and `career/Alex_Example.pdf` are its **exports**: every
accepted edit rewrites both. `main.json` is committed so a clone carries a
portable, versioned copy; the PDF is ignored by Git and rebuilt from the record
by `os hunt resume build`. Neither is read back as a source, and there is never a
second writable resume copy. The deterministic Python LaTeX compiler in
`workers/` renders the PDF with no model call and refuses anything other than one
page. Rebuilding an unchanged record is byte-stable, so Git sees a PDF change only
when rendered content changes.

`career/cover_letter.md` is fixed prose with `{{slots}}` and named proof modules.
`os hunt apply` fills the listing facts, asks Claude for only the
posting-dependent slots, opens the result in VS Code, and then fills or uploads
whatever you left in the file. Edit the prose and modules here; its numbers should
match the resume.

`career/long_form_questions.md` is the bank of base long-form questions you answer
once, in your own words: interests, direction, evidence, a story bank, each
experience and project, and short technical evidence notes. Application prose is
composed from these answers. Each prompt says which application questions it
serves.

`career/Transcript.pdf`, when present, is uploaded to any file control the autofill
worker recognizes as asking for a transcript (label or field name containing
"transcript"), the same way the resume is. It is optional — its absence just leaves
that field for you.

## Run and verify

```bash
os hunt resume                    # = os hunt resume edit --open
os hunt resume build              # republish both exports from the record
os hunt resume import             # once: load main.json into an empty record
```

`edit` opens a working copy of the record as JSON (outside the repository) in VS
Code. Each save goes to the API, which refuses a semicolon, keyword emphasis, an
em dash in a heading, or a header that disagrees with the facts; an accepted save
rewrites `main.json` and atomically replaces the PDF, and the browser preview
refreshes. A refused save changes nothing and says why. Resume content rules
(no semicolons, two rendered lines maximum, no keyword bolding, `|` between role
and organization) are in [`../AGENTS.md`](../AGENTS.md).

## Structure

```text
career/main.json              JSON export of the career record (never edited here)
career/Alex_Example.pdf     its generated one-page projection, ignored by Git
career/Alex_Example.tex     build intermediate, untracked
career/cover_letter.md        cover letter template: prose, {{slots}}, proof modules
career/Cover_Letter.pdf       rendered cover letter
career/long_form_questions.md base long-form questions to answer once, and what each serves
career/Transcript.pdf         optional, auto-uploaded to transcript file controls
career/defense.md             interview companion: what each compressed bullet
                              expands to, and what must not be overclaimed
```
