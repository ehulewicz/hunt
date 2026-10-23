"""The live view: one Chrome tab screencast to the page, and the page's input sent back.

This is a raw CDP connection to the browser endpoint, separate from the apply
run's Playwright connection. It attaches only to the tab being shown and uses
the Page and Input domains there (Target lists the tabs), so Playwright never
attaches to every tab a second time. Each JPEG frame goes to the viewer before
it is acknowledged, which is Chrome's own flow control: a slow viewer gets fewer
frames, never a backlog.

The page sends positions as fractions of the image and DOM key fields as they
are; turning them into CDP coordinates and key events happens here, where it is
tested, against the last frame's metadata.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from personal_os_worker.core.tls import tls

QUALITY = 90
MAX_SIDE = 2560
DEFAULT_SIZE = (1280, 800)
# Narrower than this, most boards switch to their tablet layout.
MIN_LAYOUT_WIDTH = 1024
MAX_SCALE = 3.0
# Zoomed to its text, a page is laid out no narrower than this, where boards
# still show a form rather than a phone's.
MIN_FIT_WIDTH = 800
# The room left either side of the text, in the page's own pixels.
FIT_MARGIN = 16
# A fit that would move the layout less than this keeps it, so the page never jitters.
FIT_STEP = 24
# How long a page is given to paint its form before its text is measured.
FIT_SECONDS = 0.8
# Ignoring the outermost few percent of text on each side drops a skip link or a
# stray icon at the very edge without dropping a real column.
FIT_TRIM = 0.02
# Another client clearing its own override clears this one too; how often it is restored.
REASSERT_SECONDS = 1.0
# A minimized or idle tab may not paint; one screenshot shows it until it does.
FIRST_FRAME_SECONDS = 1.5
# How long a tab switch waits for the stream to finish opening.
OPEN_SECONDS = 20.0
COMMAND_SECONDS = 15.0
LINE_PIXELS = 40
NOT_TABS = ("chrome://", "devtools://", "chrome-extension://", "chrome-untrusted://")

BUTTONS = {0: "left", 1: "middle", 2: "right", 3: "back", 4: "forward"}
MOUSE_EVENTS = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased"}
CODES = {
    "Enter": 13, "NumpadEnter": 13, "Tab": 9, "Backspace": 8, "Escape": 27, "Space": 32,
    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40, "Home": 36, "End": 35,
    "PageUp": 33, "PageDown": 34, "Insert": 45, "Delete": 46, "CapsLock": 20,
    "ShiftLeft": 16, "ShiftRight": 16, "ControlLeft": 17, "ControlRight": 17,
    "AltLeft": 18, "AltRight": 18, "MetaLeft": 91, "MetaRight": 92, "ContextMenu": 93,
    "Semicolon": 186, "Equal": 187, "Comma": 188, "Minus": 189, "Period": 190, "Slash": 191,
    "Backquote": 192, "BracketLeft": 219, "Backslash": 220, "BracketRight": 221, "Quote": 222,
    "IntlBackslash": 226, "NumpadMultiply": 106, "NumpadAdd": 107, "NumpadSubtract": 109,
    "NumpadDecimal": 110, "NumpadDivide": 111,
}
# When a key arrives without a code, its name still says which key it was.
KEYS = {
    "Enter": 13, "Tab": 9, "Backspace": 8, "Escape": 27, " ": 32, "ArrowLeft": 37,
    "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40, "Home": 36, "End": 35, "PageUp": 33,
    "PageDown": 34, "Delete": 46, "Shift": 16, "Control": 17, "Alt": 18, "Meta": 91,
}


def screencast_size(view: dict[str, Any]) -> tuple[int, int]:
    """The largest frame worth sending: the viewer's pane in device pixels."""
    try:
        ratio = max(1.0, float(view.get("dpr") or 1))
        width = round(float(view["width"]) * ratio)
        height = round(float(view["height"]) * ratio)
    except (KeyError, TypeError, ValueError):
        return DEFAULT_SIZE
    return max(200, min(MAX_SIDE, width)), max(200, min(MAX_SIDE, height))


