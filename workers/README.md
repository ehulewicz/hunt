# Personal OS local CLI

Install the Python 3.13 CLI in editable mode:

```bash
cd /home/eahtr/personal-os
UV_CACHE_DIR=/tmp/personal-os-uv-cache uv tool install --editable ./backend
UV_CACHE_DIR=/tmp/personal-os-uv-cache uv tool install --editable ./workers
```

The installed entry point is `os`. Jobs commands live under `os hunt`:

```bash
os doctor
os hunt sync
os hunt            # list
os hunt 20         # list 20
os hunt today
os hunt answers list
os hunt facts review
os hunt resume
os hunt queue add 1
os hunt queue empty
os hunt apps
os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply --ats greenhouse
os hunt apply failures list
```

The resume is the career record in the database. `os hunt resume` (= `resume
edit --open`) opens a working copy as JSON in VS Code and sends each save to the
API; an accepted save republishes the exports `artifacts/career/main.json`
(committed) and `Alex_Example.pdf` (generated, ignored by Git). `resume build` republishes
them from the record, `resume build --input FILE` compiles a file without
touching the record, and `resume import` loads `main.json` into an empty record
once. Facts the resume determines — school, degree, graduation, GPA, whole years
of experience, the current employer, every organization, every skill — are
derived from the record by `jobs/facts/derived.py`; a stored fact with the same
key still wins until the facts session removes it, and `os hunt facts` marks
each one that disagrees with the record.

The CLI launches local FastAPI and the dedicated native Windows Chrome profile
as needed. It never accesses Postgres directly and never submits an application.
See `../docs/jobs-cli.md` for the deterministic mapping, contextual Claude, and
failure-learning workflows.

## How a field is filled

`jobs/registry/` names what the fill path does. `kinds.py` classifies every
detected control into one of seven kinds — `text_entry`, `native_select`,
`choice_group`, `multi_choice_group`, `toggle`, `dynamic_choice`, `file` — from
generic HTML and ARIA alone; a group of `aria-pressed` buttons is found by that
state, not by any site's class names. `strategies.py` lists each kind's candidate
strategies in order, and `browser.OPERATIONS` performs them: candidates are tried
until one operates, then the value is read back. `events.py` turns every attempt
into a fill event (control, strategy, resolver, value hash, and where it stopped)
that the step sends to FastAPI; a dry run sends one not-attempted event per
question. A choice is refused, and left to you, only while an interactive captcha
is on the page; an invisible-mode reCAPTCHA badge does not count.
