import pytest

from personal_os_worker.cli import main


def test_help_describes_scaffold(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "task execution is not implemented yet" in capsys.readouterr().out
