# Portable artifacts

Durable user-facing files that move with the private Personal OS Git repository.
Everything here is tracked, so a clone is a complete working set.

## How it works

The resume itself lives in the database as the career record (education,
experience, research, projects, bullets, skills; see `../backend/README.md`).
The career record is edited at /resume/edit or with `os hunt resume get|put`;
the backend builds its one-page PDF on each save and serves it at /resume, and
a run downloads that PDF for uploads. There is no committed JSON export or
locally built PDF — never a second writable resume copy.

`career/cover_letter.md` is the fixed-prose template with `{{slots}}` and named
proof modules. `os hunt apply` asks Claude for only the posting-dependent
slots, and the filled letter is reviewed on the Hunt page (or taken as is with
--accept-letter when no [slot] prompt is left); the backend builds its PDF.
Edit the prose and modules here; its numbers should match the resume.

`career/long_form_questions.md` is the bank of base long-form questions:
interests, direction, evidence, a story bank, each experience and project, and
short technical notes. It is loaded once into the narrative library with
`os hunt library import`, and deleted after that import; the library in the
database is what long-form answers are composed from.

`career/Transcript.pdf`, when present, is uploaded to any file control the autofill
worker recognizes as asking for a transcript (label or field name containing
"transcript"), the same way the resume is. It is optional — its absence just leaves
that field for you.

## Run and verify

```bash
os hunt resume get                # the record's document as JSON
os hunt resume put FILE           # validate and save a document
```

A `put` goes to the API, which refuses a semicolon, keyword emphasis, an
em dash in a heading, or a header that disagrees with the facts; a refused save
changes nothing and says why. Resume content rules
(no semicolons, two rendered lines maximum, no keyword bolding, `|` between role
and organization) are in [`../AGENTS.md`](../AGENTS.md).

## Structure

```text
career/cover_letter.md        cover letter template: prose, {{slots}}, proof modules
career/Cover_Letter.pdf       rendered cover letter
career/long_form_questions.md base long-form questions, loaded once into the library
career/Transcript.pdf         optional, auto-uploaded to transcript file controls
career/defense.md             interview companion: what each compressed bullet
                              expands to, and what must not be overclaimed
```
