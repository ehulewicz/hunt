"""OS input with no debugger attached, on fake Chrome and a fake `OsInput`."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from test_device_drive import GOOD, LEVER, call, lease, make_agent
from test_device_press import PressCdp, attempts, pressed, submit

from personal_os_worker.device import drive_os, press
from personal_os_worker.device.motion import Motion, Point
from personal_os_worker.device.osinput import (
    ORIGIN_EXPRESSION,
    OsInput,
    OsInputError,
    Ran,
    ViewportOrigin,
)
from personal_os_worker.device.server import Agent, create_app

# A 1296x940 window at the screen's corner, an 8 px frame and 132 px of chrome, at 125%.
ORIGIN = [0, 0, 1296, 1280, 940, 800, 1.25]
RECT = (0, 0, 1700, 1200)


class OsCdp(PressCdp):
    """PressCdp that also answers the origin read and the tab's title."""

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        answer = await super().send(method, params, session_id)
        if method == "Runtime.evaluate" and (params or {}).get("expression") == ORIGIN_EXPRESSION:
            return {"result": {"value": json.dumps(ORIGIN)}}
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"url": self.after_url, "title": "Pivotal - Apply"}}
        return answer


class FakeOs(OsInput):
    """Records each batch and what the agent held on the tab when the button went down."""

    def __init__(self, agent: Agent, chrome: OsCdp) -> None:
        super().__init__(motion=Motion(3))
        self.agent, self.chrome = agent, chrome
        self.batches: list[tuple[str | int, list[dict[str, Any]]]] = []
        self.at_down: dict[str, Any] = {}
        self.refuse: str | None = None

    def run(self, window: str | int, ops: list[dict[str, Any]]) -> Ran:
        if self.refuse is not None:
            raise OsInputError(self.refuse)
        self.batches.append((window, ops))
        if any(op["op"] == "down" for op in ops):
            held = self.agent.drive.lease
            self.at_down = {
                "lock": self.agent.drive.locks.state_for("L1"),
                "cdp": None if held is None else held.cdp,
                "attaches": self.chrome.methods().count("Target.attachToTarget"),
                "closed": self.chrome.closed,
            }
        return Ran(Point(50, 50), RECT, 77)


class FakeStream:
    closed = False

    async def close(self) -> None:
        FakeStream.closed = True


@pytest.fixture
def chrome() -> OsCdp:
    return OsCdp([{"targetId": "T1", "type": "page", "url": LEVER + "/apply"}])


@pytest.fixture
def agent(tmp_path: Path, chrome: OsCdp) -> Agent:
    return make_agent(tmp_path, {}, chrome)


@pytest.fixture
def fake_os(agent: Agent, chrome: OsCdp) -> FakeOs:
    made = FakeOs(agent, chrome)

    async def sleep(seconds: float) -> None:
        await asyncio.sleep(0)

    pressing = press.Press(agent.drive, motion=Motion(7), sleep=sleep, os_input=made)
    agent.drive.on_submit, agent.drive.on_os = pressing, pressing.os
    chrome.locks = agent.drive.locks
    FakeStream.closed = False
    agent.stream = FakeStream()  # type: ignore[assignment]
    return made


@pytest.fixture
def client(agent: Agent) -> TestClient:
    agent.drive.switch.set(True)
    with TestClient(create_app(agent, background=False)) as made:
        yield made  # type: ignore[misc]


@pytest.mark.parametrize("experiment", [2, 3, 4])
def test_an_os_press_detaches_everything_then_clicks_and_reattaches_to_watch(
    client: Any, agent: Agent, chrome: OsCdp, fake_os: FakeOs, experiment: int
) -> None:
    chrome.after_url = LEVER + "/thanks"
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        submit(socket, experiment)
        message, _reason = pressed(socket)
    assert message["outcome"] == "submitted"
    assert FakeStream.closed
    assert fake_os.at_down == {"lock": "pending", "cdp": None, "attaches": 1, "closed": True}
    methods = chrome.methods()
    assert methods.index("Target.detachFromTarget") > methods.index("Runtime.evaluate")
    assert methods.count("Target.attachToTarget") == 2
    assert "Input.dispatchMouseEvent" not in methods
    windows = {window for window, _ in fake_os.batches}
    assert windows == {"Pivotal - Apply"}
    [move] = [op for _, ops in fake_os.batches for op in ops if op["op"] == "move"]
    x, y, _delay = move["points"][-1]
    assert 135 <= x <= 335 and 915 <= y <= 950
    [line] = attempts(agent)
    assert line["experiment"] == experiment and line["outcome"] == "submitted"
    assert line["debugger_attached_at_press"] is False and line["input_kind"] == "os"
    assert agent.drive.locks.state_for("L1") == "submitted"


def test_an_os_press_whose_window_cannot_be_focused_presses_nothing(
    client: Any, agent: Agent, chrome: OsCdp, fake_os: FakeOs
) -> None:
    fake_os.refuse = "no Chrome window was found"
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        submit(socket, 2)
        while (message := socket.receive_json())["type"] != "error":
            pass
        assert "could not be focused" in message["error"]
    assert agent.drive.locks.state_for("L1") is None
    assert attempts(agent) == []


