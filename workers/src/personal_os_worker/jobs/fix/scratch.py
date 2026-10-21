"""A scratch clone of the repository, where a model's fix is written and tested.

The clone shares the live repository's objects (`git clone --shared`), so it
costs seconds, and nothing done in it reaches the live checkout: a fix leaves
it only as a diff, once it passed. The gates run with the workspace's own
interpreter and the scratch source first on `PYTHONPATH`, so they test the fix
and not the code the run started with.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# A fix may change worker code and add tests, nothing else.
ALLOWED = ("workers/src/personal_os_worker/jobs/", "workers/tests/")
# What keeps the run honest and safe is never a model's to change: the submit
# guards, the account and its secrets, the model calls, the fixer, and the
# checks a lesson must pass. Existing tests are the regression net, so a fix
# adds tests and never edits or deletes one.
DENYLIST = (
    "workers/src/personal_os_worker/jobs/safety.py",
    "workers/src/personal_os_worker/jobs/model.py",
    "workers/src/personal_os_worker/jobs/model_errors.py",
    "workers/src/personal_os_worker/jobs/claude_usage.py",
    "workers/src/personal_os_worker/jobs/fix/",
    "workers/src/personal_os_worker/jobs/account.py",
    "workers/src/personal_os_worker/jobs/account_page.py",
    "workers/src/personal_os_worker/jobs/mail.py",
    "workers/src/personal_os_worker/jobs/short_answers.py",
    "workers/src/personal_os_worker/jobs/answer_check.py",
    "workers/src/personal_os_worker/jobs/answers.py",
    "workers/src/personal_os_worker/jobs/cover_letter.py",
    "workers/src/personal_os_worker/jobs/learn.py",
    "workers/src/personal_os_worker/jobs/replay.py",
)
OUTPUT_LIMIT = 6_000
GATE_TIMEOUT_SECONDS = 900


@dataclass(frozen=True, slots=True)
class Change:
    """One path a fix touched: `A`dded, `M`odified, `D`eleted, or `R`enamed."""

    status: str
    path: str


@dataclass(frozen=True, slots=True)
class Gate:
    """One check run on the scratch code."""

    name: str
    passed: bool
    output: str


def _tail(text: str) -> str:
    return text[-OUTPUT_LIMIT:]


def refusal(changes: list[Change]) -> str | None:
    """Why a fix may not be kept as it stands; None when every path is allowed."""
    if not changes:
        return "the fix changed nothing"
    for change in changes:
        path = change.path
        if not path.startswith(ALLOWED):
            return f"{path} is outside the worker code and tests a fix may change"
        if any(path == item or (item.endswith("/") and path.startswith(item))
               for item in DENYLIST):
            return f"{path} is never changed by a fix"
        if change.status != "A" and path.startswith("workers/tests/"):
            return f"{path} is an existing test; a fix adds tests and never edits one"
        if change.status in {"D", "R"}:
            return f"{path} would be deleted or renamed; a fix only adds and edits"
    return None


def interpreter(repo: Path) -> Path:
    """The workspace interpreter, which has the worker's test and lint tools."""
    for candidate in (repo / ".venv/bin/python", repo / "workers/.venv/bin/python"):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


class Scratch:
    """A clone the fix is written in; removed once the fix is kept or given up."""

    def __init__(self, repo: Path, root: Path) -> None:
        self.repo = repo
        self.root = root

    @classmethod
    def create(cls, repo: Path, root: Path) -> Scratch:
        if root.exists():
            shutil.rmtree(root)
        root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--shared", "--quiet", str(repo), str(root)],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return cls(repo, root)

    def git(self, *args: str, check: bool = True) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=check, capture_output=True, text=True,
            timeout=120,
        ).stdout

    @property
    def base(self) -> str:
        """The commit the clone started from, which the fix is a diff against."""
        return self.git("rev-parse", "HEAD").strip()

    def write(self, relative: str, content: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def changes(self) -> list[Change]:
        """Every path the fix touched, new files included, against the base commit."""
        self.git("add", "-A")
        lines = self.git("diff", "--cached", "--name-status", "--no-renames", "HEAD")
        found: list[Change] = []
        for line in lines.splitlines():
            status, _, path = line.partition("\t")
            if path:
                found.append(Change(status[:1], path))
        return found

    def diff(self) -> str:
        """The fix as one patch against the base commit, new files included."""
        self.git("add", "-A")
        return self.git("diff", "--cached", "--binary", "--no-renames", "HEAD")

    def _run(self, name: str, command: list[str]) -> Gate:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root / "workers/src")
        try:
            process = subprocess.run(
                command, cwd=self.root / "workers", capture_output=True, text=True,
                timeout=GATE_TIMEOUT_SECONDS, env=environment,
            )
        except subprocess.TimeoutExpired:
            return Gate(name, False, f"{name} did not finish in {GATE_TIMEOUT_SECONDS} seconds")
        return Gate(name, process.returncode == 0, _tail(process.stdout + process.stderr))

    def gates(self) -> list[Gate]:
        """ruff, mypy, and the whole worker suite, stopping at the first that fails."""
        python = str(interpreter(self.repo))
        results: list[Gate] = []
        for name, command in (
            ("ruff", [python, "-m", "ruff", "check", "."]),
            ("mypy", [python, "-m", "mypy", "src/"]),
            ("pytest", [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]),
        ):
            gate = self._run(name, command)
            results.append(gate)
            if not gate.passed:
                break
        return results

    def replay(self, *, question: str | None, fact_key: str | None) -> Gate:
        """The scratch code against the answers you accepted (`os hunt replay`).

        Exit 0 passes, 1 means it changes an accepted answer, and 2 means the
        check could not run, which never counts as passing.
        """
        python = str(interpreter(self.repo))
        command = [python, "-m", "personal_os_worker", "hunt", "replay", "--json"]
        if question:
            command += ["--question", question]
        if fact_key:
            command += ["--fact", fact_key]
        return self._run("replay", command)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
