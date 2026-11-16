"""DriveClient against a scripted fake agent socket."""

import asyncio
import json
from typing import Any

import pytest

from personal_os_worker.device.drive_client import DriveClient, DriveEnded, DriveError
from personal_os_worker.device.motion import Box, Motion


class FakeSocket:
    """Answers each message the client sends with `respond(message)`'s list."""

    def __init__(self, respond: Any) -> None:
        self.respond = respond
        self.sent: list[dict[str, Any]] = []
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        for answer in self.respond(message):
            self.inbox.put_nowait(json.dumps(answer) if answer is not None else None)

    async def close(self) -> None:
        self.inbox.put_nowait(None)

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> str:
        raw = await self.inbox.get()
        if raw is None:
            raise StopAsyncIteration
        return raw


def agent(message: dict[str, Any]) -> list[Any]:
    kind, method = message.get("type"), message.get("method")
    if kind == "lease":
        return [{"type": "leased", "target": "T1"}]
    if kind == "upload_file":
        return [{"type": "uploaded", "path": "C:\\drive\\cv.pdf"}]
    if kind == "os":
        return [{"type": "os_done", "ok": False, "error": "the listing is locked (burned)"}]
    if kind == "attach":
        return [{"type": "attached", "target": "T1"}]
    if kind == "submit":
        return [{"type": "outcome", "outcome": "submitted", "url": "u", "screenshot": None},
                {"type": "ended", "reason": "the press finished: submitted"}, None]
    answers: dict[str, Any] = {
        "DOM.getDocument": {"root": {"nodeId": 1}},
        "DOM.querySelector": {"nodeId": 5},
        "DOM.getBoxModel": {"model": {"border": [10, 10, 50, 10, 50, 30, 10, 30]}},
        "Page.createIsolatedWorld": {"executionContextId": 3},
        "Runtime.evaluate": {"result": {"value": "Apply"}},
    }
    replies: list[Any] = [{"id": message["id"], "result": answers.get(method, {})}]
    if method == "Input.dispatchMouseEvent" and message["params"]["type"] == "mouseReleased":
        replies.append({"type": "event", "method": "Page.fileChooserOpened",
                        "params": {"backendNodeId": 77}})
    return replies


@pytest.fixture(autouse=True)
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real(0))


def test_a_drive_from_lease_to_outcome() -> None:
    async def drive() -> FakeSocket:
        socket = FakeSocket(agent)
        client = DriveClient(socket, Motion(5))
        assert await client.lease("L1", "https://jobs.lever.co/x/1") == "T1"
        assert await client.evaluate("document.title") == "Apply"
        assert await client.find("#submit") == Box(10, 10, 40, 20)
        await client.upload("#attach", "cv.pdf", b"%PDF")
        with pytest.raises(DriveError, match="locked"):
            await client.os([{"op": "wait", "ms": 1}])
        await client.attach()
        assert (await client.submit(2, "#submit", {"fill": "slow"}))["outcome"] == "submitted"
        await asyncio.sleep(0)
        with pytest.raises(DriveEnded, match="the press finished"):
            await client.cdp("DOM.getDocument")
        return socket

    socket = asyncio.run(drive())
    sent = socket.sent
    world = next(m for m in sent if m.get("method") == "Page.createIsolatedWorld")
    assert world["params"]["frameId"] == "T1"
    files = next(m for m in sent if m.get("method") == "DOM.setFileInputFiles")
    assert files["params"] == {"backendNodeId": 77, "files": ["C:\\drive\\cv.pdf"]}
    clicks = [m["params"] for m in sent if m.get("method") == "Input.dispatchMouseEvent"]
    assert [c["type"] for c in clicks][-2:] == ["mousePressed", "mouseReleased"]
    assert 10 <= clicks[-1]["x"] <= 50 and 10 <= clicks[-1]["y"] <= 30
    assert sent[-1] == {"type": "submit", "experiment": 2, "selector": "#submit",
                        "knobs": {"fill": "slow"}}


def test_stop_refuses_every_later_action() -> None:
    async def drive() -> None:
        client = DriveClient(FakeSocket(agent))
        await client.lease("L1", "https://jobs.lever.co/x/1")
        client.stop()
        with pytest.raises(DriveEnded, match="the driver stopped"):
            await client.cdp("DOM.getDocument")

    asyncio.run(drive())
