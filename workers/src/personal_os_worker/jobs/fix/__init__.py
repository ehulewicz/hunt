"""The fixer: a run that falls short writes, checks, and keeps the code it was missing.

When a failure outlasts every lane that needs no code — the repair lane's other
strategies and renderings, the model naming a fact, the navigator — the run
hands it to the fixer (`queue.py`) and goes on filling. The fixer, one failure
at a time, in a background thread:

1. asks a model for the whole source of ONE new file — a declaration written
   only against `registry/api.py` (`lane.py`) — or, when a same-structure file
   exists, edits that file's `sites=` in an attempt folder of its own instead
   of calling the model; a revision keeps every earlier site and example;
2. checks it in subprocesses — `registry/policy.py`, ruff, mypy, the file's
   embedded examples on sanitized page fixtures (`examples.py`), replay for
   formats and question readers;
3. waits for the run's next safe point, then tries it on the live control over
   CDP from a separate process (`probe.py`): it counts only when the value
   reads back, or the route reaches the posting or the form;
4. keeps it (`keep.py`): commits exactly the file and its sanitized fixtures
   (`sanitize.py`) on `main` without touching uncommitted work, pushes, records
   the change with the failure it fixed, and loads it into the running run.

A step that fails goes back to the model with what failed, at most three
attempts; then the issue is unsolved, outlined on the page, and in your Inbox.
A model never writes a value into a form here either: it writes code, and the
code fills the field from a fact like every other field.
"""
