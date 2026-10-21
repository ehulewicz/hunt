"""The one way the worker runs `claude -p`, on the maintainer's subscription.

Every call site builds its argv here and runs it through `run`, so each one is
metered (`claude_usage`), and a usage limit or failed envelope raises
`ModelError` instead of passing for an empty answer. Fill-time calls get no
tools. Only the fixer (`jobs/fix/`) passes `tools` and a `cwd`: it edits a
scratch clone of the repository, never the live checkout.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from personal_os_worker.jobs import claude_usage
from personal_os_worker.jobs.model_errors import checked_output

TIMEOUT_SECONDS = 300


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
    command: list[str], *, cwd: Path | None = None, timeout: float = TIMEOUT_SECONDS
) -> str:
    """Run one call; its envelope, or `ModelError` when the model did not do the work."""
    process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    claude_usage.record(process.stdout)
    return checked_output(process.stdout, process.returncode)


def structured(output: str) -> Any:
    """The schema's answer inside an envelope, else the envelope itself."""
    payload = json.loads(output)
    if isinstance(payload, dict) and "structured_output" in payload:
        return payload["structured_output"]
    return payload
