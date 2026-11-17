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


class Page:
    """One element on a scrollable page: where it is, what a press hits, what it holds."""

    def __init__(self, top: float = 100, values: list[Any] | None = None,
                 hits: list[bool] | None = None) -> None:
        self.top, self.scroll, self.view = top, 0.0, 600
        self.values = values or [""]
        self.hits = hits or []
        self.hold = False
        self.held: list[str] = []

    def evaluate(self, expression: str) -> Any:
        if "elementFromPoint" in expression:
            return self.hits.pop(0) if self.hits else True
        if "activeElement" in expression:
            return True
        if "getBoundingClientRect" in expression:
            return {"x": 10, "y": self.top - self.scroll, "width": 40, "height": 20,
                    "view": self.view}
        if "checked" in expression:
            return self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return "Apply"

    def __call__(self, message: dict[str, Any]) -> list[Any]:
        method, params = message.get("method"), message.get("params") or {}
        if method == "Runtime.evaluate":
            value = self.evaluate(params["expression"])
            return [{"id": message["id"], "result": {"result": {"value": value}}}]
        if method == "Input.dispatchMouseEvent" and params["type"] == "mouseWheel":
            self.scroll += params["deltaY"]
        if self.hold and method in {"Input.dispatchKeyEvent", "Input.insertText"}:
            self.held.append(json.dumps({"id": message["id"], "result": {}}))
            return []
        return agent(message)


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
        socket = FakeSocket(Page())
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
    assert 10 <= clicks[-1]["x"] <= 50 and 100 <= clicks[-1]["y"] <= 120
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


def mouse(socket: FakeSocket, kind: str) -> list[dict[str, Any]]:
    return [m["params"] for m in socket.sent if m.get("method") == "Input.dispatchMouseEvent"
            and m["params"]["type"] == kind]


def leased(page: Page) -> tuple[FakeSocket, DriveClient]:
    socket = FakeSocket(page)
    client = DriveClient(socket, Motion(3))
    client.target = "T1"
    return socket, client


def test_scroll_stops_in_view_and_wheels_toward_the_element_otherwise() -> None:
    async def drive() -> None:
        socket, client = leased(Page(top=300))
        assert await client.scroll_into_view("#a") == Box(10, 300, 40, 20)
        assert mouse(socket, "mouseWheel") == []

        page = Page(top=1500)
        socket, client = leased(page)
        box = await client.scroll_into_view("#a")
        wheels = mouse(socket, "mouseWheel")
        assert wheels and all(80 <= w["deltaY"] <= 120 for w in wheels[:-1])
        assert box.y >= 80 and box.y + box.height <= 520
        assert page.scroll == sum(w["deltaY"] for w in wheels)

        page = Page(top=-400)
        socket, client = leased(page)
        await client.scroll_into_view("#a")
        assert all(w["deltaY"] < 0 for w in mouse(socket, "mouseWheel"))

    asyncio.run(drive())


def test_click_retries_a_miss_and_presses_on_a_hit() -> None:
    async def drive() -> None:
        socket, client = leased(Page(hits=[False, False, True]))
        end = await client.click("#a")
        tests = [m for m in socket.sent if "elementFromPoint" in
                 str(m.get("params", {}).get("expression"))]
        assert len(tests) == 3
        assert len(mouse(socket, "mousePressed")) == 1
        assert client.pointer == end

        socket, client = leased(Page(hits=[False] * 4))
        await client.click("#a")
        assert len(mouse(socket, "mousePressed")) == 1

    asyncio.run(drive())


def test_type_keeps_an_absolute_schedule_and_pipelines_replies(
        monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Hi there, friend.\nné"
    now = [0.0]
    real = asyncio.sleep

    async def sleep(seconds: float) -> None:
        now[0] += seconds
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)

    async def drive(error: bool) -> tuple[list[dict[str, Any]], list[float]]:
        page = Page()
        page.hold = True
        socket, client = leased(page)
        client.clock = lambda: now[0]
        downs: list[float] = []
        send = socket.send

        async def slow_send(raw: str) -> None:
            if '"keyDown"' in raw:
                downs.append(now[0])
            now[0] += 0.005  # a reply round trip the schedule must absorb
            await send(raw)

        socket.send = slow_send  # type: ignore[method-assign]
        task = asyncio.create_task(client.type(text))
        while len(page.held) < 2 * (len(text) - 1) + 1:
            await real(0)
        assert not task.done()  # every key went out before any reply came back
        if error:
            page.held[3] = json.dumps({"id": json.loads(page.held[3])["id"], "error": "x"})
        for raw in page.held:
            socket.inbox.put_nowait(raw)
        await task
        return socket.sent, downs

    sent, downs = asyncio.run(drive(False))
    schedule, at = [], 0.0
    for char, delay in zip(text, Motion(3).key_delays(text), strict=True):
        at += delay / 1000
        if char != "é":
            schedule.append(at)
    assert len(downs) == len(schedule)
    assert all(0 <= down - due < 0.07 for down, due in zip(downs, schedule, strict=True))
    keys = [m["params"] for m in sent if m.get("method") == "Input.dispatchKeyEvent"]
    assert {"type": "keyDown", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "text": "\r"} in keys
    assert [m["params"] for m in sent if m.get("method") == "Input.insertText"] == [
        {"text": "é"}]
    with pytest.raises(DriveError, match="x"):
        asyncio.run(drive(True))


def select_alls(socket: FakeSocket) -> int:
    return sum(1 for m in socket.sent if m.get("method") == "Input.dispatchKeyEvent"
               and m["params"].get("commands") == ["selectAll"])


def test_fill_text_retries_until_the_value_sticks() -> None:
    async def drive() -> None:
        socket, client = leased(Page(values=["old", "wrong", "wrong", "Ada"]))
        assert await client.fill_text("#name", "Ada")
        assert len(mouse(socket, "mousePressed")) == 2
        assert select_alls(socket) == 2

        socket, client = leased(Page(values=["", "x"]))
        assert not await client.fill_text("#name", "Ada")
        assert len(mouse(socket, "mousePressed")) == 3

    asyncio.run(drive())


def test_submit_refuses_a_form_that_does_not_hold_what_was_filled() -> None:
    async def drive() -> FakeSocket:
        socket, client = leased(Page(values=["Ada ", ["a"]]))
        with pytest.raises(DriveError, match="#roles") as refused:
            await client.submit(1, "#go", expected={"#name": "Ada", "#roles": ["a", "b"]})
        assert "#name" not in str(refused.value)
        return socket

    socket = asyncio.run(drive())
    assert not any(m.get("type") == "submit" for m in socket.sent)
    assert mouse(socket, "mouseWheel") == []


def test_submit_reads_back_then_scrolls_the_button_into_view_first() -> None:
    async def drive() -> FakeSocket:
        socket, client = leased(Page(top=1500, values=[" Ada", ["a", "b", "c"]]))
        reply = await client.submit(1, "#go", expected={"#name": "Ada", "#roles": ["a", "b"]})
        assert reply["outcome"] == "submitted"
        return socket

    sent = asyncio.run(drive()).sent
    wheels = [i for i, m in enumerate(sent) if m.get("method") == "Input.dispatchMouseEvent"]
    press = next(i for i, m in enumerate(sent) if m.get("type") == "submit")
    assert wheels and max(wheels) < press
