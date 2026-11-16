import asyncio
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any

import httpx
import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from personal_os_worker.core.config import CliConfig
from personal_os_worker.device import server
from personal_os_worker.device.identity import DeviceIdentity
from personal_os_worker.device.memory import Process
from personal_os_worker.device.runs import RunManager
from personal_os_worker.device.server import Agent, TokenVerifier, create_app
from personal_os_worker.device.update import Checkout

PAGE = "https://hunt.example.com"
GOOD = {"Authorization": "Bearer good"}
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


class FakeStream:
    def __init__(self, viewer: server.Viewer) -> None:
        self.viewer = viewer
        self.calls: list[tuple[str, Any]] = []

    async def open(self, view: dict[str, Any], *, run_tab: str | None = None) -> None:
        self.calls.append(("open", view))
        await self.viewer.send_json({"type": "tabs", "tabs": [{"id": "T1"}], "current": "T1"})
        await self.viewer.send_frame({"type": "frame", "target": "T1"}, b"jpeg")

    async def close(self) -> None:
        self.calls.append(("close", None))

    async def pointer(self, message: dict[str, Any]) -> None:
        self.calls.append(("pointer", message))

    async def key(self, message: dict[str, Any]) -> None:
        self.calls.append(("key", message))

    async def switch(self, target: str) -> None:
        self.calls.append(("switch", target))


@pytest.fixture
def agent() -> Agent:
    streams: list[FakeStream] = []

    def factory(viewer: server.Viewer) -> FakeStream:
        streams.append(FakeStream(viewer))
        return streams[-1]

    made = Agent(
        CONFIG, DEVICE,
        presence=FakePresence(),  # type: ignore[arg-type]
        verifier=FakeVerifier(),  # type: ignore[arg-type]
        update=lambda: "up to date",
        ensure_chrome=lambda: None,
        stream_factory=factory,  # type: ignore[arg-type]
    )
    made.streams = streams  # type: ignore[attr-defined]
    made.runs = RunManager(
        publish=made.publish,
        update=lambda: (time.sleep(0.3), "up to date")[1],
        command=lambda reference, fresh: [sys.executable, "-c", "pass"],
    )
    return made


@pytest.fixture
def client(agent: Agent) -> TestClient:
    with TestClient(create_app(agent, background=False)) as made:
        yield made  # type: ignore[misc]


def test_health_needs_nothing(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_status_needs_the_users_token(client: TestClient) -> None:
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"Authorization": "Bearer bad"}).status_code == 401
    body = client.get("/status", headers=GOOD).json()
    assert body["device"]["name"] == "DESKTOP-HOME"
    assert body["device"]["commit"] == "c" * 40
    assert body["run"] is None


def test_another_site_is_refused_and_the_page_is_allowed(client: TestClient) -> None:
    refused = client.get("/status", headers=GOOD | {"Origin": "https://evil.example"})
    assert refused.status_code == 403
    allowed = client.get("/status", headers=GOOD | {"Origin": PAGE})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == PAGE