def layout_size(view: dict[str, Any], fit: int | None = None) -> dict[str, Any] | None:
    """The viewport the shown tab is laid out at: the viewer's pane, at its pixel density.

    A pane narrower than a desktop keeps the desktop layout at the pane's shape,
    and is shown smaller. `fit` lays it out narrower than the pane, so the text
    is shown larger, rendered at the zoomed density rather than stretched. None
    when the viewer gave no usable size.
    """
    try:
        width = float(view["width"])
        height = float(view["height"])
        ratio = float(view.get("dpr") or 1)
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    layout_width = fit or max(MIN_LAYOUT_WIDTH, round(width))
    zoom = max(1.0, width / layout_width)
    return {
        "width": layout_width,
        "height": round(layout_width * height / width),
        "deviceScaleFactor": round(min(MAX_SCALE, max(1.0, ratio * zoom)), 3),
        "mobile": False,
    }


# Where the page's text and controls sit across the viewport, with how much each one weighs.
TEXT_BOXES = r"""(() => {
  const boxes = [], width = document.documentElement.clientWidth;
  const add = (box, weight) => {
    if (box.width >= 2 && box.height >= 2 && box.right > 0 && box.left < width) {
      boxes.push([box.left, box.right, weight]);
    }
  };
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  for (let node; (node = walk.nextNode()) && boxes.length < 5000;) {
    const text = node.data.trim();
    if (!text) continue;
    range.selectNodeContents(node);
    for (const box of range.getClientRects()) add(box, text.length);
  }
  const controls = 'input:not([type=hidden]),textarea,select,button,[role=combobox]';
  for (const control of document.querySelectorAll(controls)) {
    add(control.getBoundingClientRect(), 10);
  }
  return {width, boxes};
})()"""


def text_span(boxes: list[Any]) -> tuple[float, float] | None:
    """The left and right edges of the page's text, past the outermost few percent of it."""
    rows: list[tuple[float, float, float]] = []
    for box in boxes:
        with contextlib.suppress(TypeError, ValueError, IndexError):
            rows.append((float(box[0]), float(box[1]), max(1.0, float(box[2]))))
    if not rows:
        return None
    trim = FIT_TRIM * sum(weight for _, _, weight in rows)

    def edge(values: list[tuple[float, float]]) -> float:
        passed = 0.0
        for value, weight in values:
            passed += weight
            if passed > trim:
                return value
        return values[-1][0]

    left = edge(sorted((row[0], row[2]) for row in rows))
    right = edge(sorted(((row[1], row[2]) for row in rows), reverse=True))
    return (left, right) if right > left else None


def fit_width(current: int, base: int, client_width: float, span: tuple[float, float]) -> int:
    """The layout width that leaves FIT_MARGIN either side of the text.

    Both sides narrow by the same amount, so a centered form ends up centered with
    the margin on each side and a form against one edge is not narrowed at all.
    Text wider than the page widens it again, up to the pane's own layout.
    """
    left, right = span
    spare = min(left, client_width - right) - FIT_MARGIN
    return max(MIN_FIT_WIDTH, min(base, round(current - 2 * spare)))


