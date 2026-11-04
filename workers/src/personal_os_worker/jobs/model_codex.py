"""Text-only Codex CLI fallback, adapted to the existing model envelope.

Only model.py invokes this adapter. The prompt goes over stdin in a disposable
directory, with user configuration, tools and session persistence disabled.
Authentication still comes from the user's existing Codex login.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from personal_os_worker.jobs.model_errors import ModelError

DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "memories", "multi_agent",
    "image_generation", "view_image", "sleep_tool", "skill_search", "hooks",
    "browser_use", "computer_use", "goals",
)


def run(command: list[str], *, timeout: float) -> str:
    """Retry a command built by model.argv; never inherit the caller's checkout."""
    if len(command) < 3 or command[:2] != ["claude", "-p"]:
        raise ModelError("Claude usage limit reached; Codex needs a model.argv command")
    schema = None
    if "--json-schema" in command:
        schema = command[command.index("--json-schema") + 1]
    with TemporaryDirectory(prefix="personal-os-model-") as directory:
        root = Path(directory)
        args = [
            "codex", "exec", "--json", "--ephemeral", "--ignore-user-config",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", "project_doc_max_bytes=0",
        ]
        for feature in DISABLED_FEATURES:
            args += ["--disable", feature]
        if schema is not None:
            schema_path = root / "schema.json"
            schema_path.write_text(schema, encoding="utf-8")
            args += ["--output-schema", str(schema_path)]
        process = subprocess.run(
            [*args, "-"], input=command[-1], cwd=root, check=False,
            capture_output=True, text=True, timeout=timeout,
        )
    return checked_output(process.stdout, process.returncode, structured=schema is not None)


def checked_output(output: str, returncode: int, *, structured: bool) -> str:
    """Require a completed turn and answer; never surface raw model error text."""
    if returncode:
        raise ModelError(f"Codex fallback failed (exit {returncode}); Claude usage limit reached")
    answer = ""
    completed = False
    usage: dict[str, Any] = {}
    try:
        for line in output.splitlines():
            event = json.loads(line)
            kind = event.get("type")
            if kind in ("turn.failed", "error"):
                raise ModelError("Codex fallback failed; Claude usage limit reached")
            if kind in ("item.started", "item.completed"):
                item = event.get("item", {})
                if item.get("type") not in ("agent_message", "reasoning"):
                    raise ModelError("Codex fallback returned an unexpected item")
                if kind == "item.completed" and item.get("type") == "agent_message":
                    answer = item.get("text", "")
            if kind == "turn.completed":
                completed = True
                usage = event.get("usage") or {}
        if not completed or not isinstance(answer, str) or not answer.strip():
            raise ModelError("Codex fallback returned no completed answer")
        envelope: dict[str, Any] = {
            "type": "result", "subtype": "success", "is_error": False,
            "result": answer, "modelUsage": {"codex": {}},
            "usage": _usage(usage),
        }
        if structured:
            envelope["structured_output"] = json.loads(answer)
        return json.dumps(envelope)
    except (ValueError, TypeError, AttributeError) as error:
        raise ModelError("Codex fallback returned malformed output") from error


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    """Codex input includes cache reads; Claude reports them separately."""
    def whole(key: str) -> int:
        value = usage.get(key)
        return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0

    cached = min(whole("cached_input_tokens"), whole("input_tokens"))
    return {
        "input_tokens": whole("input_tokens") - cached,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": whole("cache_write_input_tokens"),
        "output_tokens": whole("output_tokens"),
    }
