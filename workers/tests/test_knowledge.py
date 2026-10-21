import httpx
import pytest

from personal_os_worker.jobs import knowledge


@pytest.mark.parametrize("error", [RuntimeError("Sign in again"), httpx.ConnectError("Offline")])
def test_replay_connection_failures_are_distinct_from_regressions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception,
) -> None:
    def unavailable():  # type: ignore[no-untyped-def]
        raise error

    monkeypatch.setattr(knowledge, "connected", unavailable)
    assert knowledge.run_replay() == 2
    assert "could not complete" in capsys.readouterr().out
