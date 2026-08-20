import pytest

from personal_os_worker.cli import build_parser, main


def test_help_describes_the_local_cli(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "never submits applications" in output
    for command in ("login", "whoami", "doctor", "run"):
        assert command in output


def test_pairing_is_gone() -> None:
    """Sign-in replaced the paired worker credential, so no pair command remains."""
    commands = {
        name
        for action in build_parser()._subparsers._group_actions  # type: ignore[union-attr]
        for name in action.choices
    }
    assert "pair" not in commands
    assert commands == {"login", "whoami", "doctor", "run"}
