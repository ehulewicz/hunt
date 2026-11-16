"""The press on fake Chrome: checks, the pending lock, closed commands, outcomes, scorecard."""

import asyncio
import base64
import json
import threading
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest
from starlette.testclient import TestClient
from test_device_drive import GOOD, LEVER, FakeCdp, call, ended, lease, make_agent

from personal_os_worker.device import drive, press
from personal_os_worker.device.motion import Motion
from personal_os_worker.device.server import Agent, create_app

REJECTED = "There was an error verifying your application. Please try again."
CHALLENGE = "https://newassets.hcaptcha.com/captcha/v1/a/static/hcaptcha.html#frame=challenge"
JPEG = b"\xff\xd8\xff\xe0jpeg"


class PressCdp(FakeCdp):
    """Chrome with a submit button at (100, 600) and a page the press reads after it."""

    def __init__(self, targets: list[dict[str, Any]]) -> None:
        super().__init__(targets)
        self.name = "Submit application"
        self.html = "<button>Submit application</button>"
        self.after_url = LEVER + "/apply"
        self.page: dict[str, Any] = {"text": "Submit your application", "frames": [],
                                     "errors": 0}
        self.locks: Any = None
        self.lock_at_press: str | None = "unset"
        self.border = [100, 600, 260, 600, 260, 640, 100, 640]

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        await super().send(method, params, session_id)
        params = params or {}
        if method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed":
            self.lock_at_press = self.locks.state_for("L1")
        answers: dict[str, Any] = {
            "Target.getTargets": {"targetInfos": self.targets},
            "Target.attachToTarget": {"sessionId": "S1"},
            "Page.createIsolatedWorld": {"executionContextId": 9},
            "DOM.getDocument": {"root": {"nodeId": 1}},
            "DOM.querySelector": {"nodeId": 0 if params.get("selector") == "#none" else 42},
            "Accessibility.getPartialAXTree": {"nodes": [{"name": {"value": self.name}}]},
            "DOM.getOuterHTML": {"outerHTML": self.html},
            "DOM.getBoxModel": {"model": {"border": self.border}},
            "Page.getLayoutMetrics": {"cssLayoutViewport": {"clientWidth": 1280,
                                                            "clientHeight": 800}},
            "Target.getTargetInfo": {"targetInfo": {"url": self.after_url}},
            "Runtime.evaluate": {"result": {"value": {"url": self.after_url, **self.page}}},
            "Page.captureScreenshot": {"data": base64.b64encode(JPEG).decode()},
        }
        return dict(answers.get(method, {"ok": True}))


@pytest.fixture
def chrome() -> PressCdp:
    return PressCdp([{"targetId": "T1", "type": "page", "url": LEVER + "/apply"}])


@pytest.fixture
def agent(tmp_path: Path, chrome: PressCdp) -> Agent:
    return make_agent(tmp_path, {}, chrome)


@pytest.fixture
def client(agent: Agent) -> TestClient:
    agent.drive.switch.set(True)
    with TestClient(create_app(agent, background=False)) as made:
        yield made  # type: ignore[misc]


@pytest.fixture
def gate(agent: Agent, chrome: PressCdp) -> threading.Event:
    """Holds the press at its 3-8 s wait until the test sets it."""
    opened = threading.Event()

    async def sleep(seconds: float) -> None:
        if seconds >= press.WAIT_SECONDS[0]:
            await asyncio.to_thread(opened.wait, 5)
        await asyncio.sleep(0)

    agent.drive.on_submit = press.Press(agent.drive, motion=Motion(7), sleep=sleep)
    chrome.locks = agent.drive.locks
    return opened


def submit(socket: Any, experiment: int = 1, selector: str = "#submit") -> None:
    socket.send_json({"type": "submit", "experiment": experiment, "selector": selector,
                      "knobs": {"fill": "fast"}})


def attempts(agent: Agent) -> list[dict[str, Any]]:
    path = agent.drive.scorecard.path
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def pressed(socket: Any) -> tuple[dict[str, Any], str]:
    """The outcome message, then the reason the lease ended."""
    while (message := socket.receive_json())["type"] != "outcome":
        assert message["type"] != "ended", message
    return message, ended(socket)


@pytest.mark.parametrize(
    ("after", "page", "outcome", "lock"),
    [
        ("/thanks", {"text": "Application submitted! Thank you for applying."},
         "submitted", "submitted"),
        ("/apply", {"text": REJECTED}, "captcha_rejected", "burned"),
        ("/apply", {"frames": [{"src": CHALLENGE, "width": 400, "height": 580,
                                "visible": True}]}, "challenge_shown", "burned"),
        ("/apply", {"errors": 2}, "validation", None),
        ("/apply", {}, "unconfirmed", "unconfirmed"),
    ],
)
def test_experiment_1_presses_once_with_cdp_and_locks_by_outcome(
    client: Any, agent: Agent, chrome: PressCdp, gate: threading.Event,
    after: str, page: dict[str, Any], outcome: str, lock: str | None,
) -> None:
    chrome.after_url = LEVER + after
    chrome.page.update(page)
    gate.set()
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        submit(socket)
        message, reason = pressed(socket)
    assert message == {"type": "outcome", "outcome": outcome, "url": LEVER + after,
                       "screenshot": base64.b64encode(JPEG).decode()}
    assert reason == f"the press finished: {outcome}"
    assert chrome.lock_at_press == "pending"
    assert agent.drive.locks.state_for("L1") == lock
    methods = chrome.methods()
    assert methods.index("DOM.disable") < methods.index("Input.dispatchMouseEvent")
    assert methods.index("Accessibility.disable") < methods.index("Input.dispatchMouseEvent")
    mouse = [params for method, params, _ in chrome.sent if method == "Input.dispatchMouseEvent"]
    kinds = [params["type"] for params in mouse]
    assert kinds[-2:] == ["mousePressed", "mouseReleased"] and kinds.count("mousePressed") == 1
    assert set(kinds[:-2]) == {"mouseMoved"} and len(kinds) >= 10
    assert 100 <= mouse[-1]["x"] <= 260 and 600 <= mouse[-1]["y"] <= 640
    assert chrome.closed
    [line] = attempts(agent)
    assert line["outcome"] == outcome and line["experiment"] == 1
    assert line["debugger_attached_at_press"] is True and line["input_kind"] == "cdp"
    assert line["knobs"]["fill"] == "fast" and 3 <= line["knobs"]["agent_wait_s"] <= 8
    assert line["url"] == LEVER + "/apply"
    assert Path(line["screenshot"]).read_bytes() == JPEG
    assert client.get("/drive", headers=GOOD).json()["attempts"] == [line]


