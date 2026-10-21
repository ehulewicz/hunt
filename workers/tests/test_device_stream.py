import asyncio
import base64
import json
from typing import Any

import pytest

from personal_os_worker.device import stream
from personal_os_worker.device.stream import (
    key_events,
    layout_size,
    mouse_params,
    page_point,
    screencast_size,
    wheel_params,
)

META = {"deviceWidth": 1280, "deviceHeight": 720, "offsetTop": 0, "pageScaleFactor": 1}


def test_a_point_on_the_image_is_the_same_point_on_the_page() -> None:
    assert page_point(0.5, 0.25, META) == (640.0, 180.0)
    assert page_point(-0.2, 1.4, META) == (0.0, 720.0)  # clamped to the viewport


def test_the_screencast_is_sized_to_the_viewer() -> None:
    assert screencast_size({"width": 800, "height": 600, "dpr": 2}) == (1600, 1200)
    assert screencast_size({"width": 4000, "height": 3000, "dpr": 2}) == (2560, 2560)
    assert screencast_size({}) == (1280, 800)


def test_a_click_presses_and_releases_the_left_button() -> None:
    down = mouse_params(
        {"event": "down", "x": 0.5, "y": 0.5, "button": 0, "buttons": 1, "clickCount": 1}, META
    )
    up = mouse_params(
        {"event": "up", "x": 0.5, "y": 0.5, "button": 0, "buttons": 0, "clickCount": 1}, META
    )
    assert down == {
        "type": "mousePressed", "x": 640.0, "y": 360.0, "button": "left", "buttons": 1,
        "clickCount": 1, "modifiers": 0, "pointerType": "mouse",
    }
    assert up["type"] == "mouseReleased" and up["button"] == "left"


def test_a_right_click_with_shift_and_a_double_click() -> None:
    right = mouse_params(
        {"event": "down", "x": 0, "y": 0, "button": 2, "buttons": 2, "shift": True}, META
    )
    assert right["button"] == "right" and right["modifiers"] == 8
    double = mouse_params({"event": "down", "x": 0, "y": 0, "button": 0, "clickCount": 2}, META)
    assert double["clickCount"] == 2


def test_a_move_carries_no_button_and_no_click() -> None:
    move = mouse_params({"event": "move", "x": 0.1, "y": 0.1, "buttons": 0}, META)
    assert move["type"] == "mouseMoved" and move["button"] == "none"
    assert "clickCount" not in move


def test_wheel_lines_and_pages_become_pixels() -> None:
    pixels = wheel_params({"x": 0.5, "y": 0.5, "dx": 0, "dy": 120, "mode": 0}, META)
    lines = wheel_params({"x": 0.5, "y": 0.5, "dx": 0, "dy": 3, "mode": 1}, META)
    pages = wheel_params({"x": 0.5, "y": 0.5, "dx": 0, "dy": 1, "mode": 2}, META)
    assert pixels["type"] == "mouseWheel" and pixels["deltaY"] == 120
    assert lines["deltaY"] == 120
    assert pages["deltaY"] == 720


def test_a_printable_key_types_its_text() -> None:
    down, up = (
        key_events({"event": "down", "key": "a", "code": "KeyA"})
        + key_events({"event": "up", "key": "a", "code": "KeyA"})
    )
    assert down == {
        "type": "keyDown", "key": "a", "code": "KeyA", "text": "a", "unmodifiedText": "a",
        "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65, "modifiers": 0,
        "autoRepeat": False,
    }
    assert up["type"] == "keyUp" and "text" not in up


def test_shifted_and_punctuation_keys_keep_their_character() -> None:
    capital = key_events({"event": "down", "key": "A", "code": "KeyA", "shift": True})[0]
    assert capital["text"] == "A" and capital["modifiers"] == 8
    question = key_events({"event": "down", "key": "?", "code": "Slash", "shift": True})[0]
    assert question["text"] == "?" and question["windowsVirtualKeyCode"] == 191


