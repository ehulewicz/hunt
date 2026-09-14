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
os hunt list
os hunt today
os hunt answers list
os hunt resume watch --open
os hunt queue add 1
os hunt queue empty
os hunt apps
os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply failures list
```

Edit `artifacts/resume/main.json`; `os hunt resume watch --open` keeps the
Git-tracked `artifacts/resume/Alex_Example.pdf` beside it up to date.

The CLI launches local FastAPI and the dedicated native Windows Chrome profile
as needed. It never accesses Postgres directly and never submits an application.
See `../docs/jobs-cli.md` for the deterministic mapping, contextual Claude, and
failure-learning workflows.
