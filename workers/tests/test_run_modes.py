from __future__ import annotations

from typing import Any

import pytest

from personal_os_worker import runner


class FakeApi:
    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self._tasks = list(tasks)
        self.closed = False

    def claim(self, _worker_id: str) -> dict[str, Any] | None:
        return self._tasks.pop(0) if self._tasks else None

    def close(self) -> None:
        self.closed = True


def task(index: int, task_type: str = "prepare_application") -> dict[str, Any]:
    return {"id": f"{index:08d}-0000-4000-8000-000000000000", "task_type": task_type}


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def _wire(tasks: list[dict[str, Any]], outcomes: list[str]) -> FakeApi:
        api = FakeApi(tasks)
        monkeypatch.setattr(runner, "WorkerApi", lambda *a, **k: api)
        results = iter(outcomes)
        monkeypatch.setattr(runner, "process_task", lambda *a, **k: next(results))
        monkeypatch.setattr(runner.time, "sleep", lambda _: None)
        return api

    return _wire


def test_default_drains_the_queue_then_exits(wire, capsys) -> None:  # type: ignore[no-untyped-def]
    api = wire([task(1), task(2), task(3)], ["done", "done", "done"])
    assert runner.run_worker(object(), "token") == 0  # type: ignore[arg-type]
    output = capsys.readouterr().out
    assert "Queue drained. 3 done, 0 failed." in output
    assert api.closed is True


def test_empty_queue_says_so_and_does_not_hang(wire, capsys) -> None:  # type: ignore[no-untyped-def]
    wire([], [])
    assert runner.run_worker(object(), "token") == 0  # type: ignore[arg-type]
    assert "Queue is empty" in capsys.readouterr().out


def test_once_stops_after_a_single_task(wire, capsys) -> None:  # type: ignore[no-untyped-def]
    wire([task(1), task(2), task(3)], ["done", "done", "done"])
    assert runner.run_worker(object(), "token", once=True) == 0  # type: ignore[arg-type]
    output = capsys.readouterr().out
    assert "[1]" in output and "[2]" not in output
    # A single unit of work is not a drain, so there is no summary.
    assert "Queue drained" not in output


def test_failures_are_counted_and_change_the_exit_code(wire, capsys) -> None:  # type: ignore[no-untyped-def]
    wire([task(1), task(2)], ["done", "failed"])
    assert runner.run_worker(object(), "token") == 1  # type: ignore[arg-type]
    assert "1 done, 1 failed" in capsys.readouterr().out


def test_lost_leases_are_reported_separately(wire, capsys) -> None:  # type: ignore[no-untyped-def]
    wire([task(1), task(2)], ["done", "lost"])
    assert runner.run_worker(object(), "token") == 0  # type: ignore[arg-type]
    assert "1 lost" in capsys.readouterr().out


def test_watch_keeps_polling_past_an_empty_queue(monkeypatch, wire) -> None:  # type: ignore[no-untyped-def]
    """--watch must not exit when the queue drains, unlike the default."""
    wire([task(1)], ["done"])
    sleeps = 0

    def counting_sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(runner.time, "sleep", counting_sleep)
    with pytest.raises(KeyboardInterrupt):
        runner.run_worker(object(), "token", watch=True)  # type: ignore[arg-type]
    assert sleeps == 3
