"""A driver's side of `WS /drive`: lease a tab, send CDP and OS input, press, read the outcome.

Command replies are matched by id; the agent's other answers (`leased`,
`uploaded`, `os_done`, `attached`, `outcome`, errors) arrive in the order asked.
`stop()` sets a flag checked before every action; an `ended` message raises
`DriveEnded` in whatever waits and in every later call.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import time
from collections.abc import Callable
from typing import Any

from personal_os_worker.core.page_scripts import load
from personal_os_worker.device.motion import Box, Motion, Point

CHOOSER_SECONDS = 10.0
OUTCOME_SECONDS = 120.0
SCROLL_STEPS = 60
SETTLE_READS = 10
SETTLE_SECONDS = 0.25
CLICK_TRIES = 4
MISS_SECONDS = 0.5
FILL_TRIES = 3
KEY_UP_MS = (25, 60)
WHEEL_GAP_MS = (30, 90)
SUBMIT_PAUSE = (0.6, 1.4)
ORIGIN = Point(0.0, 0.0)
CTRL = 2
RECT = ("(e => e && (r => ({x: r.left, y: r.top, width: r.width, height: r.height, "
        "view: innerHeight}))(e.getBoundingClientRect()))(document.querySelector(%s))")
FOCUSED = "document.activeElement === document.querySelector(%s)"
ENTER = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}


class DriveEnded(RuntimeError):
    """The lease ended, or the driver stopped; the message says why."""


class DriveError(RuntimeError):
    """The agent refused or failed one request; the lease goes on."""


class DriveClient:
    def __init__(self, websocket: Any, motion: Motion | None = None) -> None:
        self.websocket = websocket
        self.motion = motion or Motion()
        self.stopped = False
        self.ended: str | None = None
        self.target = ""
        self.pointer = ORIGIN
        self.clock: Callable[[], float] = time.monotonic
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._replies: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, url: str, token: str, motion: Motion | None = None) -> DriveClient:
        from websockets.asyncio.client import connect

        websocket = await connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                                  max_size=None)
        return cls(websocket, motion)

    def stop(self) -> None:
        self.stopped = True

    def _check(self) -> None:
        if self.ended is not None:
            raise DriveEnded(self.ended)
        if self.stopped:
            raise DriveEnded("the driver stopped")

    async def _read(self) -> None:
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                kind, number = message.get("type"), message.get("id")
                future = self._pending.pop(number, None) if isinstance(number, int) else None
                if future is not None:
                    with contextlib.suppress(asyncio.InvalidStateError):  # given up on
                        future.set_result(message)
                elif kind == "event":
                    self.events.put_nowait(message)
                elif kind in {"ended", "refused"}:
                    self.ended = str(message.get("reason") or kind)
                else:
                    self._replies.put_nowait(message)
        except Exception:  # the socket is gone either way
            pass
        self.ended = self.ended or "the connection closed"
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DriveEnded(self.ended))
        self._replies.put_nowait({"type": "ended", "reason": self.ended})

    async def _send(self, message: dict[str, Any]) -> None:
        self._check()
        await self.websocket.send(json.dumps(message))

    async def _reply(self, timeout: float | None = None) -> dict[str, Any]:
        reply = await asyncio.wait_for(self._replies.get(), timeout)
        if reply.get("type") == "ended":
            raise DriveEnded(str(reply.get("reason")))
        if reply.get("type") == "error" or reply.get("ok") is False:
            raise DriveError(str(reply.get("error")))
        return reply

    async def _ask(self, message: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        await self._send(message)
        return await self._reply(timeout)

    async def lease(self, listing_id: str, url: str) -> str:
        """Lease the posting's tab; returns its target id."""
        reply = await self._ask({"type": "lease", "listing_id": listing_id, "url": url})
        self.target = str(reply.get("target") or "")
        return self.target

    async def _command(self, method: str,
                       params: dict[str, Any] | None = None) -> asyncio.Future[dict[str, Any]]:
        """Send one CDP command; the future holds its reply."""
        number = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[number] = future
        await self._send({"id": number, "method": method, "params": params or {}})
        return future

    async def cdp(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One allowed CDP command on the leased tab, awaited."""
        reply = await (await self._command(method, params))
        if "error" in reply:
            raise DriveError(str(reply["error"]))
        return dict(reply.get("result") or {})

    async def evaluate(self, expression: str) -> Any:
        """`expression`'s value in a fresh isolated world on the main frame."""
        # A tab's main frame has the tab's target id.
        world = await self.cdp("Page.createIsolatedWorld",
                               {"frameId": self.target, "worldName": "driver"})
        read = await self.cdp("Runtime.evaluate", {
            "expression": expression, "contextId": world["executionContextId"],
            "returnByValue": True,
        })
        return (read.get("result") or {}).get("value")

    async def find(self, selector: str) -> Box:
        """The box of the first node `selector` matches, in viewport CSS pixels."""
        root = (await self.cdp("DOM.getDocument", {"depth": 0}))["root"]["nodeId"]
        node = (await self.cdp("DOM.querySelector", {"nodeId": root, "selector": selector}))
        if not node.get("nodeId"):
            raise DriveError(f"nothing matches {selector!r}")
        model = await self.cdp("DOM.getBoxModel", {"nodeId": node["nodeId"]})
        return Box.from_quad([float(value) for value in model["model"]["border"]])

    async def _rect(self, selector: str) -> dict[str, float]:
        rect = await self.evaluate(RECT % json.dumps(selector))
        if not isinstance(rect, dict):
            raise DriveError(f"nothing matches {selector!r}")
        return {key: float(value) for key, value in rect.items()}

    async def _settle(self, selector: str) -> dict[str, float]:
        """The rect once a smooth scroll has stopped moving it."""
        rect = await self._rect(selector)
        for _ in range(SETTLE_READS - 1):
            await asyncio.sleep(SETTLE_SECONDS)
            again = await self._rect(selector)
            moved, rect = abs(again["y"] - rect["y"]), again
            if moved < 1:
                break
        return rect

    async def scroll_into_view(self, selector: str, margin: float = 80) -> Box:
        """Wheel the element fully inside the viewport, `margin` px from either edge."""
        rect, sent = await self._settle(selector), 0
        while sent < SCROLL_STEPS:
            dy = _offset(rect, margin)
            if abs(dy) < 1:
                break
            for delta in self.motion.wheel(dy)[:SCROLL_STEPS - sent]:
                await self.cdp("Input.dispatchMouseEvent", {
                    "type": "mouseWheel", "x": self.pointer.x, "y": self.pointer.y,
                    "deltaX": 0, "deltaY": delta})
                sent += 1
                await asyncio.sleep(self.motion.between(WHEEL_GAP_MS) / 1000)
            rect = await self._settle(selector)
        return Box(rect["x"], rect["y"], rect["width"], rect["height"])

    async def hits(self, selector: str, point: Point) -> bool:
        """Whether a press at `point` lands on the element, inside it, or on its label."""
        return bool(await self.evaluate(
            f"({load('drive_hits')})({json.dumps(selector)}, {point.x}, {point.y})"))

    async def _move(self, end: Point, start: Point | None = None) -> None:
        here = start or self.pointer
        if here == ORIGIN:  # nowhere yet: come in from below, as a hand would
            here = Point(end.x + self.motion.rng.uniform(-200, 200), end.y + 120)
        for step in self.motion.path(here, end):
            await asyncio.sleep(step.delay_ms / 1000)
            await self.cdp("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": step.x, "y": step.y})
        self.pointer = end

    async def _press(self, end: Point) -> None:
        await asyncio.sleep(self.motion.dwell_ms() / 1000)
        press = {"x": end.x, "y": end.y, "button": "left", "clickCount": 1}
        await self.cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "buttons": 1, **press})
        await asyncio.sleep(self.motion.press_ms() / 1000)
        await self.cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "buttons": 0, **press})

    async def click(self, target: str | Box, start: Point | None = None) -> Point:
        """A human-shaped CDP click inside the box, or on the selector once it is hit-tested."""
        if isinstance(target, Box):
            end = self.motion.target(target)
            await self._move(end, start)
        else:
            for attempt in range(CLICK_TRIES):
                end = self.motion.target(await self.scroll_into_view(target))
                await self._move(end, start if attempt == 0 else None)
                if await self.hits(target, end):
                    break
                if attempt < CLICK_TRIES - 1:
                    await asyncio.sleep(MISS_SECONDS)
        await self._press(end)
        return end

    async def type(self, text: str) -> None:
        """Key `text` into the focused element on an absolute schedule, replies pipelined."""
        futures: list[asyncio.Future[dict[str, Any]]] = []
        at = self.clock()
        for char, delay in zip(text, self.motion.key_delays(text), strict=True):
            at += delay / 1000
            await asyncio.sleep(max(0.0, at - self.clock()))
            if char == "\n" or (char.isascii() and char.isprintable()):
                keys = ENTER if char == "\n" else {"key": char}
                down = {"type": "keyDown", **keys, "text": "\r" if char == "\n" else char}
                futures.append(await self._command("Input.dispatchKeyEvent", down))
                up = at + self.motion.between(KEY_UP_MS) / 1000
                await asyncio.sleep(max(0.0, up - self.clock()))
                futures.append(await self._command("Input.dispatchKeyEvent",
                                                   {"type": "keyUp", **keys}))
            else:
                futures.append(await self._command("Input.insertText", {"text": char}))
        failed = [reply for reply in await asyncio.gather(*futures) if "error" in reply]
        if failed:
            raise DriveError(str(failed[0]["error"]))

    async def _key(self, params: dict[str, Any]) -> None:
        await self.cdp("Input.dispatchKeyEvent", {"type": "keyDown", **params})
        await asyncio.sleep(self.motion.between(KEY_UP_MS) / 1000)
        keys = {key: value for key, value in params.items() if key != "commands"}
        await self.cdp("Input.dispatchKeyEvent", {"type": "keyUp", **keys})

    async def read_value(self, selector: str) -> Any:
        """An input's value, a select's chosen text, or a radio/checkbox group's checked values."""
        return await self.evaluate(f"({load('drive_value')})({json.dumps(selector)})")

    async def fill_text(self, selector: str, value: str) -> bool:
        """Click, clear, type and read back; True once the element holds `value`."""
        for _ in range(FILL_TRIES):
            await self.click(selector)
            if not await self.evaluate(FOCUSED % json.dumps(selector)):
                continue
            if await self.read_value(selector):
                await self._key({"key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65,
                                 "modifiers": CTRL, "commands": ["selectAll"]})
                await self._key({"key": "Backspace", "code": "Backspace",
                                 "windowsVirtualKeyCode": 8})
            await self.type(value)
            if str(await self.read_value(selector) or "").strip() == value.strip():
                return True
        return False

    async def upload_file(self, name: str, data: bytes) -> str:
        """Hand the agent a PDF or DOCX; returns the path Chrome may be given."""
        reply = await self._ask({"type": "upload_file", "name": name,
                                 "data_b64": base64.b64encode(data).decode()})
        return str(reply["path"])

    async def upload(self, selector: str, name: str, data: bytes) -> None:
        """Attach a file through the page's own chooser: a real click, then the agent's path."""
        path = await self.upload_file(name, data)
        await self.cdp("Page.setInterceptFileChooserDialog", {"enabled": True})
        try:
            await self.click(selector)
            while True:
                event = await asyncio.wait_for(self.events.get(), CHOOSER_SECONDS)
                if event.get("method") == "Page.fileChooserOpened":
                    break
            node = (event.get("params") or {}).get("backendNodeId")
            await self.cdp("DOM.setFileInputFiles", {"backendNodeId": node, "files": [path]})
        finally:
            with contextlib.suppress(DriveError):
                await self.cdp("Page.setInterceptFileChooserDialog", {"enabled": False})

    async def os(self, ops: list[dict[str, Any]]) -> dict[str, Any]:
        """One batch of OS input; the tab stays detached until `attach()`."""
        return await self._ask({"type": "os", "ops": ops})

    async def attach(self) -> None:
        """A page session on the tab again, for reads after OS input."""
        await self._ask({"type": "attach"})

    async def mismatches(self, expected: dict[str, Any]) -> list[str]:
        """The selectors whose value is not what was filled: lists need every item checked."""
        wrong: list[str] = []
        for selector, want in expected.items():
            got = await self.read_value(selector)
            if isinstance(want, list):
                held = isinstance(got, list) and {str(item) for item in want} <= set(map(str, got))
            else:
                held = str(got if got is not None else "").strip() == str(want).strip()
            if not held:
                wrong.append(selector)
        return wrong

    async def submit(self, experiment: int, selector: str, knobs: dict[str, Any] | None = None,
                     expected: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read back `expected`, scroll the button into view, press by `experiment`'s route.

        A field that does not hold what was filled raises `DriveError` and nothing is pressed.
        """
        wrong = await self.mismatches(expected) if expected else []
        if wrong:
            raise DriveError(f"the form does not hold what was filled: {', '.join(wrong)}")
        await self.scroll_into_view(selector)
        await asyncio.sleep(self.motion.rng.uniform(*SUBMIT_PAUSE))
        await self._send({"type": "submit", "experiment": experiment, "selector": selector,
                          "knobs": knobs or {}})
        return await self._reply(OUTCOME_SECONDS)

    async def release(self) -> None:
        """Give the lease back and close the socket."""
        with contextlib.suppress(Exception):
            await self.websocket.send(json.dumps({"type": "release"}))
            await self.websocket.close()
        self._reader.cancel()


def _offset(rect: dict[str, float], margin: float) -> float:
    """How far to scroll down (negative: up) to fit the rect inside the margins.

    A rect taller than the space between them is brought top first.
    """
    low, high = margin, rect["view"] - margin
    top, bottom = rect["y"], rect["y"] + rect["height"]
    if top < low or bottom - top > high - low:
        return top - low
    return max(0.0, bottom - high)
