"""Whether this device can fix code during a run: `os doctor`'s fixer check.

The fixer (`jobs/fix/`) commits a kept fix on `main` and pushes it, runs the
verification gates with the workspace interpreter, and asks `claude` for the
code. Any of those missing only warns: a run fills as it would without the
fixer, and what only code can settle waits in the Inbox.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personal_os_worker.core.doctor import DoctorCheck
from personal_os_worker.jobs.facts.career import repository_root
from personal_os_worker.jobs.fix.gates import interpreter

NAME = "Fixer"
GATE_TOOLS = ("ruff", "mypy")
REMOTE_TIMEOUT_SECONDS = 10
TOOL_TIMEOUT_SECONDS = 60

Run = Callable[..., subprocess.CompletedProcess[str]]


def fixer_check(
    repo: Path | None = None,
    *,
    run: Run = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> DoctorCheck:
    """On `main`, the gates run, `origin` answers without a prompt, and `claude` is here."""
    root = repo or repository_root()
    problems: list[str] = []

    def call(command: list[str], *, timeout: float, env: dict[str, str] | None = None) -> bool:
        kwargs: dict[str, Any] = {"env": env} if env is not None else {}
        try:
            result = run(command, cwd=root, capture_output=True, text=True, check=False,
                         timeout=timeout, **kwargs)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    try:
        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                     capture_output=True, text=True, check=False, timeout=TOOL_TIMEOUT_SECONDS)
        on = branch.stdout.strip() if branch.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        on = ""
    if on != "main":
        problems.append(f"the checkout is on {on or 'no branch'}, not main")
    python = interpreter(root)
    broken = [tool for tool in GATE_TOOLS
              if not call([str(python), "-m", tool, "--version"], timeout=TOOL_TIMEOUT_SECONDS)]
    if broken:
        problems.append(f"{', '.join(broken)} will not run under {python}")
    # A passphrase or a login prompt would hang a push mid-run; neither may ask.
    quiet = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    quiet.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    if not call(["git", "ls-remote", "--exit-code", "origin", "HEAD"],
                timeout=REMOTE_TIMEOUT_SECONDS, env=quiet):
        problems.append(
            f"origin did not answer within {REMOTE_TIMEOUT_SECONDS} seconds without a prompt"
        )
    if which("claude") is None:
        problems.append("claude is not on PATH")
    if problems:
        return DoctorCheck(
            NAME, False,
            "; ".join(problems) + ". Runs still fill, but fix no code.",
            advisory=True,
        )
    return DoctorCheck(
        NAME, True, f"on main, gates run under {python}, origin answers, claude is here",
        advisory=True,
    )
