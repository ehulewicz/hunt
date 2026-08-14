import pytest

from personal_os_worker.cli import main


def test_help_describes_foreground_worker(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "never submits applications" in output
    assert "pair" in output
    assert "doctor" in output
    assert "run" in output
