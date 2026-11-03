from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.core.doctor import python_check
from personal_os_worker.core.tls import tls

SOURCE = Path(__file__).resolve().parents[1] / "src" / "personal_os_worker"
# What builds an httpx client, and so a TLS context of its own unless told otherwise.
BUILDS_A_CLIENT = {"Client", "AsyncClient", "get", "post", "put", "patch", "delete", "head",
                   "options", "request", "stream"}


def test_every_client_verifies_with_the_one_shared_context() -> None:
    """A fresh context's handshake keeps about half a megabyte for good; the agent,
    building two clients a beat, grew 170 MB an hour before they shared one."""
    unshared: list[str] = []
    seen = 0
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx"
                    and node.func.attr in BUILDS_A_CLIENT):
                continue
            seen += 1
            verify = next((keyword.value for keyword in node.keywords
                           if keyword.arg == "verify"), None)
            if not (isinstance(verify, ast.Call) and isinstance(verify.func, ast.Name)
                    and verify.func.id == "tls"):
                unshared.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    assert seen >= 10  # the scan does see the clients
    assert unshared == []


def test_the_shared_context_is_one_object() -> None:
    assert tls() is tls()


def test_the_project_interpreter_passes() -> None:
    check = python_check((3, 13, 4), "/tools/os/bin/python3")
    assert check.ok and "3.13" in check.detail


def test_python_3_14_fails_with_the_reinstall_command() -> None:
    check = python_check((3, 14, 3), "/tools/os/bin/python3")
    assert not check.ok and not check.advisory
    assert "/tools/os/bin/python3 is Python 3.14, not 3.13" in check.detail
    assert "uv tool install --reinstall --python 3.13 --editable ./workers" in check.detail


def test_a_run_on_the_wrong_interpreter_never_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os_worker.core.doctor import DoctorCheck
    from personal_os_worker.jobs import apply_run as commands

    def connected() -> Any:
        pytest.fail("nothing is connected to before the interpreter is checked")

    monkeypatch.setattr(commands, "python_check",
                        lambda: DoctorCheck("Python", False, "it is Python 3.14"))
    monkeypatch.setattr(commands, "connected", connected)
    with pytest.raises(RuntimeError, match=r"Python: it is Python 3\.14"):
        commands.apply_posting("posting", dry_run=False, no_claude=True)
