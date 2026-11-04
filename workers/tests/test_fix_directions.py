"""Directions you send the fixer mid-run, and a posting it may call gone."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.queue import FixQueue
from personal_os_worker.jobs.remote import RunLink


class Api:
    def __init__(self, status: str = "open") -> None:
        self.status = status
        self.updates: list[dict[str, Any]] = []

    def autofill_failure(self, _issue_id: str) -> dict[str, Any]:
        return {"status": self.status, "verification_notes": ""}

    def update_autofill_failure(self, _issue_id: str, changes: dict[str, Any]) -> None:
        self.updates.append(changes)
        self.status = str(changes.get("status") or self.status)

    def facts(self) -> list[dict[str, Any]]:
        return []


def reply(kind: str, *, summary: str = "said", quote: str = "") -> str:
    structured = {"kind": kind, "file": "", "name": "", "missing_fact": "",
                  "summary": summary, "quote": quote}
    return json.dumps({"result": json.dumps(structured), "structured_output": structured})


def posting() -> lane.Problem:
    return lane.Problem(
        key="posting_not_found|greenhouse.io|knowbe4",
        cause="posting_not_found",
        question="Software Engineer Summer Internship",
        note="not found",
        target={"kind": "route", "goal": "posting"},
        site="greenhouse.io",
        issue_id="i1",
        run_id="run-1",
        evidence={"arrival_text": "The job you are looking for is no longer open.\nOpenings",
                  "page_text": "Current openings\n0 jobs"},
    )


def queue_for(tmp_path: Path, runner: Callable[..., str], api: Api | None = None) -> FixQueue:
    return FixQueue(
        repo=tmp_path, workdir=tmp_path / "fix", api=api or Api(), run_id="run-1",
        report=lambda _line: None, runner=runner,
        prober=lambda _r, _a: {"ok": False, "note": "no"},
        checker=lambda _d, _f, _p: "refused",
    )


def until(condition: Callable[[], bool]) -> None:
    for _ in range(600):
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("it never happened")


def prompt_of(command: list[str]) -> str:
    return command[-1]


def test_a_direction_retries_an_unsolved_problem_with_the_direction_in_its_prompt(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    def runner(command: list[str], timeout: int = 0) -> str:
        prompts.append(prompt_of(command))
        return reply("impossible", summary=f"call {len(prompts)}")

    queue = queue_for(tmp_path, runner)
    try:
        problem = posting()
        queue.submit(problem)
        until(lambda: queue.statuses[problem.key].state == "unsolved")
        assert "THE APPLICANT" not in prompts[0]

        said = queue.guide("  the banner says  it closed ")
        assert said.startswith("Direction sent")
        until(lambda: len(prompts) == 2 and queue._pending == 0)
        assert "- the banner says it closed" in prompts[1]
        assert queue.statuses[problem.key].state == "unsolved"
    finally:
        queue.close()


def test_a_direction_during_a_call_joins_the_next_attempt_even_if_that_call_gave_up(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []
    calling, release = threading.Event(), threading.Event()

    def runner(command: list[str], timeout: int = 0) -> str:
        prompts.append(prompt_of(command))
        calling.set()
        release.wait(5)
        return reply("impossible")

    queue = queue_for(tmp_path, runner)
    try:
        problem = posting()
        queue.submit(problem)
        assert calling.wait(5)
        assert "next attempt" in queue.guide("look again", problem.key)
        release.set()
        until(lambda: len(prompts) >= 2 and queue._pending == 0)
        assert "- look again" not in prompts[0]
        assert "- look again" in prompts[1]
    finally:
        release.set()
        queue.close()


def test_a_direction_needs_a_problem_and_words(tmp_path: Path) -> None:
    queue = queue_for(tmp_path, lambda _c, timeout=0: reply("impossible"))
    try:
        assert queue.guide("  ") == "An empty direction was not sent."
        assert queue.guide("do this") == "The fixer has no problem to direct yet."
    finally:
        queue.close()


def test_a_problem_an_earlier_run_left_unsolved_is_listed_and_can_be_directed(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def runner(command: list[str], timeout: int = 0) -> str:
        calls.append(prompt_of(command))
        return reply("impossible")

    queue = queue_for(tmp_path, runner, Api(status="unsolved"))
    try:
        problem = posting()
        assert queue.submit(problem) is False
        assert calls == []
        assert queue.statuses[problem.key].state == "unsolved"
        queue.guide("it closed")
        until(lambda: len(calls) == 1)
        assert "- it closed" in calls[0]
    finally:
        queue.close()


def test_the_brake_does_not_stop_a_direction(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(command: list[str], timeout: int = 0) -> str:
        calls.append(prompt_of(command))
        return reply("impossible")

    queue = queue_for(tmp_path, runner)
    try:
        queue._braked = True
        problem = posting()
        assert queue.submit(problem) is False
        queue.guide("try this")
        until(lambda: len(calls) == 1)
    finally:
        queue.close()


def test_the_site_saying_closed_settles_a_posting_without_a_direction(tmp_path: Path) -> None:
    quote = "The job you are looking for is no longer open."
    api = Api()
    queue = queue_for(tmp_path, lambda _c, timeout=0: reply("closed", quote=quote), api)
    try:
        problem = posting()
        queue.submit(problem)
        until(lambda: queue.closed_quote(problem.key) == quote)
        assert queue.statuses[problem.key].state == "closed"
        assert [update["status"] for update in api.updates[-2:]] == ["unsolved", "archived"]
    finally:
        queue.close()


def test_the_model_reading_zero_results_alone_does_not_remove_a_row(tmp_path: Path) -> None:
    queue = queue_for(tmp_path, lambda _c, timeout=0: reply("closed", quote="0 jobs"))
    try:
        problem = posting()
        queue.submit(problem)
        until(lambda: queue.statuses[problem.key].state == "unsolved")
        assert queue.closed_quote(problem.key) is None
    finally:
        queue.close()


def test_your_direction_lets_the_same_words_settle_it(tmp_path: Path) -> None:
    answers = iter([reply("impossible"), reply("closed", quote="0 jobs")])
    queue = queue_for(tmp_path, lambda _c, timeout=0: next(answers))
    try:
        problem = posting()
        queue.submit(problem)
        until(lambda: queue.statuses[problem.key].state == "unsolved")
        queue.guide("the search went in and it is gone")
        until(lambda: queue.closed_quote(problem.key) == "0 jobs")
    finally:
        queue.close()


def test_words_that_are_not_on_the_page_settle_nothing(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(command: list[str], timeout: int = 0) -> str:
        calls.append(prompt_of(command))
        return reply("closed", quote="This job closed yesterday.")

    queue = queue_for(tmp_path, runner)
    try:
        problem = posting()
        queue.submit(problem)
        queue.guide("it closed")
        until(lambda: queue.statuses[problem.key].state == "unsolved" and queue._pending == 0)
        assert queue.closed_quote(problem.key) is None
        assert "is not on the page" in calls[1]
    finally:
        queue.close()


def test_only_a_missing_posting_can_be_called_closed(tmp_path: Path) -> None:
    queue = queue_for(tmp_path, lambda _c, timeout=0: reply("closed", quote="closed"))
    problem = lane.Problem(key="wall|x.example", cause="wall", question="Apply", note="",
                           target={"kind": "route", "goal": "form"}, site="x.example",
                           issue_id="i1", run_id="run-1")
    try:
        queue.submit(problem)
        until(lambda: queue.statuses[problem.key].state == "unsolved")
        assert queue.closed_quote(problem.key) is None
    finally:
        queue.close()


def test_the_prompt_shows_the_page_and_keeps_scripts_out_of_it() -> None:
    problem = lane.Problem(
        **{**posting().__dict__,
           "page_files": {"frame-0.html": b"<script>var a=1</script><div id='toast'>x</div>",
                          "screen.png": b"\x89PNG"}}
    )
    text = lane.prompt(problem, None)
    assert "no longer open" in text and "0 jobs" in text
    assert "<div id='toast'>x</div>" in text
    assert "var a=1" not in text and "PNG" not in text
    assert 'kind "closed"' in text


class Client:
    def __init__(self) -> None:
        self.deliver: Any = None
        self.closed = False

    def start(self, on_message: Any) -> None:
        self.deliver = on_message

    def send(self, _message: dict[str, Any]) -> None:
        return None


def test_the_pages_direction_reaches_the_fixer_through_the_link() -> None:
    client = Client()
    link = RunLink(client)  # type: ignore[arg-type]
    seen: list[tuple[str, str | None]] = []
    client.deliver({"type": "guide", "key": "k", "text": "before a fixer"})
    assert seen == []
    link.on_guide(lambda text, key: seen.append((text, key)) or "sent")  # type: ignore[func-returns-value]
    client.deliver({"type": "guide", "key": "k", "text": "look at the toast"})
    client.deliver({"type": "guide", "key": "", "text": "the latest"})
    assert seen == [("look at the toast", "k"), ("the latest", None)]
