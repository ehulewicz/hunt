"""OS input on the leased tab with no debugger attached: the `os` and `attach` messages.

The OS press routes (experiments 2-4, `press.py`) and experiment 4's fill use
this. Before any OS input the agent closes the live-view stream, which clears
its `Emulation` override, brings the leased tab to the front, and measures the
viewport origin with one isolated-world read of `ORIGIN_EXPRESSION` and the
tab's title, which finds its Chrome window. It then detaches every CDP session
it holds on the tab: the lease's page session and its browser websocket
(the stream is already closed). While detached no command is forwarded, no
Chrome event is read and the Hunt page opens no stream.

`{"type": "os", "ops": [...]}` runs one batch through `OsInput`: `click_at`
`{x, y}` (CSS pixels from a read made while attached), `type {text}`,
`key {name}`, `wheel {dy}` and `wait {ms}`. It is refused when the switch is
off, the listing is locked, or the window cannot be focused, and answered with
`{"type": "os_done", "ok", ...}`. The tab stays detached until the driver sends
`{"type": "attach"}`, which opens a new connection and page session.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from personal_os_worker.device.cdp import CdpError
from personal_os_worker.device.drive import DriveManager, Lease
from personal_os_worker.device.motion import Box, Motion, Point
from personal_os_worker.device.osinput import (
    KEYS,
    ORIGIN_EXPRESSION,
    OsInput,
    OsInputError,
    Ran,
    ViewportOrigin,
    click_ops,
    key_op,
    start_point,
    type_ops,
    wait_op,
    wheel_ops,
)

MAX_OPS = 50
MAX_TEXT = 2000
MAX_WAIT_MS = 10_000
MAX_WHEEL = 5000.0


class OsRefused(RuntimeError):
    """OS input the agent will not run; the message is the reason."""


def _number(op: dict[str, Any], key: str, limit: float) -> float:
    value = op.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or abs(value) > limit:
        raise OsRefused(f"{op.get('op')} needs a number {key} within {limit:g}")
    return float(value)


def parse_ops(raw: object) -> list[dict[str, Any]]:
    """The driver's ops, checked, or OsRefused naming the first bad one."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_OPS:
        raise OsRefused(f"send 1 to {MAX_OPS} ops")
    ops: list[dict[str, Any]] = []
    for op in raw:
        kind = op.get("op") if isinstance(op, dict) else None
        if kind == "click_at":
            ops.append({"op": kind, "x": _number(op, "x", 1e5), "y": _number(op, "y", 1e5)})
        elif kind == "type":
            text = op.get("text")
            if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                raise OsRefused(f"type needs text of 1 to {MAX_TEXT} characters")
            ops.append({"op": kind, "text": text})
        elif kind == "key":
            name = str(op.get("name") or "").strip().lower()
            if name not in KEYS:
                raise OsRefused(f"unknown key {name!r}; known: {', '.join(sorted(KEYS))}")
            ops.append({"op": kind, "name": name})
        elif kind == "wheel":
            ops.append({"op": kind, "dy": _number(op, "dy", MAX_WHEEL)})
        elif kind == "wait":
            ops.append({"op": kind, "ms": int(_number(op, "ms", MAX_WAIT_MS))})
        else:
            raise OsRefused(f"unknown op {kind!r}")
    return ops


