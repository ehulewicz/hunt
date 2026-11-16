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
from typing import Any

from personal_os_worker.device.motion import Box, Motion, Point

CHOOSER_SECONDS = 10.0
OUTCOME_SECONDS = 120.0


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

    async def cdp(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One allowed CDP command on the leased tab, awaited."""
        number = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[number] = future
        await self._send({"id": number, "method": method, "params": params or {}})
        reply = await future
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

    async def click(self, target: str | Box, start: Point | None = None) -> Point:
        """A human-shaped CDP click inside the box (or the selector's box)."""
        box = await self.find(target) if isinstance(target, str) else target
        end = self.motion.target(box)
        here = start or Point(end.x + self.motion.rng.uniform(-200, 200), end.y + 120)
        for step in self.motion.path(here, end):
            await asyncio.sleep(step.delay_ms / 1000)
            await self.cdp("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": step.x, "y": step.y})
        await asyncio.sleep(self.motion.dwell_ms() / 1000)
        press = {"x": end.x, "y": end.y, "button": "left", "clickCount": 1}
        await self.cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "buttons": 1, **press})
        await asyncio.sleep(self.motion.press_ms() / 1000)
        await self.cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "buttons": 0, **press})
        return end

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

    async def submit(self, experiment: int, selector: str,
                     knobs: dict[str, Any] | None = None) -> dict[str, Any]:
        """Press by `experiment`'s route; returns the outcome message."""
        await self._send({"type": "submit", "experiment": experiment, "selector": selector,
                          "knobs": knobs or {}})
        return await self._reply(OUTCOME_SECONDS)

    async def release(self) -> None:
        """Give the lease back and close the socket."""
        with contextlib.suppress(Exception):
            await self.websocket.send(json.dumps({"type": "release"}))
            await self.websocket.close()
        self._reader.cancel()