def test_enter_tab_backspace_and_arrows() -> None:
    enter = key_events({"event": "down", "key": "Enter", "code": "Enter"})[0]
    assert enter["type"] == "keyDown" and enter["text"] == "\r"
    assert enter["windowsVirtualKeyCode"] == 13
    for key, code, vk in [("Tab", "Tab", 9), ("Backspace", "Backspace", 8),
                          ("ArrowLeft", "ArrowLeft", 37), ("Delete", "Delete", 46),
                          ("Escape", "Escape", 27), ("F5", "F5", 116)]:
        event = key_events({"event": "down", "key": key, "code": code})[0]
        assert event["type"] == "rawKeyDown" and "text" not in event
        assert event["windowsVirtualKeyCode"] == vk


def test_a_shortcut_sends_no_text() -> None:
    select_all = key_events({"event": "down", "key": "a", "code": "KeyA", "ctrl": True})[0]
    assert select_all["type"] == "rawKeyDown"
    assert select_all["modifiers"] == 2 and "text" not in select_all
    assert select_all["windowsVirtualKeyCode"] == 65


def test_modifier_keys_and_the_numpad() -> None:
    shift = key_events({"event": "down", "key": "Shift", "code": "ShiftLeft", "location": 1})[0]
    assert shift["windowsVirtualKeyCode"] == 16 and shift["location"] == 1
    digit = key_events({"event": "down", "key": "7", "code": "Numpad7", "location": 3})[0]
    assert digit["text"] == "7" and digit["windowsVirtualKeyCode"] == 103
    assert digit["isKeypad"] is True


def test_an_unusable_key_message_sends_nothing() -> None:
    assert key_events({"event": "down"}) == []
    assert key_events({"event": "sideways", "key": "a", "code": "KeyA"}) == []


