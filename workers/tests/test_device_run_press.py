"""A linked `os hunt apply --submit` run's press, as the agent makes it on fake Chrome."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_device_drive import LEVER, make_agent
from test_device_press import PressCdp, attempts

from personal_os_worker.device import press
from personal_os_worker.device.motion import Motion
from personal_os_worker.device.runs import RunState
from personal_os_worker.device.server import Agent


async def instant(_seconds: float) -> None:
    await asyncio.sleep(0)


@pytest.fixture
def chrome() -> PressCdp:
    return PressCdp([{"targetId": "T1", "type": "page", "url": LEVER + "/apply"}])


@pytest.fixture
def agent(tmp_path: Path, chrome: PressCdp) -> Agent:
    made = make_agent(tmp_path, {}, chrome)
    made.drive.switch.set(True)
    made.drive.on_submit = press.Press(made.drive, motion=Motion(7), sleep=instant)
    chrome.locks = made.drive.locks
    return made


def ask(agent: Agent, kind: str = "press", **extra: Any) -> dict[str, Any]:
    message = {"type": kind, "listing_id": "L1", "url": LEVER + "/apply", "target": "T1",
               "selector": "#btn-submit", "experiment": 1, "fill_seconds": 95.0, **extra}
    return asyncio.run(agent.press_for_run(message))


def clicks(chrome: PressCdp) -> list[str]:
    return [params["type"] for method, params, _ in chrome.sent
            if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed"]


def test_a_runs_press_is_experiment_1_on_its_own_tab(agent: Agent, chrome: PressCdp) -> None:
    chrome.after_url = LEVER + "/thanks"
    reply = ask(agent)
    assert reply == {"type": "press_outcome", "outcome": "submitted", "url": LEVER + "/thanks",
                     "error": None}
    assert chrome.last("Target.attachToTarget")[1] == {"targetId": "T1", "flatten": True}
    assert chrome.lock_at_press == "pending" and clicks(chrome) == ["mousePressed"]
    methods = chrome.methods()
    assert "Runtime.enable" not in methods and "Target.setAutoAttach" not in methods
    assert methods.index("DOM.disable") < methods.index("Input.dispatchMouseEvent")
    assert methods.index("Accessibility.disable") < methods.index("Input.dispatchMouseEvent")
    assert agent.drive.locks.state_for("L1") == "submitted"
    assert agent.drive.lease is None and chrome.closed
    [line] = attempts(agent)
    assert line["experiment"] == 1 and line["input_kind"] == "cdp"
    assert line["knobs"]["via"] == "os hunt apply --submit" and line["fill_seconds"] >= 95


@pytest.mark.parametrize("state", ["pending", "submitted", "unconfirmed", "burned"])
def test_a_locked_listing_is_refused_and_nothing_attaches(
    agent: Agent, chrome: PressCdp, state: str
) -> None:
    agent.drive.locks.lock("L1", LEVER, state)
    reply = ask(agent)
    assert reply["outcome"] is None and reply["error"] == f"the listing is locked ({state})"
    assert chrome.sent == [] and attempts(agent) == []
    ready = ask(agent, "press_ready")
    assert ready["ok"] is False and ready["locked"] == state


def test_the_switch_off_refuses_and_nothing_attaches(agent: Agent, chrome: PressCdp) -> None:
    agent.drive.switch.set(False)
    assert ask(agent)["error"] == "the drive switch is off"
    assert ask(agent, "press_ready") == {"type": "press_ready", "ok": False,
                                         "reason": "the drive switch is off", "locked": None}
    assert chrome.sent == [] and agent.drive.locks.state_for("L1") is None


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"url": "https://boards.greenhouse.io/acme/jobs/1"},
         "not a device press host: boards.greenhouse.io"),
        ({"experiment": 2}, "a run presses by experiment 1 only"),
        ({"target": ""}, "send the run's tab"),
        ({"listing_id": ""}, "send the listing"),
    ],
)
def test_a_press_the_run_cannot_make_is_refused(
    agent: Agent, chrome: PressCdp, extra: dict[str, Any], error: str
) -> None:
    assert ask(agent, **extra)["error"] == error
    assert chrome.sent == []


def test_another_lease_refuses_the_press(agent: Agent, chrome: PressCdp) -> None:
    agent.drive.lease = object()  # type: ignore[assignment]
    assert ask(agent)["error"] == "another lease is active"
    assert ask(agent, "press_ready")["ok"] is False and chrome.sent == []


def test_a_selector_that_names_no_submit_releases_the_lease_unpressed(
    agent: Agent, chrome: PressCdp
) -> None:
    chrome.name, chrome.html = "Next", "<button>Next</button>"
    reply = ask(agent)
    assert reply["outcome"] is None and reply["error"] == "the selector is not a submit control"
    assert clicks(chrome) == [] and agent.drive.locks.state_for("L1") is None
    assert agent.drive.lease is None and chrome.closed


def test_the_ready_answer_says_yes_when_it_would_press(agent: Agent) -> None:
    assert ask(agent, "press_ready") == {"type": "press_ready", "ok": True, "reason": None,
                                         "locked": None}


class Writer:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        self.lines.extend(json.loads(line) for line in data.splitlines())

    async def drain(self) -> None:
        return None


def test_the_run_link_carries_the_request_and_its_answer(agent: Agent) -> None:
    writer = Writer()

    async def go() -> None:
        agent.runs._writer = writer  # type: ignore[assignment]
        state = RunState(key="k", origin="terminal")
        await agent.runs._from_run(state, {"type": "press_ready", "listing_id": "L1",
                                           "url": LEVER + "/apply"})
        while agent.runs._tasks:
            await asyncio.sleep(0)

    asyncio.run(go())
    assert writer.lines == [{"type": "press_ready", "ok": True, "reason": None, "locked": None}]
