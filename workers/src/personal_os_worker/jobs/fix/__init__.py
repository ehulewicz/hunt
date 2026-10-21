"""The fixer: a run that falls short writes, tests, and keeps the code it was missing.

When a failure outlasts every lane that needs no code — the repair lane's other
strategies and renderings, the model naming a fact, the navigator — the run
hands it to the fixer and goes on filling. The fixer, one failure at a time, in
a background thread:

1. clones the repository into a scratch directory (`scratch.py`) and writes the
   live page's evidence in as a test fixture;
2. asks a model to make the smallest code change that solves it and to add a
   regression test against that fixture (`lane.py`), editing only the scratch
   clone, only worker code, and never the safety denylist;
3. runs ruff, mypy, and the whole worker test suite on the scratch code, then
   replays it against the answers you accepted;
4. at the run's next safe point, tries it on the live control from a separate
   process running the scratch code, with the submit guards armed
   (`probe.py`); it counts only when the value reads back, or the route reaches
   the posting or the form;
5. keeps it (`keep.py`): commits exactly its hunks on `main` without touching
   anyone else's uncommitted work, pushes, records the change with its
   verification and the failure it fixed, and marks the issue fixed. The run
   then reloads on the same tab with the new code and carries on.

A step that fails goes back to the model with what failed, at most three
attempts; then the issue is unsolved, outlined on the page, and in your Inbox.
A model never writes a value into a form here either: it writes code, and the
code fills the field from a fact like every other field.
"""