def test_commands_and_a_second_submit_are_refused_once_the_press_begins(
    client: Any, agent: Agent, chrome: PressCdp, gate: threading.Event
) -> None:
    chrome.after_url = LEVER + "/thanks"
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        submit(socket)
        closed = call(socket, 1, "DOM.getDocument")
        assert closed["error"] == "the press has begun; commands are closed"
        submit(socket)
        assert socket.receive_json()["error"] == "the press has begun; commands are closed"
        socket.send_json({"type": "upload_file", "name": "cv.pdf", "data_b64": "eA=="})
        assert socket.receive_json()["type"] == "error"
        gate.set()
        assert pressed(socket)[0]["outcome"] == "submitted"
    assert [line["outcome"] for line in attempts(agent)] == ["submitted"]
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        assert lease(socket)["reason"] == "the listing is locked (submitted)"


@pytest.mark.parametrize(
    ("message", "name", "html", "error"),
    [
        ({"experiment": 5}, "Submit application", "", "not implemented yet"),
        ({"experiment": True}, "Submit application", "", "not implemented yet"),
        ({"selector": "#none"}, "Submit application", "", "the selector matches nothing"),
        ({}, "Next", "<button>Next</button>", "the selector is not a submit control"),
        ({"selector": ""}, "Submit", "", "send the submit control's selector"),
    ],
)
def test_a_submit_that_names_no_submit_control_is_refused_and_the_lease_goes_on(
    client: Any, agent: Agent, chrome: PressCdp, gate: threading.Event,
    message: dict[str, Any], name: str, html: str, error: str,
) -> None:
    chrome.name, chrome.html = name, html
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        socket.send_json({"type": "submit", "experiment": 1, "selector": "#s", **message})
        assert socket.receive_json() == {"type": "error", "error": error}
        assert "result" in call(socket, 1, "DOM.getDocument")
    assert agent.drive.locks.state_for("L1") is None
    assert "Input.dispatchMouseEvent" not in chrome.methods()
    assert attempts(agent) == []


def test_the_submit_text_counts_when_the_accessible_name_is_empty(
    client: Any, agent: Agent, chrome: PressCdp, gate: threading.Event
) -> None:
    chrome.name, chrome.html = "", '<button class="x"><span>Submit application</span></button>'
    chrome.after_url = LEVER + "/thanks"
    gate.set()
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        submit(socket)
        assert pressed(socket)[0]["outcome"] == "submitted"


def test_a_locked_listing_or_a_control_out_of_view_is_never_pressed(
    client: Any, agent: Agent, chrome: PressCdp, gate: threading.Event
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        agent.drive.locks.lock("L1", LEVER, "unconfirmed")
        submit(socket)
        assert socket.receive_json()["error"] == "the listing is locked (unconfirmed)"
        agent.drive.locks.release("L1")
        chrome.border = [100, 900, 260, 900, 260, 940, 100, 940]
        submit(socket)
        assert socket.receive_json()["error"] == "the submit control is not in view"
    assert "Input.dispatchMouseEvent" not in chrome.methods()


def test_a_stop_before_the_click_releases_the_lock_and_presses_nothing(
    client: Any, agent: Agent, chrome: PressCdp, gate: threading.Event
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        submit(socket)
        call(socket, 1, "DOM.getDocument")
        assert agent.drive.locks.state_for("L1") == "pending"
        client.portal.call(agent.drive.stop)
        assert ended(socket) == "stopped from the Hunt page"
    gate.set()
    for _ in range(100):
        if chrome.closed:
            break
        client.portal.call(asyncio.sleep, 0.01)
    assert chrome.closed
    assert agent.drive.locks.state_for("L1") is None
    assert "mousePressed" not in {params.get("type") for _, params, _ in chrome.sent}
    assert attempts(agent) == []
    assert any(line["outcome"].startswith("abandoned") for line in
               map(json.loads, agent.drive.audit.path.read_text().splitlines()))


def test_an_upload_path_is_the_one_windows_chrome_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    path = drive.chrome_file_path(Path("/home/box/.local/state/drive/files/cv.pdf"))
    assert PureWindowsPath(path) == PureWindowsPath(
        "\\\\wsl.localhost\\Ubuntu\\home\\box\\.local\\state\\drive\\files\\cv.pdf"
    )
    monkeypatch.delenv("WSL_DISTRO_NAME")
    assert drive.chrome_file_path(Path("/tmp/cv.pdf")) == "/tmp/cv.pdf"
