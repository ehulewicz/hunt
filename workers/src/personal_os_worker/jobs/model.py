"""The one way the worker runs `claude -p`, on the maintainer's subscription.

Every call site builds its argv here and runs it through `run`, so each one is
metered (`claude_usage`), and a usage limit or failed envelope raises
`ModelError` instead of passing for an empty answer. Fill-time calls get no
tools. Only the fixer (`jobs/fix/`) passes `tools` and a `cwd`: it edits a
scratch clone of the repository, never the live checkout.

Each call names its purpose and is counted under it, process-wide. Learning
(`short_answers.py`) and fixing (`fix/`) are what a site should stop needing
once it has been seen; checking and drafting run on every application.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from personal_os_worker.jobs import claude_usage
from personal_os_worker.jobs.model_errors import checked_output

TIMEOUT_SECONDS = 300

Purpose = Literal["learn", "check", "draft", "fix"]
PURPOSES: tuple[Purpose, ...] = ("learn", "check", "draft", "fix")

_calls: Counter[str] = Counter()
_calls_lock = threading.Lock()


def argv(
    prompt: str,
    *,
    schema: dict[str, Any] | None = None,
    tools: Sequence[str] = (),
) -> list[str]:
    """A `claude -p` command: no tools unless named, no session kept, a JSON envelope."""
    command = [
        "claude",
        "-p",
        "--safe-mode",
        "--tools",
        ",".join(tools),
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    if tools:
        # Edits land in the scratch clone the call runs in, without a prompt.
        command += ["--permission-mode", "acceptEdits"]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    return [*command, prompt]


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = TIMEOUT_SECONDS,
    purpose: Purpose = "draft",
) -> str:
    """Run one call; its envelope, or `ModelError` when the model did not do the work."""
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        # It ran and was spent, though it answered nothing.
        _count(purpose)
        raise
    _count(purpose)
    claude_usage.record(process.stdout)
    return checked_output(process.stdout, process.returncode)


def _count(purpose: Purpose) -> None:
    with _calls_lock:
        _calls[purpose] += 1


def calls(*purposes: Purpose) -> int:
    """How many calls this process has made for these purposes; every purpose if none."""
    with _calls_lock:
        return sum(_calls[purpose] for purpose in purposes or PURPOSES)


def counts() -> dict[str, int]:
    """Every purpose's calls so far in this process."""
    with _calls_lock:
        return {purpose: _calls[purpose] for purpose in PURPOSES}


def structured(output: str) -> Any:
    """The schema's answer inside an envelope, else the envelope itself."""
    payload = json.loads(output)
    if isinstance(payload, dict) and "structured_output" in payload:
        return payload["structured_output"]
    return payload