def test_a_preflight_answers_chromes_private_network_check(client: TestClient) -> None:
    response = client.options(
        "/runs",
        headers={
            "Origin": PAGE,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-private-network"] == "true"
    assert "Authorization" in response.headers["access-control-allow-headers"]


def test_runs_take_only_references_and_one_at_a_time(client: TestClient, agent: Agent) -> None:
    assert client.post("/runs", json={"reference": "1"}).status_code == 401
    bad = client.post("/runs", json={"reference": "--dry-run"}, headers=GOOD)
    assert bad.status_code == 422
    started = client.post("/runs", json={"reference": "12"}, headers=GOOD)
    assert started.status_code == 202
    assert started.json()["run"]["reference"] == "12"
    assert client.post("/runs", json={}, headers=GOOD).status_code == 409
    deadline = time.monotonic() + 10
    while agent.runs.busy():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.post("/runs", json={}, headers=GOOD).status_code == 202


def test_a_websocket_from_another_site_never_opens(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/session", headers={"origin": "https://evil.example"}
    ):
        pass


def test_the_session_needs_the_token_first(client: TestClient) -> None:
    with client.websocket_connect("/session", headers={"origin": PAGE}) as socket:
        socket.send_json({"type": "auth", "token": "bad"})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == server.UNAUTHORIZED


def test_the_cdp_relay_needs_a_bearer_token_and_no_origin(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer bad"}, {**GOOD, "origin": PAGE}):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/cdp", headers=headers
        ):
            pass


def test_the_cdp_relay_pipes_both_ways_to_chrome(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.sync.server import serve

    def echo(connection: Any) -> None:
        for message in connection:
            connection.send(f"chrome:{message}")

    with serve(echo, "127.0.0.1", 0) as chrome:
        port = chrome.socket.getsockname()[1]
        threading.Thread(target=chrome.serve_forever, daemon=True).start()

        async def endpoint(_: str) -> str:
            return f"ws://127.0.0.1:{port}"

        monkeypatch.setattr(server, "browser_endpoint", endpoint)
        with client.websocket_connect("/cdp", headers=GOOD) as socket:
            socket.send_text('{"id":1}')
            assert socket.receive_text() == 'chrome:{"id":1}'
        chrome.shutdown()


def test_a_session_gets_the_device_the_tab_and_frames_and_sends_input(
    client: TestClient, agent: Agent
) -> None:
    with client.websocket_connect("/session", headers={"origin": PAGE}) as socket:
        socket.send_json({"type": "auth", "token": "good", "view": {"width": 800, "height": 600}})
        hello = socket.receive_json()
        assert hello["type"] == "hello" and hello["device"]["id"] == DEVICE.id
        assert socket.receive_json()["type"] == "tabs"
        frame = socket.receive_bytes()
        size = int.from_bytes(frame[:4], "big")
        assert frame[4 + size :] == b"jpeg"
        socket.send_json({"type": "pointer", "event": "down", "x": 0.5, "y": 0.5})
        socket.send_json({"type": "tab", "id": "T2"})
        socket.send_json({"type": "nonsense"})
        socket.send_json({"type": "key", "event": "down", "key": "a", "code": "KeyA"})
        deadline = time.monotonic() + 5
        stream = agent.streams[0]  # type: ignore[attr-defined]
        while len(stream.calls) < 4:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    assert [name for name, _ in stream.calls[:4]] == ["open", "pointer", "switch", "key"]
    assert stream.calls[0][1] == {"width": 800, "height": 600}


def test_a_new_viewer_replaces_the_old_one(client: TestClient) -> None:
    with client.websocket_connect("/session", headers={"origin": PAGE}) as first:
        first.send_json({"type": "auth", "token": "good"})
        assert first.receive_json()["type"] == "hello"
        with client.websocket_connect("/session", headers={"origin": PAGE}) as second:
            second.send_json({"type": "auth", "token": "good"})
            assert second.receive_json()["type"] == "hello"
            first.receive_json()  # its tabs message
            first.receive_bytes()  # its frame
            with pytest.raises(WebSocketDisconnect) as closed:
                first.receive_json()
            assert closed.value.code == server.REPLACED


def verifier_with(handler: Any, ensure: Any = None) -> TokenVerifier:
    return TokenVerifier(
        "http://127.0.0.1:8000", ensure_api=ensure, transport=httpx.MockTransport(handler)
    )


async def test_a_token_is_good_when_the_local_api_accepts_it() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        ok = request.headers["authorization"] == "Bearer good"
        return httpx.Response(200 if ok else 403, json={})

    verifier = verifier_with(handler)
    assert await verifier.verify("good")
    assert await verifier.verify("good")  # cached
    assert not await verifier.verify("wrong-account")
    assert not await verifier.verify("")
    assert calls == ["Bearer good", "Bearer wrong-account"]


async def test_a_local_api_that_is_down_is_started_once() -> None:
    attempts: list[int] = []
    started: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={})

    verifier = verifier_with(handler, ensure=lambda: started.append(1))
    assert await verifier.verify("good")
    assert started == [1]


async def test_the_agent_restarts_only_between_runs(agent: Agent) -> None:
    prints = iter(["a", "b", "b", "b"])
    restarted: list[int] = []

    async def restart() -> None:
        restarted.append(1)

    agent.runs.current = type("S", (), {"active": True})()  # type: ignore[assignment]
    task = asyncio.create_task(
        server.restart_when_changed(agent, lambda: next(prints), restart=restart, every=0.01)
    )
    await asyncio.sleep(0.05)
    assert restarted == []
    agent.runs.current = None
    await asyncio.wait_for(task, 1)
    assert restarted == [1]


class FakeWatch:
    def __init__(self, over: Process | None) -> None:
        self.over = over
        self.agent_pid = 100
        self.limit = 4 * 1024**3
        self.asked: list[int | None] = []

    def check(self, run_pid: int | None = None) -> Process | None:
        self.asked.append(run_pid)
        return self.over


async def test_a_run_process_past_the_limit_is_named_in_the_runs_log(agent: Agent) -> None:
    notes: list[str] = []

    async def note(text: str) -> None:
        notes.append(text)

    async def restart() -> None:
        raise AssertionError("only the agent itself past the limit restarts")

    agent.runs.note = note  # type: ignore[method-assign]
    agent.runs.current = type("S", (), {"active": True, "pid": 201})()  # type: ignore[assignment]
    fake = FakeWatch(Process(201, 1, "os", 5 * 1024**3))
    task = asyncio.create_task(server.watch_memory(agent, fake, restart=restart, every=0.01))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    task.cancel()
    assert fake.asked[0] == 201
    assert notes[0].startswith("Stopped os (201): it held 5.00G, past the 4.00G limit")


async def test_the_agent_past_the_limit_restarts_once_no_run_is_active(agent: Agent) -> None:
    restarted: list[int] = []

    async def restart() -> None:
        restarted.append(1)

    agent.runs.current = type("S", (), {"active": True, "pid": None})()  # type: ignore[assignment]
    fake = FakeWatch(Process(100, 1, "python3", 5 * 1024**3))
    task = asyncio.create_task(server.watch_memory(agent, fake, restart=restart, every=0.01))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert restarted == []
    agent.runs.current = None
    await asyncio.wait_for(task, 1)
    assert restarted == [1]


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed: int | None = None

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.closed is not None:
            raise RuntimeError('Cannot call "send" once a close message has been sent.')
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = code


async def test_a_stream_that_fails_after_its_viewer_was_replaced_tells_no_one(
    agent: Agent,
) -> None:
    # Chrome too busy to answer: by the time it fails, a newer viewer has the session.
    first, second = FakeSocket(), FakeSocket()

    def busy_chrome() -> None:
        agent.viewer = server.Viewer(second)  # type: ignore[arg-type]
        asyncio.run(first.close(server.REPLACED))
        raise RuntimeError("Chrome holds port 9333 but is not answering")

    agent.ensure_chrome = busy_chrome
    await agent.attach(server.Viewer(first), {})  # type: ignore[arg-type]
    assert [message["type"] for message in first.sent] == ["hello"]


def test_nothing_here_imports_jobs() -> None:
    source = Path(server.__file__).read_text()
    assert "personal_os_worker.jobs" not in source
