"""The conftest guard: a test reaching the real `claude` CLI fails, others pass."""

import subprocess

import pytest
from conftest import REACHED_CLAUDE


def test_the_guard_refuses_the_real_claude_cli() -> None:
    with pytest.raises(pytest.fail.Exception, match="real claude"):
        subprocess.run(["claude", "-p", "x"], check=False)
    # Recorded, so a call from a background thread still fails this test's teardown.
    assert REACHED_CLAUDE
    REACHED_CLAUDE.clear()


def test_the_guard_lets_every_other_program_through() -> None:
    subprocess.run(["true"], check=True)
    assert subprocess.run(
        ["python3", "-c", "print('ok')"], capture_output=True, text=True
    ).stdout.strip() == "ok"
