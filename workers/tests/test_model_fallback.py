"""A limited Claude call retries the same work with Codex, without hiding failures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.jobs import claude_usage, model
from personal_os_worker.jobs.model_errors import ModelError

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}
LIMIT = {"is_error": True, "api_error": "usage_limit_reached", "api_error_status": 429}


def events(*, answer: str = '{"answer":"pong"}') -> str:
    return "\n".join(json.dumps(event) for event in (
        {"type": "thread.started", "thread_id": "test"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": answer}},
        {"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 7}},
    ))


class Cli:
    """Only the external process is replaced; parsing and accounting stay real."""

    def __init__(self, *, claude: dict[str, Any] = LIMIT,
                 codex: str | Exception | None = None, exit_code: int = 0) -> None:
        self.claude = claude
        self.codex = events() if codex is None else codex
        self.exit_code = exit_code
        self.programs: list[str] = []
        self.codex_kwargs: dict[str, Any] = {}
        self.command: list[str] = []
        self.schema: Any = None

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.programs.append(command[0])
        if command[0] == "claude":
            code = 1 if self.claude.get("is_error") else 0
            return subprocess.CompletedProcess(command, code, json.dumps(self.claude), "")
        assert command[:2] == ["codex", "exec"]
        self.command = command
        self.codex_kwargs = kwargs
        if "--output-schema" in command:
            path = Path(command[command.index("--output-schema") + 1])
            self.schema = json.loads(path.read_text())
        if isinstance(self.codex, Exception):
            raise self.codex
        return subprocess.CompletedProcess(command, self.exit_code, self.codex, "PRIVATE stderr")


def test_limit_retries_the_same_prompt_and_schema_and_accounts_both_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = Cli()
    monkeypatch.setattr(model.subprocess, "run", cli)
    model.begin_run()
    samples: list[dict[str, Any]] = []
    with claude_usage.observing(samples.append):
        output = model.run(model.argv("PRIVATE prompt", schema=SCHEMA), purpose="audit")
    assert model.structured(output) == {"answer": "pong"}
    assert cli.programs == ["claude", "codex"]
    assert cli.codex_kwargs["input"] == "PRIVATE prompt"
    assert "PRIVATE prompt" not in cli.command
    assert cli.schema == SCHEMA
    assert "--ephemeral" in cli.command
    assert "--ignore-user-config" in cli.command
    assert cli.command[cli.command.index("--sandbox") + 1] == "read-only"
    disabled = {cli.command[i + 1] for i, arg in enumerate(cli.command) if arg == "--disable"}
    assert {"shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "hooks"} <= disabled
    assert model.run_usage()["audit"] == {
        "calls": 2, "input_tokens": 80, "output_tokens": 7,
        "cache_read_input_tokens": 20, "cache_creation_input_tokens": 0,
    }
    assert samples[0]["model"] == "codex"
    assert samples[0]["cost_usd"] is None
    # A job gets an isolated directory, never its repository or page evidence.
    assert not cli.codex_kwargs["cwd"].exists()


@pytest.mark.parametrize("limit", [
    LIMIT,
    {"is_error": True, "api_error": "rate_limit_error"},
    {"is_error": True, "api_error_status": 429},
    {"is_error": True, "result": "You've hit your limit · resets 4am"},
])
def test_recognized_limits_use_codex(
    monkeypatch: pytest.MonkeyPatch, limit: dict[str, Any],
) -> None:
    cli = Cli(claude=limit)
    monkeypatch.setattr(model.subprocess, "run", cli)
    assert model.structured(model.run(model.argv("x", schema=SCHEMA))) == {"answer": "pong"}


def test_cooldown_skips_exhausted_claude_then_tries_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = Cli()
    clock = [100.0]
    monkeypatch.setattr(model.subprocess, "run", cli)
    monkeypatch.setattr(model.time, "monotonic", lambda: clock[0])
    command = model.argv("x", schema=SCHEMA)
    model.run(command)
    clock[0] += 299
    model.run(command)
    assert cli.programs == ["claude", "codex", "codex"]
    clock[0] += 2
    cli.claude = {"structured_output": {"answer": "Claude is back"}}
    assert model.structured(model.run(command)) == {"answer": "Claude is back"}
    assert cli.programs[-1] == "claude"


def test_other_claude_errors_do_not_switch_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = Cli(claude={"is_error": True, "api_error": "authentication_error"})
    monkeypatch.setattr(model.subprocess, "run", cli)
    with pytest.raises(ModelError, match="authentication_error"):
        model.run(model.argv("PRIVATE"))
    assert cli.programs == ["claude"]


def test_successful_text_mentioning_a_limit_does_not_trigger_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = Cli(claude={"is_error": False, "result": "You've hit your limit is an error message"})
    monkeypatch.setattr(model.subprocess, "run", cli)
    output = json.loads(model.run(model.argv("x")))
    assert output["result"] == "You've hit your limit is an error message"
    assert cli.programs == ["claude"]


@pytest.mark.parametrize("output, code", [
    (events(), 1),
    ("", 0),
    ('{"type":"turn.completed"}', 0),
    (events(answer=""), 0),
    (events(answer="PRIVATE malformed JSON"), 0),
    ('{"type":"turn.failed","error":{"message":"PRIVATE"}}', 0),
    ('{"type":"error","message":"PRIVATE"}', 1),
    ('{"type":"item.completed","item":{"type":"command_execution"}}\n' + events(), 0),
])
def test_failed_or_incomplete_codex_is_never_an_empty_success(
    monkeypatch: pytest.MonkeyPatch, output: str, code: int,
) -> None:
    cli = Cli(codex=output, exit_code=code)
    monkeypatch.setattr(model.subprocess, "run", cli)
    with pytest.raises(ModelError, match="Codex") as raised:
        model.run(model.argv("PRIVATE prompt", schema=SCHEMA))
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize("error, calls", [
    (FileNotFoundError("PRIVATE"), 1),
    (subprocess.TimeoutExpired("PRIVATE", 2), 2),
])
def test_codex_startup_failure_and_timeout_are_safe_and_counted(
    monkeypatch: pytest.MonkeyPatch, error: Exception, calls: int,
) -> None:
    monkeypatch.setattr(model.subprocess, "run", Cli(codex=error))
    model.begin_run()
    with pytest.raises(ModelError, match="Codex") as raised:
        model.run(model.argv("PRIVATE", schema=SCHEMA))
    assert "PRIVATE" not in str(raised.value)
    assert model.run_counts()["draft"] == calls


def test_text_only_call_retains_the_result_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = Cli(codex=events(answer="plain text"))
    monkeypatch.setattr(model.subprocess, "run", cli)
    output = json.loads(model.run(model.argv("x")))
    assert output["result"] == "plain text"
    assert "structured_output" not in output
