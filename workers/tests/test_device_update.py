import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.device import update

REPO = Path("/repo")
OLD = "a" * 40
NEW = "b" * 40


class FakeShell:
    """Answers git and uv the way a checkout in a given state would."""

    def __init__(
        self,
        *,
        branch: str = "main",
        dirty: str = "",
        ahead: int = 0,
        behind: int = 0,
        fetch_fails: bool = False,
        deps_changed: str = "",
        tools: str = "personal-os-worker v0.1.0\n- os\npersonal-os-backend v0.1.0\n",
    ) -> None:
        self.head = OLD
        self.branch = branch
        self.dirty = dirty
        self.ahead = ahead
        self.behind = behind
        self.fetch_fails = fetch_fails
        self.deps_changed = deps_changed
        self.tools = tools
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        self.envs.append(kwargs.get("env"))
        out, code = "", 0
        command = argv[3:] if argv[:2] == ["git", "-C"] else argv
        if command[:2] == ["rev-parse", "HEAD"]:
            out = self.head + "\n"
        elif command[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            out = self.branch + "\n"
        elif command[:1] == ["status"]:
            out = self.dirty
        elif command[:1] == ["rev-list"]:
            out = f"{self.ahead}\t{self.behind}\n"
        elif command[:1] == ["fetch"]:
            code = 128 if self.fetch_fails else 0
        elif command[:1] == ["merge"]:
            self.head, self.behind = NEW, 0
        elif command[:1] == ["diff"]:
            out = self.deps_changed
        elif argv[1:3] == ["tool", "list"]:
            out = self.tools
        return subprocess.CompletedProcess(argv, code, out, "fatal: could not read" if code else "")

    def ran(self, *prefix: str) -> bool:
        return any(
            call[: len(prefix)] == list(prefix)
            or (call[:2] == ["git", "-C"] and call[3 : 3 + len(prefix)] == list(prefix))
            for call in self.calls
        )


@pytest.fixture(autouse=True)
def uv_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update.shutil, "which", lambda name: name)


def test_a_clean_checkout_that_is_behind_fast_forwards_and_syncs() -> None:
    shell = FakeShell(behind=3)
    result = update.update_checkout(REPO, run=shell)
    assert shell.ran("merge", "--ff-only", "--quiet", "origin/main")
    assert shell.ran("uv", "sync", "--all-packages", "--python", "3.13")
    assert result.pulled_from == OLD
    assert result.checkout.commit == NEW
    assert not result.checkout.local_changes
    assert "updated" in result.note


@pytest.mark.parametrize(
    "state",
    [
        {"dirty": " M workers/src/personal_os_worker/cli.py\n"},
        {"ahead": 1},
        {"ahead": 2, "behind": 3},
        {"branch": "experiment", "behind": 1},
    ],
)
def test_local_changes_are_never_stashed_reset_or_merged(state: dict[str, Any]) -> None:
    shell = FakeShell(**state)
    result = update.update_checkout(REPO, run=shell)
    assert result.checkout.local_changes
    assert result.pulled_from is None
    assert result.note == f"running local changes at {OLD[:7]}"
    for forbidden in ("merge", "stash", "reset", "checkout", "pull"):
        assert not shell.ran(forbidden)
    assert not shell.ran("uv", "sync")


def test_untracked_files_are_not_local_changes() -> None:
    shell = FakeShell(behind=1)
    update.update_checkout(REPO, run=shell)
    status = next(call for call in shell.calls if "status" in call)
    assert "--untracked-files=no" in status


def test_an_up_to_date_checkout_is_left_alone() -> None:
    shell = FakeShell()
    result = update.update_checkout(REPO, run=shell)
    assert not shell.ran("merge")
    assert result.note == f"up to date at {OLD[:7]}"


def test_a_failed_fetch_runs_as_is_and_says_so() -> None:
    shell = FakeShell(fetch_fails=True)
    result = update.update_checkout(REPO, run=shell)
    assert not shell.ran("merge")
    assert result.note.startswith("could not reach origin")


def test_the_fetch_never_waits_on_a_prompt() -> None:
    shell = FakeShell()
    update.update_checkout(REPO, run=shell)
    fetch_env = shell.envs[next(i for i, call in enumerate(shell.calls) if "fetch" in call)]
    assert fetch_env is not None
    assert fetch_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "BatchMode=yes" in fetch_env["GIT_SSH_COMMAND"]


def test_installed_tools_are_reinstalled_only_when_dependencies_changed() -> None:
    unchanged = FakeShell(behind=1)
    update.update_checkout(REPO, run=unchanged)
    assert not unchanged.ran("uv", "tool", "install")

    changed = FakeShell(behind=1, deps_changed="uv.lock\n")
    update.update_checkout(REPO, run=changed)
    installs = [call for call in changed.calls if call[1:3] == ["tool", "install"]]
    assert [call[-1] for call in installs] == [str(REPO / "workers"), str(REPO / "backend")]
    assert all("--reinstall" in call and "--editable" in call for call in installs)


def test_the_source_fingerprint_follows_edits(tmp_path: Path) -> None:
    source = tmp_path / "pkg"
    source.mkdir()
    (source / "a.py").write_text("x = 1\n")
    before = update.source_fingerprint(source)
    (source / "a.py").write_text("x = 22\n")
    assert update.source_fingerprint(source) != before
