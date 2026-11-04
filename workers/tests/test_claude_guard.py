"""The conftest guard refuses real Claude and Codex calls, and lets other tools run."""

import subprocess

import pytest
from conftest import REACHED_CLAUDE


@pytest.mark.parametrize("program", ["claude", "codex"])
def test_the_guard_refuses_the_real_model_cli(program: str) -> None:
    with pytest.raises(pytest.fail.Exception, match=f"real {program}"):
        subprocess.run([program, "--help"], check=False)
    # Recorded, so a call from a background thread still fails this test's teardown.
    assert REACHED_CLAUDE
    REACHED_CLAUDE.clear()


def test_the_guard_lets_every_other_program_through() -> None:
    subprocess.run(["true"], check=True)
    assert subprocess.run(
        ["python3", "-c", "print('ok')"], capture_output=True, text=True
    ).stdout.strip() == "ok"
