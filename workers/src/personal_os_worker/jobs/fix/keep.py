"""Keeping a verified candidate: the learned file and its fixtures, on main.

Exactly two paths are committed — the declaration under
`registry/<category>/learned/` and its sanitized fixture folder under
`tests/fixtures/learned/` — from a temporary index, so nobody else's staged or
unstaged work is touched. A refused push leaves the commit local and says so.
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

AUTHOR = {
    "GIT_AUTHOR_NAME": "ehulewicz",
    "GIT_AUTHOR_EMAIL": "eahtree@gmail.com",
    "GIT_COMMITTER_NAME": "ehulewicz",
    "GIT_COMMITTER_EMAIL": "eahtree@gmail.com",
}
ATTEMPTS = 3


class KeepError(RuntimeError):
    """The candidate could not be committed or pushed safely."""


@dataclass(frozen=True, slots=True)
class Kept:
    commit: str
    parent: str
    pushed: bool
    note: str = ""


def _git(repo: Path, *args: str, env: dict[str, str] | None = None,
         stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True,
        text=True, env={**os.environ, **(env or {})},
    )
    if check and process.returncode:
        raise KeepError(f"git {' '.join(args)} failed: {process.stderr.strip()}")
    return process


@contextmanager
def _ref_transaction(repo: Path, commit: str, parent: str) -> Iterator[None]:
    """Move main to `commit` only while everything succeeds; restore on failure."""
    try:
        yield
    except Exception:
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", "refs/heads/main", parent],
            capture_output=True,
        )
        raise


def _replace_index(index_path: Path, content: bytes) -> None:
    """Swap the live index only by rename, so a crash never leaves it half-written."""
    swap = index_path.with_name(index_path.name + ".tmp")
    swap.write_bytes(content)
    os.replace(swap, index_path)


def keep(
    repo: Path,
    message: str,
    attempt_dir: Path,
    learned_rel: str,
    fixture_rel: str,
    *,
    push: bool = True,
) -> Kept:
    """Commit exactly the learned file and its fixtures; main moves only if still."""
    learned_src = next(attempt_dir.rglob("learned/*.py"), None)
    if learned_src is None:
        raise KeepError("the attempt produced no learned file")
    fixtures_src = attempt_dir / "fixtures"
    learned_dst = repo / learned_rel
    fixtures_dst = repo / fixture_rel
    with tempfile.TemporaryDirectory(prefix="personal-os-fix-") as folder:
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
            parent = _git(repo, "rev-parse", "HEAD").stdout.strip()
            branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
            if branch != "main":
                raise KeepError(f"the checkout is on {branch!r}, not main")
            index = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
            _git(repo, "read-tree", parent, env=index)
            staged_tree = Path(folder) / "tree"
            staged_tree.mkdir()
            learned_dst.parent.mkdir(parents=True, exist_ok=True)
            fixtures_dst.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(learned_src, learned_dst)
            if fixtures_src.is_dir():
                for fixture in fixtures_src.iterdir():
                    if fixture.is_file():
                        shutil.copyfile(fixture, fixtures_dst / fixture.name)
            for rel in [learned_rel, *sorted(
                f"{fixture_rel}/{f.name}" for f in fixtures_dst.iterdir() if f.is_file()
            )]:
                _git(repo, "add", "--", rel, env=index)
            tree = _git(repo, "write-tree", env=index).stdout.strip()
            commit = _git(repo, "commit-tree", tree, "-p", parent,
                          env=AUTHOR, stdin=message).stdout.strip()
            installed = False
            try:
                with _ref_transaction(repo, commit, parent):
                    _git(repo, "update-ref", "refs/heads/main", commit)
                    installed = True
            except Exception:
                if not installed:
                    raise
        except (OSError, subprocess.SubprocessError) as error:
            raise KeepError(str(error)) from error
        finally:
            lock.unlink(missing_ok=True)
            if original_index is not None and installed:
                pass  # the index was only read; HEAD/staging untouched
    if not push:
        return Kept(commit, parent, False, "not pushed")
    pushed = _git(repo, "push", "-q", "origin", f"{commit}:refs/heads/main", check=False)
    if pushed.returncode != 0:
        return Kept(commit, parent, False,
                    f"committed on main, not pushed: {pushed.stderr.strip()}")
    return Kept(commit, parent, True)
