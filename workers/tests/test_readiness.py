from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.core.doctor import DoctorCheck
from personal_os_worker.jobs.readiness import fixer_check


class Shell:
    """Answers each command the check runs; records how it was asked."""

    def __init__(self, *, branch: str = "main", broken: tuple[str, ...] = (),
                 remote: str = "ok") -> None:
        self.branch = branch
        self.broken = broken
        self.remote = remote
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        code, out = 0, ""
        if command[:2] == ["git", "rev-parse"]:
            out = self.branch + "\n"
        elif command[1:2] == ["-m"] and command[2] in self.broken:
            code = 1
        elif command[:2] == ["git", "ls-remote"]:
            if self.remote == "timeout":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            code = 0 if self.remote == "ok" else 128
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")


def test_a_device_ready_to_fix_passes(tmp_path: Path) -> None:
    shell = Shell()
    check = fixer_check(tmp_path, run=shell, which=lambda _: "/usr/bin/claude")
    assert check.ok and check.advisory and check.label == "OK"
    tools = [command[2] for command, _ in shell.calls if command[1:2] == ["-m"]]
    assert tools == ["ruff", "mypy", "pytest"]


def test_every_missing_piece_is_named_and_only_warns(tmp_path: Path) -> None:
    shell = Shell(branch="experiment", broken=("mypy",), remote="timeout")
    check = fixer_check(tmp_path, run=shell, which=lambda _: None)
    assert not check.ok and check.advisory and check.label == "WARN"
    assert "on experiment, not main" in check.detail
    assert "mypy will not run" in check.detail
    assert "origin did not answer within 10 seconds" in check.detail
    assert "claude is not on PATH" in check.detail
    (_, asked), = [(command, kwargs) for command, kwargs in shell.calls
                   if command[:2] == ["git", "ls-remote"]]
    # A prompt would hang the check; a refusal must come back within the timeout.
    assert asked["env"]["GIT_TERMINAL_PROMPT"] == "0" and asked["timeout"] == 10


def test_a_refused_remote_is_named(tmp_path: Path) -> None:
    check = fixer_check(tmp_path, run=Shell(remote="refused"), which=lambda _: "claude")
    assert not check.ok and "origin did not answer" in check.detail


def test_os_doctor_warns_for_the_fixer_but_does_not_fail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from personal_os_worker.jobs import commands, readiness

    seen: dict[str, Any] = {}

    def fake_doctor(config: Any, token: str, *, extra: Any) -> list[DoctorCheck]:
        seen["extra"] = tuple(extra)
        return [DoctorCheck("Sign-in", True, "loaded"),
                DoctorCheck("Fixer", False, "claude is not on PATH", advisory=True)]

    monkeypatch.setattr(commands, "connected", lambda: (object(), "token"))
    monkeypatch.setattr(commands, "run_doctor", fake_doctor)
    assert commands.run_doctor_command() == 0
    assert readiness.fixer_check in seen["extra"]
    assert "[WARN] Fixer: claude is not on PATH" in capsys.readouterr().out


def test_os_doctor_still_fails_for_a_check_that_is_not_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_worker.jobs import commands

    monkeypatch.setattr(commands, "connected", lambda: (object(), "token"))
    monkeypatch.setattr(commands, "run_doctor", lambda *_, **__: [
        DoctorCheck("Local API", False, "HTTP 401"),
    ])
    assert commands.run_doctor_command() == 1
