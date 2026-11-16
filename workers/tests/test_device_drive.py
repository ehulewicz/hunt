import json
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from personal_os_worker.core.config import CliConfig
from personal_os_worker.device import drive
from personal_os_worker.device.drive_state import LockStore, normalize_posting
from personal_os_worker.device.identity import DeviceIdentity
from personal_os_worker.device.server import Agent, create_app
from personal_os_worker.device.update import Checkout

PAGE = "https://hunt.example.com"
GOOD = {"Authorization": "Bearer good"}
LEVER = "https://jobs.lever.co/pivotal/abc-123"
CONFIG = CliConfig("http://127.0.0.1:8000", "https://example.supabase.co", "key")
DEVICE = DeviceIdentity(
    id="33333333-3333-4333-8333-333333333333", name="DESKTOP-HOME",
    url="https://desktop-home.tail1234.ts.net",
)


class FakeVerifier:
    async def verify(self, token: str) -> bool:
        return token == "good"


class FakePresence:
    identity = DEVICE
    ready, problem = True, None
    last = Checkout(commit="c" * 40, branch="main", dirty=False, ahead=0, behind=0)


class FakeCdp:
    """Chrome as the drive sees it: answers, a record of what was sent, and events to fire."""

    def __init__(self, targets: list[dict[str, Any]]) -> None:
        self.targets = targets
        self.sent: list[tuple[str, dict[str, Any], str | None]] = []
        self.closed = False
        self.on_event: Any = None

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        self.sent.append((method, params or {}, session_id))
        answers: dict[str, dict[str, Any]] = {
            "Target.getTargets": {"targetInfos": self.targets},
            "Target.createTarget": {"targetId": "NEW"},
            "Target.attachToTarget": {"sessionId": "S1"},
            "Page.createIsolatedWorld": {"executionContextId": 7},
        }
        return answers.get(method, {"ok": True})

    async def close(self) -> None:
        self.closed = True

    def last(self, method: str) -> tuple[str, dict[str, Any], str | None]:
        return next(sent for sent in reversed(self.sent) if sent[0] == method)

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.sent]


@pytest.fixture
def statuses() -> dict[str, str]:
    return {}


@pytest.fixture
def chrome() -> FakeCdp:
    return FakeCdp([
        {"targetId": "OTHER", "type": "page", "url": "https://mail.google.com/"},
        {"targetId": "T1", "type": "page", "url": LEVER + "/apply"},
    ])


@pytest.fixture
def agent(tmp_path: Path, statuses: dict[str, str], chrome: FakeCdp) -> Agent:
    made = Agent(
        CONFIG, DEVICE,
        presence=FakePresence(),  # type: ignore[arg-type]
        verifier=FakeVerifier(),  # type: ignore[arg-type]
        update=lambda: "up to date",
        ensure_chrome=lambda: None,
        application_status=statuses.get,
        drive_directory=tmp_path,
    )

    async def connect(on_event: Any) -> FakeCdp:
        chrome.on_event = on_event
        return chrome

    made.drive.connect = connect  # type: ignore[assignment]
    made.drive.files.mkdir(parents=True)
    (made.drive.files / "resume.pdf").write_bytes(b"%PDF")
    made.drive.chrome_path = str  # type: ignore[assignment]
    return made


@pytest.fixture
def client(agent: Agent) -> TestClient:
    agent.drive.switch.set(True)
    with TestClient(create_app(agent, background=False)) as made:
        yield made  # type: ignore[misc]


def audit(agent: Agent) -> list[dict[str, Any]]:
    path = agent.drive.audit.path
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def lease(socket: Any, listing: str = "L1", url: str = LEVER) -> dict[str, Any]:
    socket.send_json({"type": "lease", "listing_id": listing, "url": url})
    return dict(socket.receive_json())


def call(socket: Any, number: int, method: str, **params: Any) -> dict[str, Any]:
    socket.send_json({"id": number, "method": method, "params": params})
    return dict(socket.receive_json())


def refused(client: TestClient, **message: Any) -> str:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        reply = lease(socket, **message)
        assert reply["type"] == "refused"
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    return str(reply["reason"])


def ended(socket: Any) -> str:
    while (message := socket.receive_json())["type"] != "ended":
        pass
    with pytest.raises(WebSocketDisconnect):
        socket.receive_json()
    return str(message["reason"])


def test_a_bearer_token_and_no_origin_are_required(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer bad"}, {**GOOD, "origin": PAGE}):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/drive", headers=headers
        ) as socket:
            socket.receive_json()


def test_a_lease_attaches_to_the_postings_tab_only(client: TestClient, chrome: FakeCdp) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        assert lease(socket) == {"type": "leased", "target": "T1"}
        assert ("Target.attachToTarget", {"targetId": "T1", "flatten": True}, None) in chrome.sent
        assert "Target.setAutoAttach" not in chrome.methods()
        socket.send_json({"type": "release"})
        assert ended(socket) == "released by the driver"
    assert chrome.closed


