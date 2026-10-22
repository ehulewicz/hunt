"""Keeping a fix: commit exactly its hunks on `main`, put them in the checkout, push.

Several sessions share this checkout, and any of them may have work staged or
unstaged. The commit is built in a temporary index from `HEAD` plus the fix, so
nothing else is swept into it, and `main` moves only if it is still where the
commit was built from. The same patch is then applied to the working tree and
the same patch is merged into the real index, preserving the staging of other
edits. The real index and branch are locked while the checkout is updated.
A push that is refused (the remote moved on) leaves the commit on `main` locally;
a device runs local commits as they are.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def _ref_transaction(repo: Path, commit: str, parent: str) -> Iterator[None]:
    """Lock main with Git's own compare-and-swap transaction before touching files."""
    with subprocess.Popen(
        ["git", "update-ref", "--stdin"], cwd=repo, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        incoming, outgoing = process.stdin, process.stdout

        def command(text: str, expected: str) -> None:
            incoming.write(text + "\n")
            incoming.flush()
            if outgoing.readline().strip() != expected:
                raise KeepError("main moved or could not be locked while keeping the fix")

        try:
            command("start", "start: ok")
            command(f"update refs/heads/main {commit} {parent}\nprepare", "prepare: ok")
            yield
            command("commit", "commit: ok")
        finally:
            # EOF aborts any transaction that did not commit, releasing its locks.
            process.stdin.close()
            process.wait(timeout=120)


def _replace_index(index: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=index.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, index)
    finally:
        temporary.unlink(missing_ok=True)


def keep(repo: Path, diff: str, message: str, *, push: bool = True) -> Kept:
    """Keep only the fix, preserving the contents and staging of everyone else's work."""
    with tempfile.TemporaryDirectory(prefix="personal-os-fix-") as folder:
        patch = Path(folder) / "fix.patch"
        patch.write_text(diff, encoding="utf-8")
        index_path = Path(_git(repo, "rev-parse", "--path-format=absolute",
                               "--git-path", "index").stdout.strip())
        lock = index_path.with_name(index_path.name + ".lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise KeepError("another session is updating the index; try again later") from error
        os.close(descriptor)
        try:
            original_index = index_path.read_bytes()
            staged_index = Path(folder) / "staged-index"
            shutil.copyfile(index_path, staged_index)
            # Apply the fix to the original staged contents, rather than resetting
            # touched paths to HEAD and silently unstaging someone else's hunks.
            staged = {"GIT_INDEX_FILE": str(staged_index)}
            if _git(repo, "apply", "--cached", str(patch), env=staged,
                    check=False).returncode:
                raise KeepError("the fix does not apply to the staged files")
            if _git(repo, "apply", "--check", str(patch), check=False).returncode:
                raise KeepError("the fix does not apply to the files as they are in the checkout")
            for _attempt in range(ATTEMPTS):
                branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
                if branch != "main":
                    raise KeepError(f"the checkout is on {branch!r}, not main")
                parent = _git(repo, "rev-parse", "HEAD").stdout.strip()
                index = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
                _git(repo, "read-tree", parent, env=index)
                if _git(repo, "apply", "--cached", str(patch), env=index,
                        check=False).returncode:
                    raise KeepError("the fix does not apply to the committed files")
                tree = _git(repo, "write-tree", env=index).stdout.strip()
                commit = _git(repo, "commit-tree", tree, "-p", parent,
                              env=AUTHOR, stdin=message).stdout.strip()
                applied = False
                installed = False
                try:
                    with _ref_transaction(repo, commit, parent):
                        # Git apply is atomic unless --reject is requested. A
                        # failed apply therefore leaves both main and files alone.
                        _git(repo, "apply", str(patch))
                        applied = True
                        _replace_index(index_path, staged_index.read_bytes())
                        installed = True
                    break
                except Exception:
                    if installed:
                        _replace_index(index_path, original_index)
                    if applied:
                        _git(repo, "apply", "--reverse", str(patch))
                        raise
                    if _git(repo, "rev-parse", "HEAD").stdout.strip() == parent:
                        raise
            else:
                raise KeepError("main kept moving while the fix was being committed")
        except (OSError, subprocess.SubprocessError) as error:
            raise KeepError(str(error)) from error
        finally:
            lock.unlink(missing_ok=True)
    if not push:
        return Kept(commit, parent, False, "not pushed")
    try:
        pushed = _git(repo, "push", "-q", "origin", f"{commit}:refs/heads/main", check=False)
    except (OSError, subprocess.SubprocessError, KeepError) as error:
        return Kept(commit, parent, False, f"committed on main, not pushed: {error}")
    if pushed.returncode != 0:
        return Kept(commit, parent, False,
                    "committed on main, not pushed: " + pushed.stderr.strip()[:300])
    return Kept(commit, parent, True)