def _fraction(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def page_point(fx: Any, fy: Any, meta: dict[str, Any]) -> tuple[float, float]:
    """A position on the image, as fractions, in the tab's CSS pixels."""
    width = float(meta.get("deviceWidth") or 0)
    height = float(meta.get("deviceHeight") or 0)
    return _fraction(fx) * width, _fraction(fy) * height


def modifiers(message: dict[str, Any]) -> int:
    return (
        (1 if message.get("alt") else 0)
        | (2 if message.get("ctrl") else 0)
        | (4 if message.get("meta") else 0)
        | (8 if message.get("shift") else 0)
    )


def mouse_params(message: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    kind = MOUSE_EVENTS.get(str(message.get("event")), "mouseMoved")
    x, y = page_point(message.get("x"), message.get("y"), meta)
    params: dict[str, Any] = {
        "type": kind,
        "x": x,
        "y": y,
        "button": "none",
        "buttons": int(message.get("buttons") or 0),
        "modifiers": modifiers(message),
        "pointerType": "mouse",
    }
    if kind != "mouseMoved":
        params["button"] = BUTTONS.get(int(message.get("button") or 0), "left")
        params["clickCount"] = max(1, int(message.get("clickCount") or 1))
    return params


def wheel_params(message: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """A wheel turn in pixels, whatever unit the viewer's browser counted it in."""
    x, y = page_point(message.get("x"), message.get("y"), meta)
    mode = int(message.get("mode") or 0)
    scale_x = {1: LINE_PIXELS, 2: float(meta.get("deviceWidth") or 0)}.get(mode, 1)
    scale_y = {1: LINE_PIXELS, 2: float(meta.get("deviceHeight") or 0)}.get(mode, 1)
    return {
        "type": "mouseWheel",
        "x": x,
        "y": y,
        "deltaX": float(message.get("dx") or 0) * scale_x,
        "deltaY": float(message.get("dy") or 0) * scale_y,
        "modifiers": modifiers(message),
        "pointerType": "mouse",
    }


def virtual_key(key: str, code: str) -> int:
    """The Windows virtual-key code Chrome expects, from the DOM's code, else its key."""
    if code.startswith("Key") and len(code) == 4:
        return ord(code[3].upper())
    if code.startswith("Digit") and len(code) == 6 and code[5].isdigit():
        return 48 + int(code[5])
    if code.startswith("Numpad") and len(code) == 7 and code[6].isdigit():
        return 96 + int(code[6])
    if code.startswith("F") and code[1:].isdigit() and 1 <= int(code[1:]) <= 24:
        return 111 + int(code[1:])
    if code in CODES:
        return CODES[code]
    if key in KEYS:
        return KEYS[key]
    if len(key) == 1 and key.isalnum() and key.isascii():
        return ord(key.upper())
    return 0


def key_events(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The CDP key event for one DOM keydown or keyup; nothing for anything else."""
    event = message.get("event")
    key = str(message.get("key") or "")
    code = str(message.get("code") or "")
    if event not in {"down", "up"} or not key:
        return []
    ctrl, alt, meta = bool(message.get("ctrl")), bool(message.get("alt")), bool(message.get("meta"))
    code_number = virtual_key(key, code)
    params: dict[str, Any] = {
        "type": "keyUp",
        "key": key,
        "code": code,
        "windowsVirtualKeyCode": code_number,
        "nativeVirtualKeyCode": code_number,
        "modifiers": modifiers(message),
        "autoRepeat": bool(message.get("repeat")),
    }
    location = message.get("location")
    if location in {1, 2}:
        params["location"] = location
    elif location == 3:
        params["isKeypad"] = True
    if event == "up":
        return [params]
    # Ctrl alone makes a shortcut; Ctrl with Alt is AltGr, which types a character.
    printable = len(key) == 1 and not meta and not (ctrl and not alt)
    text = "\r" if key == "Enter" else key if printable else None
    if text is None:
        params["type"] = "rawKeyDown"
    else:
        params.update(type="keyDown", text=text, unmodifiedText=text)
    return [params]


def pack_frame(header: dict[str, Any], data: bytes) -> bytes:
    """One binary message: a 4-byte length, the JSON header, then the JPEG."""
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return len(encoded).to_bytes(4, "big") + encoded + data


def is_tab(info: dict[str, Any]) -> bool:
    return info.get("type") == "page" and not str(info.get("url") or "").startswith(NOT_TABS)


class CdpError(RuntimeError):
    """Chrome answered a command with an error, or the connection went away."""


EventHandler = Callable[[str, dict[str, Any], str | None], None]


class Cdp(Protocol):
    async def send(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


def _read_failure(future: asyncio.Future[dict[str, Any]]) -> None:
    if not future.cancelled():
        future.exception()


class CdpConnection:
    """One websocket to the browser endpoint, with flattened per-tab sessions."""

    def __init__(self, websocket: Any, on_event: EventHandler) -> None:
        self._websocket = websocket
        self._on_event = on_event
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, cdp_url: str, on_event: EventHandler) -> CdpConnection:
        from websockets.asyncio.client import connect

        async with httpx.AsyncClient(timeout=5.0, verify=tls()) as client:
            response = await client.get(f"{cdp_url.rstrip('/')}/json/version")
            response.raise_for_status()
            endpoint = str(response.json()["webSocketDebuggerUrl"])
        # Frames are large and already JPEG; Chrome is on loopback, so neither
        # compression nor a ping is worth its time.
        websocket = await connect(endpoint, max_size=None, ping_interval=None, compression=None)
        return cls(websocket, on_event)

    async def _read(self) -> None:
        try:
            async for raw in self._websocket:
                message = json.loads(raw)
                if "id" in message:
                    future = self._pending.pop(int(message["id"]), None)
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        future.set_exception(CdpError(str(message["error"].get("message"))))
                    else:
                        future.set_result(message.get("result") or {})
                elif "method" in message:
                    self._on_event(
                        str(message["method"]), message.get("params") or {},
                        message.get("sessionId"),
                    )
        except Exception:  # the connection is gone either way; say so to every waiter
            pass
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CdpError("Chrome closed the connection"))
        self._pending.clear()
        self._on_event("Stream.closed", {}, None)

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        number = next(self._ids)
        message: dict[str, Any] = {"id": number, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        # Chrome closing the connection fails every pending command, including one whose
        # send already failed and that no one will await; its error is read here, so it
        # is not reported as never retrieved. A caller still waiting still gets it.
        future.add_done_callback(_read_failure)
        self._pending[number] = future
        try:
            await self._websocket.send(json.dumps(message))
        except Exception as error:
            self._pending.pop(number, None)
            raise CdpError(str(error)) from None
        try:
            return await asyncio.wait_for(future, COMMAND_SECONDS)
        except TimeoutError:
            self._pending.pop(number, None)
            raise CdpError(f"{method} timed out") from None

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._websocket.close()
        self._reader.cancel()


class Stream:
    """The tab the viewer sees, and the viewer's hands on it."""

    def __init__(
        self,
        *,
        connect: Callable[[EventHandler], Awaitable[Cdp]],
        send_frame: Callable[[dict[str, Any], bytes], Awaitable[None]],
        send_json: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._connect = connect
        self._send_frame = send_frame
        self._send_json = send_json
        self._cdp: Cdp | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self.targets: dict[str, dict[str, Any]] = {}
        self.current: str | None = None
        self.session: str | None = None
        self.meta: dict[str, Any] | None = None
        self.view: dict[str, Any] = {}
        # The layout width the shown tab's text fits, and the override last sent for it.
        self.fit: int | None = None
        self.metrics: dict[str, Any] | None = None
        self._fits = itertools.count(1)
        self._fitting = 0
        self._reasserted = 0.0
        # Wheel turns not yet in Chrome, summed in pixels; see wheel().
        self._wheel: dict[str, Any] | None = None
        self._wheeling = False
        # Set once the first tab is shown: a switch asked for sooner waits for it, and
        # no tab list goes out while discovery is still reporting tabs one by one.
        self._ready = asyncio.Event()
        self._closed = False

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        async def guarded() -> None:
            with contextlib.suppress(CdpError):
                await coroutine

        task = asyncio.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def open(self, view: dict[str, Any], *, run_tab: str | None = None) -> None:
        self.view = view
        cdp = await self._connect(self._on_event)
        if self._closed:
            # The viewer left while Chrome was answering.
            await cdp.close()
            return
        self._cdp = cdp
        await self._cdp.send("Target.setDiscoverTargets", {"discover": True})
        infos = (await self._cdp.send("Target.getTargets")).get("targetInfos") or []
        self.targets = {str(info["targetId"]): info for info in infos if is_tab(info)}
        first = run_tab if run_tab in self.targets else next(iter(self.targets), None)
        if first is not None:
            await self.show(first)
        self._ready.set()
        await self._emit_tabs()

    async def close(self) -> None:
        self._closed = True
        self._ready.set()  # nothing waits on a stream that will never open
        cdp, self._cdp = self._cdp, None
        if cdp is None:
            return
        if self.session is not None:
            with contextlib.suppress(CdpError):
                await cdp.send("Page.stopScreencast", {}, self.session)
            with contextlib.suppress(CdpError):
                await cdp.send("Emulation.clearDeviceMetricsOverride", {}, self.session)
        self.session = self.current = None
        await cdp.close()

    async def show(self, target_id: str, *, activate: bool = False) -> None:
        """Stream this tab, leaving the one shown before."""
        cdp = self._cdp
        if cdp is None or target_id not in self.targets:
            return
        async with self._lock:
            if self.session is not None:
                with contextlib.suppress(CdpError):
                    await cdp.send("Page.stopScreencast", {}, self.session)
                # The tab the viewer leaves goes back to its own size.
                with contextlib.suppress(CdpError):
                    await cdp.send("Emulation.clearDeviceMetricsOverride", {}, self.session)
                with contextlib.suppress(CdpError):
                    await cdp.send("Target.detachFromTarget", {"sessionId": self.session})
            self.session, self.meta, self._wheel, self.fit = None, None, None, None
            attached = await cdp.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            self.current, self.session = target_id, str(attached["sessionId"])
            if activate:
                await cdp.send("Target.activateTarget", {"targetId": target_id})
            await self._restore_window(cdp, target_id)
            await cdp.send("Page.enable", {}, self.session)
            await self._start(cdp)
        await self._emit_tabs()
        self._spawn(self._first_frame(target_id))
        self._refit()

    async def _restore_window(self, cdp: Cdp, target_id: str) -> None:
        """A minimized window paints nothing, so a viewer un-minimizes it."""
        with contextlib.suppress(CdpError, KeyError, TypeError):
            window = await cdp.send("Browser.getWindowForTarget", {"targetId": target_id})
            if window["bounds"].get("windowState") == "minimized":
                await cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
                )

    async def _start(self, cdp: Cdp) -> None:
        # Chrome lays out a tab in a minimized window at whatever size it likes
        # (2560x1271 for one tab, 1065x1751 for the next): the pane then had to
        # scroll, or shrink the page. Laid out at the pane's shape, a frame fills
        # it exactly, and every wheel turn scrolls Chrome.
        await self._override(cdp)
        width, _ = screencast_size(self.view)
        await cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": QUALITY, "maxWidth": width,
             "everyNthFrame": 1},
            self.session,
        )

    async def _override(self, cdp: Cdp) -> None:
        self.metrics = layout_size(self.view, self.fit)
        if self.metrics is not None:
            await cdp.send("Emulation.setDeviceMetricsOverride", self.metrics, self.session)

    def _refit(self) -> None:
        """Measure the shown page's text once it settles; only the latest request runs."""
        self._fitting = next(self._fits)
        self._spawn(self._fit(self._fitting))

    async def _fit(self, number: int) -> None:
        await asyncio.sleep(FIT_SECONDS)
        cdp, session = self._cdp, self.session
        if cdp is None or session is None or number != self._fitting or self.metrics is None:
            return
        answer = await cdp.send(
            "Runtime.evaluate", {"expression": TEXT_BOXES, "returnByValue": True}, session
        )
        page = (answer.get("result") or {}).get("value") or {}
        span = text_span(page.get("boxes") or [])
        if span is None or not page.get("width") or number != self._fitting:
            return
        current = int(self.metrics["width"])
        base = int((layout_size(self.view) or self.metrics)["width"])
        width = fit_width(current, base, float(page["width"]), span)
        if abs(width - current) < FIT_STEP or session != self.session:
            return
        self.fit = None if width == base else width
        await self._override(cdp)

    async def _reassert(self, meta: dict[str, Any]) -> None:
        """Put the override back when the frames say Chrome dropped it.

        Chrome keeps one override per tab, not one per client: the apply run's own
        connection clearing its override, or any other client detaching, leaves
        the tab at its window's size, which is the shape of whatever monitor it
        is on. A frame at any other width than the one set says that happened.
        """
        metrics, cdp = self.metrics, self._cdp
        if metrics is None or cdp is None:
            return
        try:
            if abs(float(meta.get("deviceWidth") or 0) - metrics["width"]) <= 1:
                return
        except (TypeError, ValueError):
            return
        if time.monotonic() - self._reasserted < REASSERT_SECONDS:
            return
        self._reasserted = time.monotonic()
        await cdp.send("Emulation.setDeviceMetricsOverride", metrics, self.session)

    async def _first_frame(self, target_id: str) -> None:
        await asyncio.sleep(FIRST_FRAME_SECONDS)
        cdp, session = self._cdp, self.session
        if cdp is None or session is None or self.current != target_id or self.meta is not None:
            return
        shot = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": QUALITY},
                              session)
        metrics = await cdp.send("Page.getLayoutMetrics", {}, session)
        viewport = metrics.get("cssLayoutViewport") or {}
        if not shot.get("data") or not viewport or self.current != target_id:
            return
        if self.meta is None:
            self.meta = {"deviceWidth": viewport.get("clientWidth"),
                         "deviceHeight": viewport.get("clientHeight")}
            await self._send_frame(self._header(self.meta), base64.b64decode(shot["data"]))

    def _header(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "frame",
            "target": self.current,
            "width": meta.get("deviceWidth"),
            "height": meta.get("deviceHeight"),
        }

    async def _deliver(self, session_id: str, params: dict[str, Any]) -> None:
        cdp = self._cdp
        if cdp is None:
            return
        if session_id == self.session:
            self.meta = dict(params.get("metadata") or {})
            with contextlib.suppress(Exception):
                await self._send_frame(self._header(self.meta), base64.b64decode(params["data"]))
            self._spawn(self._reassert(self.meta))
        # Acknowledged only once delivered, so Chrome sends no faster than the viewer reads.
        await cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")},
                       session_id)

    def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Page.screencastFrame":
            if session_id is not None and session_id == self.session:
                self._spawn(self._deliver(session_id, params))
            return
        if method in {"Target.targetCreated", "Target.targetInfoChanged"}:
            info = params.get("targetInfo") or {}
            target_id = str(info.get("targetId") or "")
            if not is_tab(info):
                if self.targets.pop(target_id, None) is not None:
                    self._spawn(self._emit_tabs())
                return
            created = target_id not in self.targets
            self.targets[target_id] = info
            if created and self.current is not None and info.get("openerId") == self.current:
                # A popup the shown tab opened, like Google sign-in: follow it.
                self._spawn(self.show(target_id))
            else:
                self._spawn(self._emit_tabs())
            return
        if method in {"Page.loadEventFired", "Page.navigatedWithinDocument"}:
            # A new page, or a form's next step, has its own width of text.
            if session_id is not None and session_id == self.session:
                self._refit()
            return
        if method == "Target.targetDestroyed":
            target_id = str(params.get("targetId") or "")
            gone = self.targets.pop(target_id, None)
            if target_id == self.current:
                self.current = self.session = None
                opener = str((gone or {}).get("openerId") or "")
                back = opener if opener in self.targets else next(iter(self.targets), None)
                if back is not None:
                    self._spawn(self.show(back))
            self._spawn(self._emit_tabs())
            return
        if method == "Stream.closed" and not self._closed:
            self._spawn(self._notify_lost())

    async def _notify_lost(self) -> None:
        """Chrome closed the connection under a viewer that may itself be gone."""
        with contextlib.suppress(Exception):
            await self._send_json({"type": "stream", "state": "lost"})

    async def _emit_tabs(self) -> None:
        if not self._ready.is_set():
            return
        tabs = [
            {"id": key, "title": str(info.get("title") or ""), "url": str(info.get("url") or "")}
            for key, info in self.targets.items()
        ]
        with contextlib.suppress(Exception):
            await self._send_json({"type": "tabs", "tabs": tabs, "current": self.current})

    async def _opened(self) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), OPEN_SECONDS)
        except TimeoutError:
            return False
        return True

    async def switch(self, target_id: str) -> None:
        """The viewer picked a tab: show it, and bring it forward in Chrome too."""
        if await self._opened():
            await self.show(target_id, activate=True)

    async def follow(self, target_id: str) -> None:
        """The run moved to another tab; the view goes with it."""
        if await self._opened() and target_id != self.current:
            await self.show(target_id)

    async def resize(self, view: dict[str, Any]) -> None:
        self.view = view
        if not await self._opened():
            return
        if self._cdp is not None and self.session is not None:
            self.fit = None
            await self._cdp.send("Page.stopScreencast", {}, self.session)
            await self._start(self._cdp)
            self._refit()

    async def pointer(self, message: dict[str, Any]) -> None:
        if self._cdp is not None and self.session is not None and self.meta:
            await self._cdp.send(
                "Input.dispatchMouseEvent", mouse_params(message, self.meta), self.session
            )

    async def wheel(self, message: dict[str, Any]) -> None:
        """Turn the wheel without waiting for Chrome.

        Chrome answers each wheel event only at its next paint, so one at a time
        it takes about 60 a second, no more than a 60 Hz viewer sends. Awaited in
        the viewer's message loop, any hitch left a backlog that kept scrolling
        after the hand stopped. Turns that arrive while one is in Chrome are
        summed into the next instead.
        """
        if self._cdp is None or self.session is None or not self.meta:
            return
        params = wheel_params(message, self.meta)
        if self._wheel is not None:
            params["deltaX"] += self._wheel["deltaX"]
            params["deltaY"] += self._wheel["deltaY"]
        self._wheel = params
        if not self._wheeling:
            self._wheeling = True
            self._spawn(self._turn())

    async def _turn(self) -> None:
        try:
            while self._wheel is not None and self._cdp is not None and self.session is not None:
                params, self._wheel = self._wheel, None
                await self._cdp.send("Input.dispatchMouseEvent", params, self.session)
        finally:
            self._wheeling = False

    async def key(self, message: dict[str, Any]) -> None:
        if self._cdp is None or self.session is None:
            return
        for params in key_events(message):
            await self._cdp.send("Input.dispatchKeyEvent", params, self.session)

    async def text(self, text: str) -> None:
        if self._cdp is not None and self.session is not None and text:
            await self._cdp.send("Input.insertText", {"text": text[:20_000]}, self.session)
