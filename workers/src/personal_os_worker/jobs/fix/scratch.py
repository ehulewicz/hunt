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
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

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
    mode: str = field(default="100644", compare=False)
    old_mode: str = field(default="100644", compare=False)


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
        canonical = PurePosixPath(path)
        if (str(canonical) != path or canonical.is_absolute() or ".." in canonical.parts
                or "\\" in path or any(ord(char) < 32 for char in path)):
            return f"{path!r} is not a canonical relative path"
        if not path.startswith(ALLOWED):
            return f"{path} is outside the worker code and tests a fix may change"
        if any(path == item or (item.endswith("/") and path.startswith(item))
               for item in DENYLIST):
            return f"{path} is never changed by a fix"
        # A package or bytecode/extension file can take the import name of a
        # protected module even though it is not the protected .py file.
        folded = path.casefold()
        if any(folded == item.casefold().rstrip("/")
               or folded.startswith(item.casefold().removesuffix(".py").rstrip("/") + "/")
               or folded.startswith(item.casefold().removesuffix(".py").rstrip("/") + ".")
               for item in DENYLIST):
            return f"{path} shadows code that is never changed by a fix"
        if change.status != "A" and path.startswith("workers/tests/"):
            return f"{path} is an existing test; a fix adds tests and never edits one"
        if change.status in {"D", "R"}:
            return f"{path} would be deleted or renamed; a fix only adds and edits"
        if change.status not in {"A", "M"} or change.mode != "100644" \
                or (change.status == "M" and change.old_mode != change.mode):
            return f"{path} is not an ordinary file edit (symlinks and mode changes are refused)"
        if path.startswith(ALLOWED[1]):
            if canonical.name in {"conftest.py", "__init__.py"}:
                return f"{path} changes how existing tests run"
            if not path.startswith("workers/tests/fixtures/") and not (
                canonical.name.startswith("test_") and canonical.suffix == ".py"
            ):
                return f"{path} is not a new regression test or fixture"
    return None


def interpreter(repo: Path) -> Path:
    """The workspace interpreter, which has the worker's test and lint tools."""
    for candidate in (repo / ".venv/bin/python", repo / "workers/.venv/bin/python"):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def clear_stale(root: Path) -> None:
    """Remove every clone a previous run's fixer left under `root`; a missing
    root is fine."""
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


class Scratch:
    """A clone the fix is written in; removed once the fix is kept or given up."""

    def __init__(self, repo: Path, root: Path) -> None:
        self.repo = repo
        self.root = root
        self._base = self.git("rev-parse", "HEAD").strip()

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
        return self._base

    def write(self, relative: str, content: bytes) -> Path:
        path = self.root / relative
        if (PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
                or "\\" in relative or not path.resolve().is_relative_to(self.root.resolve())
                or any(parent.is_symlink() for parent in (path, *path.parents)
                       if parent.is_relative_to(self.root))):
            raise ValueError("a scratch write must stay inside the clone without symlinks")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def changes(self) -> list[Change]:
        """Every path the fix touched, new files included, against the base commit."""
        self.git("add", "-A")
        records = self.git("diff", "--cached", "--raw", "-z", "--no-renames", self.base)
        found: list[Change] = []
        parts = records.rstrip("\0").split("\0") if records else []
        for header, path in zip(parts[::2], parts[1::2], strict=True):
            old_mode, mode, _, _, status = header.removeprefix(":").split()
            found.append(Change(status, path, mode, old_mode))
        # An ignored .pyc or .py can be imported during gates/probes without
        # ever appearing in the patch. Refuse these uncommittable additions.
        ignored = self.git("ls-files", "--others", "--ignored", "--exclude-standard",
                           "-z", "--", *ALLOWED)
        found.extend(Change("I", path) for path in ignored.split("\0") if path)
        return found

    def diff(self) -> str:
        """The fix as one patch against the base commit, new files included."""
        self.git("add", "-A")
        return self.git("diff", "--cached", "--binary", "--no-renames", self.base)

    def _run(self, name: str, command: list[str]) -> Gate:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root / "workers/src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
        command = [python, "-m", "personal_os_worker.jobs.replay", "--json"]
        if question:
            command += ["--question", question]
        if fact_key:
            command += ["--fact", fact_key]
        return self._run("replay", command)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
