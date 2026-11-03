"""The live view: one Chrome tab screencast to the page, and the page's input sent back.

This is a raw CDP connection (`cdp.py`) to the browser endpoint, separate from
the apply run's Playwright connection. It attaches only to the tab being shown and uses
the Page and Input domains there (Target lists the tabs), so Playwright never
attaches to every tab a second time. Each JPEG frame goes to the viewer before
it is acknowledged, which is Chrome's own flow control: a slow viewer gets fewer
frames, never a backlog.

The page sends positions as fractions of the image and DOM key fields as they
are; `pane.py` turns them into CDP coordinates and key events, where it is
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
from typing import Any

from personal_os_worker.device.cdp import (
    COMMAND_SECONDS,
    Cdp,
    CdpConnection,
    CdpError,
    EventHandler,
    _read_failure,
)
from personal_os_worker.device.pane import (
    BUTTONS,
    CODES,
    DEFAULT_SIZE,
    FIT_MARGIN,
    FIT_TRIM,
    KEYS,
    LINE_PIXELS,
    MAX_SCALE,
    MAX_SIDE,
    MIN_FIT_WIDTH,
    MIN_LAYOUT_WIDTH,
    MIN_ZOOM,
    MOUSE_EVENTS,
    TEXT_BOXES,
    _fraction,
    fit_width,
    key_events,
    laid_out,
    layout_size,
    modifiers,
    mouse_params,
    page_point,
    screencast_size,
    text_span,
    virtual_key,
    wheel_params,
)

__all__ = [
    "BUTTONS",
    "CODES",
    "COMMAND_SECONDS",
    "DEFAULT_SIZE",
    "FIRST_FRAME_SECONDS",
    "FIT_MARGIN",
    "FIT_SECONDS",
    "FIT_STEP",
    "FIT_TRIM",
    "KEYS",
    "LINE_PIXELS",
    "MAX_SCALE",
    "MAX_SIDE",
    "MIN_FIT_WIDTH",
    "MIN_LAYOUT_WIDTH",
    "MIN_ZOOM",
    "MOUSE_EVENTS",
    "NOT_TABS",
    "OPEN_SECONDS",
    "QUALITY",
    "REASSERT_SECONDS",
    "TEXT_BOXES",
    "Cdp",
    "CdpConnection",
    "CdpError",
    "EventHandler",
    "Stream",
    "_fraction",
    "_read_failure",
    "fit_width",
    "is_tab",
    "key_events",
    "laid_out",
    "layout_size",
    "modifiers",
    "mouse_params",
    "pack_frame",
    "page_point",
    "screencast_size",
    "text_span",
    "virtual_key",
    "wheel_params",
]

QUALITY = 90
# A fit that would move the layout less than this keeps it, so the page never jitters.
FIT_STEP = 24
# How long a page is given to paint its form before its text is measured.
FIT_SECONDS = 0.8
# How long the tab may stay at another size before the override is put back. A
# full-page screenshot lays it out at the whole page for less than this, and
# Chrome gives the override back itself.
REASSERT_SECONDS = 1.0
# A minimized or idle tab may not paint; one screenshot shows it until it does.
FIRST_FRAME_SECONDS = 1.5
# How long a tab switch waits for the stream to finish opening.
OPEN_SECONDS = 20.0
NOT_TABS = ("chrome://", "devtools://", "chrome-extension://", "chrome-untrusted://")


def pack_frame(header: dict[str, Any], data: bytes) -> bytes:
    """One binary message: a 4-byte length, the JSON header, then the JPEG."""
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return len(encoded).to_bytes(4, "big") + encoded + data


def is_tab(info: dict[str, Any]) -> bool:
    return info.get("type") == "page" and not str(info.get("url") or "").startswith(NOT_TABS)


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
        # When frames started arriving at another size than the layout; see _reassert().
        self._astray: float | None = None
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
            self._astray = None
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
        cdp, session, base = self._cdp, self.session, layout_size(self.view)
        if cdp is None or session is None or number != self._fitting or self.metrics is None:
            return
        if base is None:  # the viewer sent a size it could not lay out
            return
        pane = round(float(self.view["width"]))
        answer = await cdp.send(
            "Runtime.evaluate", {"expression": TEXT_BOXES, "returnByValue": True}, session
        )
        page = (answer.get("result") or {}).get("value") or {}
        span = text_span(page.get("boxes") or [])
        if span is None or not page.get("width") or number != self._fitting:
            return
        current = int(self.metrics["width"])
        client_width = float(page["width"])
        scroll_width = float(page.get("scroll") or client_width)
        width = fit_width(current, pane, client_width, span, scroll_width)
        if abs(width - current) < FIT_STEP or session != self.session:
            return
        self.fit = None if width == base["width"] else width
        await self._override(cdp)

    async def _reassert(self, since: float) -> None:
        """Put the override back when the frames say Chrome dropped it.

        Chrome keeps one override per tab, not one per client: the apply run's own
        connection clearing its override, or any other client detaching, leaves
        the tab at its window's size, which is the shape of whatever monitor it
        is on. The run's full-page evidence screenshot also lays the tab out at
        the whole page while it shoots, then Chrome restores the override itself.
        So only frames still at another size after REASSERT_SECONDS say it is gone.
        """
        await asyncio.sleep(REASSERT_SECONDS)
        metrics, cdp = self.metrics, self._cdp
        if self._astray != since or metrics is None or cdp is None:
            return
        self._astray = None
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
            meta = dict(params.get("metadata") or {})
            if self.metrics is None or laid_out(meta, self.metrics):
                self._astray = None
                self.meta = meta
                with contextlib.suppress(Exception):
                    await self._send_frame(self._header(meta), base64.b64decode(params["data"]))
            elif self._astray is None:
                # Not shown: the viewer keeps the last frame at its layout, never a
                # whole page squeezed into the pane.
                self._astray = time.monotonic()
                self._spawn(self._reassert(self._astray))
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
