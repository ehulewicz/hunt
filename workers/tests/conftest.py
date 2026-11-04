"""Every test's model call is stubbed: none may reach the real `claude` CLI.

`subprocess.run` and the asyncio subprocesses both go through
`subprocess.Popen`, so guarding it catches every path — a runner left
unstubbed fails its test, and a call made on a background thread is still
recorded and fails the test after it ends.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.jobs import model

# argv programs a guarded Popen refused, per test.
REACHED_CLAUDE: list[str] = []


def _program(args: Any) -> str:
    """The basename of the program an argv would exec."""
    if isinstance(args, str):
        argv = shlex.split(args)
    elif isinstance(args, (bytes, os.PathLike)):
        argv = [os.fsdecode(args)]
    else:
        argv = [os.fsdecode(item) for item in args]
    return Path(argv[0]).name if argv else ""


class GuardedPopen(subprocess.Popen):
    """A Popen that refuses to launch `claude`: tests stub their runner."""

    def __init__(self, args: Any, *args_: Any, **kwargs: Any) -> None:
        if _program(args) == "claude":
            REACHED_CLAUDE.append(str(args))
            # Failed is a BaseException: an `except Exception` cannot swallow it.
            raise pytest.fail.Exception(
                f"a test reached the real claude CLI: {args!r}"
            )
        super().__init__(args, *args_, **kwargs)


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    REACHED_CLAUDE.clear()
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    yield
    model.end_run()
    if REACHED_CLAUDE:
        pytest.fail("a test reached the real claude CLI: stub its runner")


# ----- a git checkout and a fake API, shared by the fixer's tests ---------------

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout of `main` with a remote, as the device's checkout is."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "config", "user.name", "test")
    git(work, "config", "user.email", "test@example.com")
    (work / "README").write_text("x")
    (work / ".gitignore").write_text("__pycache__/\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "start")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "origin", "main")
    return work


class Api:
    """The worker API the fixer needs: issue rows, facts, and code-change
    recording, all in memory."""

    def __init__(self, issues: dict[str, dict[str, Any]] | None = None) -> None:
        self.issues = issues or {}
        self.next_id = 0
        self.updates: list[dict[str, Any]] = []
        self.code_changes: list[dict[str, Any]] = []

    def autofill_failure(self, issue_id: str) -> dict[str, Any]:
        return self.issues.get(issue_id) or {}

    def update_autofill_failure(self, issue_id: str, changes: dict[str, Any]) -> None:
        self.updates.append({"id": issue_id, **changes})
        self.issues.setdefault(issue_id, {}).update(changes)

    def facts(self) -> list[dict[str, Any]]:
        return []

    def record_code_change(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.code_changes.append({"args": args, **kwargs})
        return {}

    def close(self) -> None:
        pass


def patch_package(
    monkeypatch: pytest.MonkeyPatch, package: str, name: str, value: Any
) -> None:
    """Set `name` on every module of `package` that has it.

    A name one big module imported once now lives in several package modules,
    so a patch has to land on each one that uses it. A dotted target like
    `time.monotonic` patches an attribute of a module the package imports.
    """
    import importlib
    import pkgutil

    pkg = importlib.import_module(package)
    head, _, attr = name.partition(".")
    for item in pkgutil.iter_modules(pkg.__path__):
        module = importlib.import_module(f"{pkg.__name__}.{item.name}")
        target = getattr(module, head, None)
        if target is None:
            continue
        if attr:
            monkeypatch.setattr(target, attr, value)
        else:
            monkeypatch.setattr(module, head, value)


def patch_session(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """Set `name` on every personal_os_worker.jobs.session.* module that has it."""
    patch_package(monkeypatch, "personal_os_worker.jobs.session", name, value)
