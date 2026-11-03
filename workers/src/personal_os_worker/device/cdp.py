"""The raw CDP connection under the live view: one websocket, flattened per-tab sessions."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from personal_os_worker.core.tls import tls

COMMAND_SECONDS = 15.0


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
