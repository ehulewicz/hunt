"""Keeping a fix: commit exactly its hunks on `main`, put them in the checkout, push.

Several sessions share this checkout, and any of them may have work staged or
unstaged. The commit is built in a temporary index from `HEAD` plus the fix, so
nothing else is swept into it, and `main` moves only if it is still where the
commit was built from. The same patch is then applied to the working tree and
those paths' index entries follow the new `HEAD`, which leaves anyone else's
edits to those files in place as edits. A push that is refused (the remote moved
on) leaves the commit on `main` locally and says so; a device runs local commits
as they are.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

AUTHOR = {"GIT_AUTHOR_NAME": "ehulewicz", "GIT_AUTHOR_EMAIL": "eahtree@gmail.com",
          "GIT_COMMITTER_NAME": "ehulewicz", "GIT_COMMITTER_EMAIL": "eahtree@gmail.com"}
ATTEMPTS = 3


class KeepError(RuntimeError):
    """The fix could not be put on `main` as it stands."""


@dataclass(frozen=True, slots=True)
class Kept:
    commit: str
    parent: str
    pushed: bool
    note: str = ""


def _git(repo: Path, *args: str, env: dict[str, str] | None = None,
         stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    process = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env=environment,
        input=stdin, timeout=120,
    )
    if check and process.returncode != 0:
        raise KeepError(f"git {args[0]}: {(process.stderr or process.stdout).strip()[:500]}")
    return process


def _paths(patch: Path, repo: Path) -> list[str]:
    listed = _git(repo, "apply", "--numstat", str(patch)).stdout
    return [line.split("\t", 2)[2] for line in listed.splitlines() if line.count("\t") >= 2]


def keep(repo: Path, diff: str, message: str, *, push: bool = True) -> Kept:
    """Commit `diff` on `main`, apply it to the checkout, and push; raise when it cannot."""
    with tempfile.TemporaryDirectory(prefix="personal-os-fix-") as folder:
        patch = Path(folder) / "fix.patch"
        patch.write_text(diff, encoding="utf-8")
        branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
        if branch != "main":
            raise KeepError(f"the checkout is on {branch!r}, not main")
        if _git(repo, "apply", "--check", str(patch), check=False).returncode != 0:
            raise KeepError("the fix does not apply to the files as they are in the checkout")
        paths = _paths(patch, repo)
        for _attempt in range(ATTEMPTS):
            parent = _git(repo, "rev-parse", "HEAD").stdout.strip()
            index = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
            _git(repo, "read-tree", parent, env=index)
            if _git(repo, "apply", "--cached", str(patch), env=index, check=False).returncode:
                raise KeepError("the fix does not apply to the committed files")
            tree = _git(repo, "write-tree", env=index).stdout.strip()
            commit = _git(
                repo, "commit-tree", tree, "-p", parent, env=AUTHOR, stdin=message
            ).stdout.strip()
            # Only if main is still where the commit was built from.
            moved = _git(repo, "update-ref", "refs/heads/main", commit, parent, check=False)
            if moved.returncode == 0:
                break
        else:
            raise KeepError("main kept moving while the fix was being committed")
        _git(repo, "apply", str(patch))
        _git(repo, "reset", "-q", "HEAD", "--", *paths)
    if not push:
        return Kept(commit, parent, False, "not pushed")
    pushed = _git(repo, "push", "-q", "origin", "main", check=False)
    if pushed.returncode != 0:
        return Kept(commit, parent, False,
                    "committed on main, not pushed: " + pushed.stderr.strip()[:300])
    return Kept(commit, parent, True)