class FakeCdp:
    """A browser endpoint: answers commands, and can push events at the stream."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], str | None]] = []
        self.handler: Any = None
        self.targets = [
            {"targetId": "T1", "type": "page", "title": "Board", "url": "https://jobs.example/1"},
            {"targetId": "T2", "type": "page", "title": "Apply", "url": "https://jobs.example/2"},
            {"targetId": "W", "type": "service_worker", "title": "", "url": "https://x/sw.js"},
        ]

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        self.sent.append((method, params or {}, session_id))
        if method == "Target.getTargets":
            return {"targetInfos": self.targets}
        if method == "Target.attachToTarget":
            return {"sessionId": f"S-{params['targetId']}"}  # type: ignore[index]
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {"windowState": "normal"}}
        return {}

    async def close(self) -> None:
        pass

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.sent]


async def started(
    run_tab: str | None = None,
) -> tuple[stream.Stream, FakeCdp, list[tuple[dict[str, Any], bytes]], list[dict[str, Any]]]:
    cdp = FakeCdp()
    frames: list[tuple[dict[str, Any], bytes]] = []
    messages: list[dict[str, Any]] = []

    async def send_frame(header: dict[str, Any], data: bytes) -> None:
        frames.append((header, data))

    async def send_json(message: dict[str, Any]) -> None:
        messages.append(message)

    async def connect(on_event: Any) -> FakeCdp:
        cdp.handler = on_event
        return cdp

    view = stream.Stream(connect=connect, send_frame=send_frame, send_json=send_json)
    await view.open({"width": 640, "height": 400, "dpr": 1}, run_tab=run_tab)
    return view, cdp, frames, messages


async def test_the_stream_shows_the_runs_tab_and_lists_only_pages() -> None:
    view, cdp, _, messages = await started(run_tab="T2")
    assert view.current == "T2"
    attach = next(params for method, params, _ in cdp.sent if method == "Target.attachToTarget")
    assert attach == {"targetId": "T2", "flatten": True}
    start = next(
        (params, session) for method, params, session in cdp.sent
        if method == "Page.startScreencast"
    )
    assert start[1] == "S-T2"
    assert start[0]["format"] == "jpeg" and start[0]["maxWidth"] == 640
    tabs = [message for message in messages if message["type"] == "tabs"][-1]
    assert [tab["id"] for tab in tabs["tabs"]] == ["T1", "T2"] and tabs["current"] == "T2"


def test_the_tab_is_laid_out_at_the_viewers_pane_but_never_narrower_than_a_desktop() -> None:
    assert layout_size({"width": 1180, "height": 700, "dpr": 1.25}) == {
        "width": 1180, "height": 700, "deviceScaleFactor": 1.25, "mobile": False,
    }
    # A narrow pane keeps the desktop layout, at the pane's shape, shown smaller.
    assert layout_size({"width": 700, "height": 600, "dpr": 1}) == {
        "width": 1024, "height": 878, "deviceScaleFactor": 1.0, "mobile": False,
    }
    assert layout_size({"width": 1180, "height": 700, "dpr": 5})["deviceScaleFactor"] == 3.0  # type: ignore[index]
    assert layout_size({}) is None
    assert layout_size({"width": 0, "height": 700}) is None


async def test_the_shown_tab_fills_the_pane_at_the_viewers_pixel_density() -> None:
    view, cdp, _, _ = await started(run_tab="T1")
    await view.resize({"width": 1180, "height": 700, "dpr": 1.25})
    # Chrome gives a tab in a minimized window whatever size it likes (2560x1271,
    # 1065x1751); the pane then scrolls, or shrinks the page. Lay it out at the pane.
    override = [
        (params, session) for method, params, session in cdp.sent
        if method == "Emulation.setDeviceMetricsOverride"
    ][-1]
    assert override == (
        {"width": 1180, "height": 700, "deviceScaleFactor": 1.25, "mobile": False}, "S-T1"
    )
    params = [params for method, params, _ in cdp.sent if method == "Page.startScreencast"][-1]
    assert params["maxWidth"] == 1475
    assert "maxHeight" not in params
    assert params["quality"] >= 90
    order = cdp.methods()
    assert order.index("Emulation.setDeviceMetricsOverride") < order.index("Page.startScreencast")
    await view.close()


async def test_a_tab_gets_its_own_size_back_once_nobody_watches_it() -> None:
    view, cdp, _, _ = await started(run_tab="T1")
    await view.switch("T2")
    assert ("Emulation.clearDeviceMetricsOverride", {}, "S-T1") in cdp.sent
    await view.close()
    assert ("Emulation.clearDeviceMetricsOverride", {}, "S-T2") in cdp.sent


async def test_each_frame_reaches_the_viewer_before_it_is_acknowledged() -> None:
    view, cdp, frames, _ = await started(run_tab="T1")
    jpeg = b"\xff\xd8 fake jpeg"
    cdp.handler(
        "Page.screencastFrame",
        {"data": base64.b64encode(jpeg).decode(), "metadata": META, "sessionId": 7},
        "S-T1",
    )
    await asyncio.sleep(0.01)
    assert frames == [({"type": "frame", "target": "T1", **{
        "width": 1280, "height": 720}}, jpeg)]
    assert ("Page.screencastFrameAck", {"sessionId": 7}, "S-T1") in cdp.sent
    await view.pointer({"event": "down", "x": 0.5, "y": 0.5, "button": 0, "buttons": 1})
    dispatched = [params for method, params, _ in cdp.sent if method == "Input.dispatchMouseEvent"]
    assert dispatched[-1]["x"] == 640.0


async def test_a_frame_from_a_tab_no_longer_shown_is_dropped() -> None:
    _, cdp, frames, _ = await started(run_tab="T1")
    cdp.handler(
        "Page.screencastFrame",
        {"data": base64.b64encode(b"old").decode(), "metadata": META, "sessionId": 1},
        "S-T9",
    )
    await asyncio.sleep(0.01)
    assert frames == []


async def test_switching_tabs_brings_the_tab_forward_in_chrome() -> None:
    view, cdp, _, _ = await started(run_tab="T1")
    await view.switch("T2")
    assert view.current == "T2"
    assert ("Target.activateTarget", {"targetId": "T2"}, None) in cdp.sent
    assert ("Page.stopScreencast", {}, "S-T1") in cdp.sent


async def test_a_popup_from_the_shown_tab_is_followed_and_left_when_it_closes() -> None:
    view, cdp, _, _ = await started(run_tab="T1")
    cdp.handler("Target.targetCreated", {"targetInfo": {
        "targetId": "G", "type": "page", "title": "Sign in", "url": "https://accounts.google.com/",
        "openerId": "T1"}}, None)
    await asyncio.sleep(0.01)
    assert view.current == "G"
    cdp.handler("Target.targetDestroyed", {"targetId": "G"}, None)
    await asyncio.sleep(0.01)
    assert view.current == "T1"


async def test_keys_and_pasted_text_reach_the_shown_tab() -> None:
    view, cdp, _, _ = await started(run_tab="T1")
    await view.key({"event": "down", "key": "a", "code": "KeyA"})
    await view.text("pasted words")
    assert ("Input.insertText", {"text": "pasted words"}, "S-T1") in cdp.sent
    key = next(params for method, params, _ in cdp.sent if method == "Input.dispatchKeyEvent")
    assert key["text"] == "a"


async def test_wheel_turns_made_while_chrome_is_busy_are_summed_into_one() -> None:
    view, cdp, _, _ = await started(run_tab="T1")
    view.meta = {"deviceWidth": 640, "deviceHeight": 400}
    painted = asyncio.Event()
    answer = cdp.send

    async def slow(method: str, params: Any = None, session_id: Any = None) -> dict[str, Any]:
        if method == "Input.dispatchMouseEvent":
            await painted.wait()
        return await answer(method, params, session_id)

    cdp.send = slow  # type: ignore[method-assign]
    await view.wheel({"x": 0.5, "y": 0.5, "dx": 0, "dy": 40, "mode": 0})
    await asyncio.sleep(0)  # the first turn is in Chrome, waiting for its paint
    for dy in (30, 20, 10):
        await view.wheel({"x": 0.5, "y": 0.5, "dx": 0, "dy": dy, "mode": 0})
    painted.set()
    await asyncio.sleep(0.01)
    turns = [params["deltaY"] for method, params, _ in cdp.sent
             if method == "Input.dispatchMouseEvent"]
    assert turns == [40, 60]


def test_frame_headers_are_length_prefixed_json() -> None:
    packed = stream.pack_frame({"type": "frame", "target": "T1"}, b"jpeg")
    size = int.from_bytes(packed[:4], "big")
    assert json.loads(packed[4 : 4 + size]) == {"type": "frame", "target": "T1"}
    assert packed[4 + size :] == b"jpeg"


@pytest.fixture(autouse=True)
def _quiet_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # The first-frame screenshot fallback would wait a second and a half per test.
    monkeypatch.setattr(stream, "FIRST_FRAME_SECONDS", 0.0)


async def test_a_switch_asked_for_while_opening_waits_for_the_open() -> None:
    cdp = FakeCdp()
    gate = asyncio.Event()
    messages: list[dict[str, Any]] = []

    async def connect(on_event: Any) -> FakeCdp:
        cdp.handler = on_event
        await gate.wait()  # Chrome is slow to answer
        return cdp

    async def send_json(message: dict[str, Any]) -> None:
        messages.append(message)

    async def send_frame(header: dict[str, Any], data: bytes) -> None:
        pass

    view = stream.Stream(connect=connect, send_frame=send_frame, send_json=send_json)
    opening = asyncio.create_task(view.open({"width": 640, "height": 400}))
    switching = asyncio.create_task(view.switch("T2"))
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.wait_for(asyncio.gather(opening, switching), 2)
    assert view.current == "T2"


async def test_no_tab_list_is_sent_until_the_first_tab_is_shown() -> None:
    cdp = FakeCdp()
    messages: list[dict[str, Any]] = []

    async def connect(on_event: Any) -> FakeCdp:
        cdp.handler = on_event
        # Discovery reports existing tabs one by one before getTargets answers.
        on_event("Target.targetCreated", {"targetInfo": cdp.targets[0]}, None)
        await asyncio.sleep(0.01)
        return cdp

    async def send_json(message: dict[str, Any]) -> None:
        messages.append(message)

    async def send_frame(header: dict[str, Any], data: bytes) -> None:
        pass

    view = stream.Stream(connect=connect, send_frame=send_frame, send_json=send_json)
    await view.open({"width": 640, "height": 400})
    await asyncio.sleep(0.01)
    tabs = [message for message in messages if message["type"] == "tabs"]
    assert tabs and all(message["current"] == "T1" for message in tabs)
    assert all(len(message["tabs"]) == 2 for message in tabs)


async def test_a_stream_closed_while_opening_lets_go_of_chrome() -> None:
    cdp = FakeCdp()
    closed: list[int] = []
    gate = asyncio.Event()

    async def connect(on_event: Any) -> FakeCdp:
        await gate.wait()
        return cdp

    async def close() -> None:
        closed.append(1)

    cdp.close = close  # type: ignore[method-assign]

    async def nothing(*_: Any) -> None:
        pass

    view = stream.Stream(connect=connect, send_frame=nothing, send_json=nothing)
    opening = asyncio.create_task(view.open({"width": 640, "height": 400}))
    await asyncio.sleep(0.01)
    switching = asyncio.create_task(view.switch("T2"))
    await view.close()
    gate.set()
    await asyncio.wait_for(asyncio.gather(opening, switching), 2)
    assert closed == [1]
    assert view.current is None
    assert "Target.attachToTarget" not in cdp.methods()


async def test_closing_the_stream_on_purpose_reports_nothing_lost() -> None:
    view, cdp, _, messages = await started(run_tab="T1")
    await view.close()
    cdp.handler("Stream.closed", {}, None)
    await asyncio.sleep(0.01)
    assert not [message for message in messages if message["type"] == "stream"]


async def test_chrome_going_away_is_reported_even_to_a_viewer_that_left() -> None:
    cdp = FakeCdp()

    async def connect(on_event: Any) -> FakeCdp:
        cdp.handler = on_event
        return cdp

    async def gone(*_: Any) -> None:
        raise RuntimeError("the viewer disconnected")

    view = stream.Stream(connect=connect, send_frame=gone, send_json=gone)
    await view.open({"width": 640, "height": 400})
    cdp.handler("Stream.closed", {}, None)
    await asyncio.sleep(0.01)  # no unhandled task error


class ClosingSocket:
    """A CDP websocket that Chrome closes while a command is still being sent."""

    def __init__(self) -> None:
        self.ended = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> "ClosingSocket":
        return self

    async def __anext__(self) -> str:
        await self.ended.wait()
        raise StopAsyncIteration

    async def send(self, _data: str) -> None:
        await self.release.wait()
        raise ConnectionError("sent 1000 (OK); then received 1000 (OK)")

    async def close(self) -> None:
        self.ended.set()


async def test_a_command_cut_off_by_chrome_closing_leaves_nothing_unretrieved() -> None:
    import gc

    from personal_os_worker.device.stream import CdpConnection, CdpError

    reported: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    socket = ClosingSocket()
    connection = CdpConnection(socket, lambda *_: None)
    command = asyncio.create_task(connection.send("Page.screencastFrameAck"))
    await asyncio.sleep(0)
    socket.ended.set()  # Chrome goes away: the reader fails every pending command
    await asyncio.sleep(0)
    socket.release.set()  # and the send itself then fails on the closed socket
    with pytest.raises(CdpError):
        await command
    del command
    gc.collect()
    await asyncio.sleep(0)
    loop.set_exception_handler(None)
    assert [context.get("message") for context in reported] == []