def test_os_ops_run_detached_until_the_driver_attaches(
    client: Any, agent: Agent, chrome: OsCdp, fake_os: FakeOs
) -> None:
    ops = [{"op": "click_at", "x": 120, "y": 610}, {"op": "type", "text": "hi"},
           {"op": "key", "name": "Tab"}, {"op": "wheel", "dy": 200}, {"op": "wait", "ms": 50}]
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        socket.send_json({"type": "os", "ops": ops})
        assert socket.receive_json() == {"type": "os_done", "ok": True, "cursor": [50.0, 50.0]}
        assert "detached for OS input" in call(socket, 1, "DOM.getDocument")["error"]
        socket.send_json({"type": "os", "ops": [{"op": "key", "name": "enter"}]})
        assert socket.receive_json()["ok"] is True
        socket.send_json({"type": "attach"})
        assert socket.receive_json() == {"type": "attached", "target": "T1"}
        assert "result" in call(socket, 2, "DOM.getDocument")
    assert FakeStream.closed
    assert chrome.methods().count("Runtime.evaluate") == 1  # measured once, while attached
    _focus, (window, batch), _focus2, (_, second) = fake_os.batches
    kinds = [op["op"] for op in batch]
    assert window == "Pivotal - Apply" and kinds[0] == "move" and "down" in kinds
    assert [op["chunk"] for op in batch if op["op"] == "text"] == ["h", "i"]
    assert [op["vk"] for op in batch if op["op"] == "key"] == [0x09]
    assert all(op["delta"] < 0 for op in batch if op["op"] == "wheel")
    assert [op["vk"] for op in second if op["op"] == "key"] == [0x0D]


def test_os_ops_are_refused_on_a_locked_listing_or_an_unfocusable_window(
    client: Any, agent: Agent, chrome: OsCdp, fake_os: FakeOs
) -> None:
    click = {"type": "os", "ops": [{"op": "click_at", "x": 120, "y": 610}]}
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        agent.drive.locks.lock("L1", LEVER, "burned")
        socket.send_json(click)
        assert socket.receive_json() == {"type": "os_done", "ok": False,
                                         "error": "the listing is locked (burned)"}
        agent.drive.locks.release("L1")
        fake_os.refuse = "no Chrome window was found"
        socket.send_json(click)
        reply = socket.receive_json()
        assert reply["ok"] is False and "could not be focused" in reply["error"]
    assert fake_os.batches == []


def test_os_ops_are_refused_when_the_switch_is_off(tmp_path: Path, chrome: OsCdp) -> None:
    agent = make_agent(tmp_path, {}, chrome)
    held = drive_os.OsDrive(agent.drive, FakeOs(agent, chrome))
    leased = press.Lease("L1", LEVER, "jobs.lever.co", LEVER, 0.0, 0.0)
    reply = asyncio.run(held(leased, {"type": "os", "ops": [{"op": "wait", "ms": 1}]}))
    assert reply == {"type": "os_done", "ok": False, "error": "the drive switch is off"}


@pytest.mark.parametrize(
    ("ops", "error"),
    [
        ([], "send 1 to 50 ops"),
        ([{"op": "click_at", "x": "1", "y": 2}], "click_at needs a number x"),
        ([{"op": "type", "text": ""}], "type needs text"),
        ([{"op": "key", "name": "f13"}], "unknown key 'f13'"),
        ([{"op": "wait", "ms": 60_000}], "wait needs a number ms"),
        ([{"op": "drag"}], "unknown op 'drag'"),
    ],
)
def test_bad_ops_are_refused_by_name(ops: list[Any], error: str) -> None:
    with pytest.raises(drive_os.OsRefused, match=error):
        drive_os.parse_ops(ops)


def test_a_click_outside_the_window_is_refused_before_anything_runs() -> None:
    origin = ViewportOrigin.parse(ORIGIN)
    ran = Ran(Point(50, 50), RECT, 77)
    ops = drive_os.parse_ops([{"op": "click_at", "x": 2000, "y": 10}])
    with pytest.raises(drive_os.OsRefused, match="outside the Chrome window"):
        drive_os.plan(Motion(1), origin, ran, ops)
    inside = drive_os.plan(Motion(1), origin, ran, drive_os.parse_ops(
        [{"op": "click_at", "x": 100, "y": 600}]))
    assert inside[0]["points"][-1][:2] == [135, 915]


def test_the_hunt_page_opens_no_stream_while_the_tab_is_detached(
    client: Any, agent: Agent, chrome: OsCdp, fake_os: FakeOs
) -> None:
    opened: list[Any] = []
    agent.stream_factory = lambda viewer: opened.append(viewer)  # type: ignore[assignment,func-returns-value]
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        socket.send_json({"type": "os", "ops": [{"op": "wait", "ms": 1}]})
        assert socket.receive_json()["ok"] is True
        assert agent.drive.cdp_off()
        sent: list[dict[str, Any]] = []

        class Viewer:
            async def send_json(self, message: dict[str, Any]) -> None:
                sent.append(message)

        client.portal.call(agent.attach, Viewer(), {})
    assert opened == [] and sent[-1]["state"] == "unavailable"