def test_a_lease_with_no_open_tab_creates_one(client: TestClient, chrome: FakeCdp) -> None:
    chrome.targets = []
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        assert lease(socket)["target"] == "NEW"
    assert ("Target.createTarget", {"url": LEVER}, None) in chrome.sent


def test_the_switch_off_refuses_and_turning_it_off_ends_the_lease(
    client: TestClient, agent: Agent
) -> None:
    agent.drive.switch.set(False)
    assert refused(client) == "the drive switch is off"
    assert client.post("/drive", json={"on": True}).status_code == 401
    assert client.post("/drive", json={"on": True}, headers=GOOD).json()["on"] is True
    assert client.get("/status", headers=GOOD).json()["device"]["drive_on"] is True
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        status = client.get("/drive", headers=GOOD).json()
        assert status["lease"]["listing_id"] == "L1"
        client.post("/drive", json={"on": False}, headers=GOOD)
        assert ended(socket) == "the drive switch was turned off"
    assert client.get("/drive", headers=GOOD).json() == {"on": False, "lease": None}


def test_a_lease_off_an_ats_host_or_on_a_locked_or_applied_listing_is_refused(
    client: TestClient, agent: Agent, statuses: dict[str, str]
) -> None:
    assert refused(client, url="https://evil.example/jobs").startswith("not an ATS host")
    assert refused(client, url="https://jobs.lever.co.evil.example/x").startswith("not an ATS")
    agent.drive.locks.lock("L9", LEVER + "/thanks", "submitted")
    assert refused(client, listing="L2") == "the listing is locked (submitted)"
    statuses["L3"] = "applied"
    assert refused(client, listing="L3", url="https://jobs.ashbyhq.com/a/b") == (
        "the application is already applied"
    )