def plan(
    motion: Motion, origin: ViewportOrigin, ran: Ran, ops: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The OsInput batch for checked ops: CSS points mapped to the screen, human timings."""
    batch: list[dict[str, Any]] = []
    cursor: Point | None = None
    for op in ops:
        kind = op["op"]
        if kind == "click_at":
            x, y = origin.to_screen(Point(op["x"], op["y"]))
            if not ran.inside(x, y):
                raise OsRefused(f"the point {x},{y} is outside the Chrome window {ran.rect}")
            end = Point(float(x), float(y))
            batch += click_ops(motion, cursor or start_point(motion, ran, end), end)
            cursor = end
        elif kind == "type":
            batch += type_ops(motion, op["text"])
        elif kind == "key":
            batch.append(key_op(op["name"], motion))
        elif kind == "wheel":
            batch += wheel_ops(motion, op["dy"])
        else:
            batch.append(wait_op(op["ms"]))
    return batch


class OsDrive:
    """Detach, measure and reattach the leased tab, and run the driver's OS input."""

    def __init__(self, drive: DriveManager, os_input: OsInput | None = None) -> None:
        self.drive = drive
        self.os = os_input or OsInput()

    async def prepare(self, lease: Lease) -> ViewportOrigin:
        """The live view closed, the tab in front, its origin and window title read."""
        await self.drive.close_stream()
        cdp = lease.cdp
        if cdp is None or lease.detached:
            if not isinstance(lease.origin, ViewportOrigin) or not lease.window:
                raise OsRefused("the tab was never measured; send attach first")
            return lease.origin
        try:
            await cdp.send("Target.activateTarget", {"targetId": lease.target})
            info = await cdp.send("Target.getTargetInfo", {"targetId": lease.target})
            world = await cdp.send("Page.createIsolatedWorld",
                                   {"frameId": lease.target, "worldName": "drive-origin"},
                                   lease.session)
            read = await cdp.send("Runtime.evaluate", {
                "expression": ORIGIN_EXPRESSION, "contextId": world["executionContextId"],
                "returnByValue": True,
            }, lease.session)
            origin = ViewportOrigin.parse(read["result"]["value"])
        except (CdpError, KeyError, TypeError, ValueError, OsInputError) as error:
            raise OsRefused(f"the tab's origin could not be read: {error}") from None
        title = str((info.get("targetInfo") or {}).get("title") or "").strip()[:200]
        if not title:
            raise OsRefused("the tab has no title to find its window by")
        lease.origin, lease.window = origin, title
        return origin

    async def detach(self, lease: Lease) -> None:
        """Every CDP session on the tab gone: the page session, then the browser socket."""
        lease.detached = True
        cdp, lease.cdp = lease.cdp, None
        lease.worlds.clear()
        lease.chooser_node = None
        if cdp is None:
            return
        if lease.session:
            with contextlib.suppress(Exception):
                await cdp.send("Target.detachFromTarget", {"sessionId": lease.session})
        lease.session = ""
        with contextlib.suppress(Exception):
            await cdp.close()

    async def attach(self, lease: Lease) -> None:
        """A new connection and page session on the leased tab, and its URL checked."""
        if not lease.detached:
            return
        cdp = await self.drive.connect(self.drive.on_event)
        lease.cdp = cdp
        attached = await cdp.send("Target.attachToTarget",
                                  {"targetId": lease.target, "flatten": True})
        lease.session = str(attached["sessionId"])
        await cdp.send("Target.setDiscoverTargets", {"discover": True})
        await cdp.send("Page.enable", {}, lease.session)
        info = await cdp.send("Target.getTargetInfo", {"targetId": lease.target})
        lease.detached = False
        url = str((info.get("targetInfo") or {}).get("url") or "")
        self.drive.on_event("Target.targetInfoChanged",
                            {"targetInfo": {"targetId": lease.target, "url": url}}, None)

    async def focus(self, lease: Lease) -> Ran:
        try:
            return await asyncio.to_thread(self.os.focus_window, lease.window)
        except OsInputError as error:
            raise OsRefused(f"the Chrome window could not be focused: {error}") from None

    async def run(self, lease: Lease, batch: list[dict[str, Any]]) -> Ran:
        return await asyncio.to_thread(self.os.run, lease.window, batch)

    def in_view(self, origin: ViewportOrigin, box: Box) -> bool:
        return box.width >= 1 and box.height >= 1 and box.x >= 0 and box.y >= 0 \
            and box.x + box.width <= origin.inner_width \
            and box.y + box.height <= origin.inner_height

    async def __call__(self, lease: Lease, message: dict[str, Any]) -> dict[str, Any]:
        """`DriveManager.on_os`: an `os` batch or an `attach`, audited either way."""
        kind = str(message.get("type"))
        summary = {"ops": [op.get("op") for op in message.get("ops") or []
                           if isinstance(op, dict)][:MAX_OPS]} if kind == "os" else {}
        try:
            if kind == "attach":
                await self.attach(lease)
                self.drive.audit.write(lease.listing_id, kind, {}, "attached")
                return {"type": "attached", "target": lease.target}
            ran = await self.ops(lease, message.get("ops"))
        except (OsRefused, OsInputError, CdpError, OSError, KeyError) as error:
            self.drive.audit.write(lease.listing_id, kind, summary, f"refused: {error}")
            reply = {"type": "os_done", "ok": False, "error": str(error)}
            return reply if kind == "os" else {"type": "error", "error": str(error)}
        self.drive.audit.write(lease.listing_id, kind, summary, "ran")
        return {"type": "os_done", "ok": True, "cursor": [ran.cursor.x, ran.cursor.y]}

    async def ops(self, lease: Lease, raw: object) -> Ran:
        if not self.drive.switch.on():
            raise OsRefused("the drive switch is off")
        locks = self.drive.locks
        lock = locks.state_for(lease.listing_id, lease.url) or (
            locks.state_for(url=lease.tab_url) if lease.tab_url else None)
        if lock is not None:
            raise OsRefused(f"the listing is locked ({lock})")
        ops = parse_ops(raw)
        origin = await self.prepare(lease)
        await self.detach(lease)
        ran = await self.focus(lease)
        return await self.run(lease, plan(self.os.motion, origin, ran, ops))