def test_one_lease_at_a_time_and_none_during_a_page_run(
    client: TestClient, agent: Agent
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        assert refused(client, listing="L2") == "another lease is active"
    agent.runs.current = type("S", (), {"active": True})()  # type: ignore[assignment]
    assert refused(client) == "a page run is active"


def test_methods_off_the_allowlist_are_refused_and_audited(
    client: TestClient, agent: Agent, chrome: FakeCdp
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        for number, method in enumerate(("Network.getCookies", "Runtime.enable",
                                         "Target.getTargets", "Storage.getCookies"), 1):
            assert call(socket, number, method) == {"id": number, "error": f"not allowed: {method}"}
        assert call(socket, 9, "DOM.getDocument")["result"] == {"ok": True}
    assert ("DOM.getDocument", {}, "S1") in chrome.sent
    assert "Runtime.enable" not in chrome.methods()
    outcomes = {line["what"]: line["outcome"] for line in audit(agent)}
    assert outcomes["Network.getCookies"] == "refused: not allowed: Network.getCookies"
    assert outcomes["DOM.getDocument"] == "forwarded"


def test_evaluate_runs_only_in_the_leases_isolated_world(
    client: TestClient, chrome: FakeCdp
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        assert "error" in call(socket, 1, "Runtime.evaluate", expression="1")
        assert "error" in call(socket, 2, "Runtime.evaluate", expression="1", contextId=7)
        assert call(socket, 3, "Page.createIsolatedWorld", frameId="T1")["result"] == {
            "executionContextId": 7
        }
        reply = call(socket, 4, "Runtime.evaluate", expression="1", contextId=7,
                     returnByValue=False)
        assert "result" in reply
    assert chrome.last("Runtime.evaluate") == (
        "Runtime.evaluate", {"expression": "1", "contextId": 7, "returnByValue": True}, "S1"
    )


def test_navigate_stays_on_the_lease_host_and_off_locked_listings(
    client: TestClient, agent: Agent
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        off = call(socket, 1, "Page.navigate", url="https://evil.example/")
        assert off["error"] == "navigate stays on the lease host"
        assert "result" in call(socket, 2, "Page.navigate", url=LEVER + "/apply")
        agent.drive.locks.lock("L7", "https://jobs.lever.co/other/xyz", "burned")
        locked = call(socket, 3, "Page.navigate", url="https://jobs.lever.co/other/xyz/apply")
        assert locked["error"] == "the listing is locked (burned)"
        agent.drive.locks.lock("L1", LEVER, "pending")
        for number, method in enumerate(("Page.reload", "Input.insertText",
                                         "Input.dispatchMouseEvent"), 4):
            assert call(socket, number, method, text="x")["error"] == (
                "the listing is locked (pending)"
            )


def test_files_go_only_to_the_choosers_node_from_the_drive_files(
    client: TestClient, agent: Agent, chrome: FakeCdp
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        assert "error" in call(socket, 1, "DOM.setFileInputFiles", backendNodeId=5,
                               files=["resume.pdf"])
        client.portal.call(chrome.on_event, "Page.fileChooserOpened",  # type: ignore[union-attr]
                           {"frameId": "T1", "mode": "selectSingle", "backendNodeId": 5}, "S1")
        assert socket.receive_json()["method"] == "Page.fileChooserOpened"
        for number, files in enumerate((["../switch.json"], ["/etc/passwd"], []), 2):
            assert "error" in call(socket, number, "DOM.setFileInputFiles", backendNodeId=5,
                                   files=files)
        assert "error" in call(socket, 6, "DOM.setFileInputFiles", backendNodeId=6,
                               files=["resume.pdf"])
        assert "result" in call(socket, 7, "DOM.setFileInputFiles", backendNodeId=5,
                                files=["resume.pdf"])
    sent = chrome.last("DOM.setFileInputFiles")
    assert sent[1] == {"backendNodeId": 5,
                       "files": [str((agent.drive.files / "resume.pdf").resolve())]}


def test_the_audit_never_holds_typed_values(client: TestClient, agent: Agent) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        call(socket, 1, "Input.insertText", text="hunter2-secret")
        call(socket, 2, "Input.dispatchKeyEvent", type="char", key="q", text="q")
    raw = agent.drive.audit.path.read_text()
    assert "hunter2" not in raw and '"q"' not in raw
    typed = next(line for line in audit(agent) if line["what"] == "Input.insertText")
    assert typed["params"] == {"text_length": 14}
    assert typed["listing_id"] == "L1"
    assert datetime.fromisoformat(typed["ts"]).utcoffset() is not None


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ({"type": "control", "action": "stop"}, "stopped from the Hunt page"),
        ({"type": "pointer", "event": "move", "x": 0.1, "y": 0.1}, "the live view was used"),
        ({"type": "text", "text": "a"}, "the live view was used"),
        ({"type": "wheel", "dx": 0, "dy": 10}, "the live view was used"),
    ],
)
def test_stop_and_live_view_input_end_the_lease(
    client: TestClient, agent: Agent, message: dict[str, Any], reason: str
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        client.portal.call(agent.on_message, message)  # type: ignore[union-attr]
        assert ended(socket) == reason
    assert agent.drive.lease is None


def test_an_idle_lease_expires(
    client: TestClient, agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drive, "IDLE_SECONDS", 0.2)
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        started = time.monotonic()
        assert ended(socket) == "the lease was idle for 10 minutes"
        assert time.monotonic() - started < 5


def test_the_tab_closing_or_leaving_the_host_ends_the_lease(
    client: TestClient, chrome: FakeCdp
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        client.portal.call(chrome.on_event, "Target.targetInfoChanged",  # type: ignore[union-attr]
                           {"targetInfo": {"targetId": "T1", "url": "https://evil.example/"}},
                           None)
        assert ended(socket) == "the leased tab left the lease host"
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        client.portal.call(chrome.on_event, "Target.targetDestroyed",  # type: ignore[union-attr]
                           {"targetId": "T1"}, None)
        assert ended(socket) == "the leased tab closed"


def test_a_confirmation_in_any_tab_or_submitted_on_the_page_locks_the_listing(
    client: TestClient, agent: Agent, chrome: FakeCdp
) -> None:
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        client.portal.call(chrome.on_event, "Target.targetInfoChanged",  # type: ignore[union-attr]
                           {"targetInfo": {"targetId": "OTHER", "url": LEVER + "/thanks"}},
                           None)
        assert "error" in call(socket, 1, "Page.reload")
    assert agent.drive.locks.state_for("L1") == "submitted"
    agent.drive.locks.release("L1")
    with client.websocket_connect("/drive", headers=GOOD) as socket:
        lease(socket)
        client.portal.call(agent.on_message,  # type: ignore[union-attr]
                           {"type": "control", "action": "done"})
        assert call(socket, 1, "Page.navigate", url=LEVER)["error"] == (
            "the listing is locked (submitted)"
        )


def test_posting_urls_normalize_to_one_key(tmp_path: Path) -> None:
    key = "jobs.lever.co/pivotal/abc-123"
    for url in (LEVER, LEVER + "/", LEVER + "/apply", LEVER + "/thanks?x=1",
                "HTTPS://JOBS.LEVER.CO/pivotal/abc-123/apply/#top"):
        assert normalize_posting(url) == key
    locks = LockStore(tmp_path)
    locks.lock("L1", LEVER + "/apply", "unconfirmed", "no change")
    assert locks.state_for(url=LEVER + "/thanks") == "unconfirmed"
    assert locks.state_for("L1") == "unconfirmed"
    locks.release("L1")
    assert locks.state_for("L1", LEVER) is None
    assert not list(tmp_path.glob(".locks.json.*"))
