"""The agent the Hunt page connects to: status, runs, and the live session.

It listens on 127.0.0.1 only; Windows `tailscale serve` publishes it to the
tailnet over HTTPS, and nothing else reaches it. Every request carries the
user's Supabase access token, which the agent checks by asking its own local
API for `/v1/me` — the same JWT and allowed-user checks every API call gets. A
browser request must also come from the page's origin. The agent never runs a
command it was sent: it can start `os hunt apply <reference> --remote`, show a
tab, pass input to it, and pass controls, a letter, and facts to a run.

`/drive` leases one tab to a driver of the user's own on the tailnet, with an
allowlist of CDP methods and locks the device enforces (`drive.py`). It needs
the token in its Authorization header and refuses any request with an Origin,
so no web page can open it. `GET`/`POST /drive` read and set its switch.

`/session` is one websocket. The page's first message is its token; then the
agent sends JPEG frames (binary: a length, a JSON header, the image) and JSON
for tabs, the run, its log, a letter to edit, and gaps to fill, while the page
sends pointer, wheel, key, text, tab, control, letter, and fact messages. One
viewer at a time: a new one replaces the old.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

from personal_os_worker.core.config import CliConfig
from personal_os_worker.core.tls import tls
from personal_os_worker.device.drive import (
    UNAUTHORIZED,
    ApplicationStatus,
    DriveManager,
    serve_drive,
)
from personal_os_worker.device.drive_state import drive_dir
from personal_os_worker.device.heartbeat import HEARTBEAT_SECONDS, Presence
from personal_os_worker.device.identity import DeviceIdentity
from personal_os_worker.device.link import socket_path
from personal_os_worker.device.memory import MemoryWatch, gigabytes
from personal_os_worker.device.press import Press
from personal_os_worker.device.runs import RunBusy, RunManager
from personal_os_worker.device.stream import CdpConnection, Stream, pack_frame
from personal_os_worker.device.update import source_fingerprint

TOKEN_SECONDS = 60.0
AUTH_SECONDS = 10.0
# Close codes the page understands.
REPLACED, RESTARTING = 4000, 1012


class TokenVerifier:
    """The page's token is good when the local API accepts it for `/v1/me`."""

    def __init__(
        self,
        api_url: str,
        *,
        ensure_api: Callable[[], object] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._ensure_api = ensure_api
        self._transport = transport
        self._accepted: dict[str, float] = {}

    async def verify(self, token: str) -> bool:
        if not token:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        if self._accepted.get(key, 0.0) > now:
            return True
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=15.0, transport=self._transport, verify=tls()
                ) as client:
                    response = await client.get(
                        f"{self._api_url}/v1/me", headers={"Authorization": f"Bearer {token}"}
                    )
            except httpx.TransportError:
                if attempt == 0 and self._ensure_api is not None:
                    # The local API may simply not be up yet; it starts on demand.
                    await asyncio.to_thread(self._ensure_api)
                    continue
                return False
            if response.status_code == 200:
                self._accepted = {k: v for k, v in self._accepted.items() if v > now}
                self._accepted[key] = now + TOKEN_SECONDS
                return True
            return False
        return False


class PageOrigins:
    """Only the page's origin may call from a browser, and Chrome may ask first.

    A browser always says where a request comes from, so a request from any
    other site is refused outright, websockets included (they get no CORS
    protection of their own). A request with no Origin is not a browser's, and
    still needs the token. Preflights answer Chrome's private-network check.
    """

    def __init__(self, app: ASGIApp, origins: tuple[str, ...]) -> None:
        self.app = app
        self.origins = frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await PlainTextResponse("Origin not allowed", 403)(scope, receive, send)
            return
        if scope["type"] == "websocket" or origin is None:
            await self.app(scope, receive, send)
            return
        allowed = {
            "access-control-allow-origin": origin,
            "vary": "Origin",
        }
        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
            preflight = allowed | {
                "access-control-allow-methods": "GET, POST, OPTIONS",
                "access-control-allow-headers": "Authorization, Content-Type",
                "access-control-max-age": "600",
            }
            if headers.get("access-control-request-private-network") == "true":
                preflight["access-control-allow-private-network"] = "true"
            await Response(status_code=204, headers=preflight)(scope, receive, send)
            return

        async def with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    *((key.encode(), value.encode()) for key, value in allowed.items()),
                ]
            await send(message)

        await self.app(scope, receive, with_cors)


class Viewer:
    """The page on the other end of `/session`; sends never interleave."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self._lock = asyncio.Lock()

    async def send_json(self, message: dict[str, Any]) -> None:
        async with self._lock:
            await self.websocket.send_json(message)

    async def send_frame(self, header: dict[str, Any], data: bytes) -> None:
        async with self._lock:
            await self.websocket.send_bytes(pack_frame(header, data))


StreamFactory = Callable[[Viewer], Stream]


class Agent:
    def __init__(
        self,
        config: CliConfig,
        identity: DeviceIdentity,
        *,
        presence: Presence,
        verifier: TokenVerifier,
        update: Callable[[], str],
        ensure_chrome: Callable[[], object],
        stream_factory: StreamFactory | None = None,
        log_dir: Path | None = None,
        memory: MemoryWatch | None = None,
        application_status: ApplicationStatus | None = None,
        drive_directory: Path | None = None,
        drive: DriveManager | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.presence = presence
        self.verifier = verifier
        self.memory = memory
        self.ensure_chrome = ensure_chrome
        self.stream_factory = stream_factory or self._chrome_stream
        self.viewer: Viewer | None = None
        self.stream: Stream | None = None
        self.runs = RunManager(
            publish=self.publish, update=update, on_tab=self.follow_tab, log_dir=log_dir
        )
        self.drive = drive or DriveManager(
            drive_directory or drive_dir(),
            connect=lambda on_event: CdpConnection.open(config.cdp_url, on_event),
            ensure_chrome=ensure_chrome,
            busy=lambda: self.runs.busy(),
            application_status=application_status,
        )
        if self.drive.on_submit is None:
            pressing = Press(self.drive)
            self.drive.on_submit, self.drive.on_os = pressing, pressing.os
        self.drive.close_stream = self.close_stream
        self.runs.on_press = self.press_for_run

    async def press_for_run(self, message: dict[str, Any]) -> dict[str, Any]:
        """A linked `--submit` run's press goes to the drive's press, whichever it is now."""
        pressing = self.drive.on_submit
        if not isinstance(pressing, Press):
            raise RuntimeError("this agent has no press route")
        return await pressing.for_link(message)

    def _chrome_stream(self, viewer: Viewer) -> Stream:
        async def connect(on_event: Any) -> CdpConnection:
            return await CdpConnection.open(self.config.cdp_url, on_event)

        return Stream(connect=connect, send_frame=viewer.send_frame, send_json=viewer.send_json)

    def device_status(self) -> dict[str, Any]:
        checkout = self.presence.last
        return {
            "id": self.identity.id,
            "name": self.identity.name,
            "url": self.presence.identity.url,
            "commit": checkout.commit if checkout else None,
            "local_changes": checkout.local_changes if checkout else None,
            "ready": self.presence.ready,
            "problem": self.presence.problem,
            "drive_on": self.drive.switch.on(),
        }

    async def publish(self, message: dict[str, Any]) -> None:
        viewer = self.viewer
        if viewer is not None:
            with contextlib.suppress(Exception):
                await viewer.send_json(message)

    async def follow_tab(self, target: str) -> None:
        if self.stream is not None:
            await self.stream.follow(target)

    async def attach(self, viewer: Viewer, view: dict[str, Any]) -> None:
        previous, self.viewer = self.viewer, viewer
        if previous is not None:
            with contextlib.suppress(Exception):
                await previous.websocket.close(REPLACED, "Opened in another window")
        await self.close_stream()
        await viewer.send_json(
            {
                "type": "hello",
                "device": self.device_status(),
                "run": self.runs.snapshot(),
                "log": self.runs.history(),
            }
        )
        if self.drive.cdp_off():
            await viewer.send_json({"type": "stream", "state": "unavailable",
                                    "message": "A driver is using OS input on this tab"})
            return
        stream = self.stream_factory(viewer)
        # Registered before it opens, so a tab or input the page sends meanwhile reaches it.
        self.stream = stream
        try:
            await asyncio.to_thread(self.ensure_chrome)
            run = self.runs.current
            await stream.open(view, run_tab=run.tab if run is not None and run.active else None)
        except Exception as error:  # Chrome may be closing or not yet up; the run still works
            if self.stream is stream:
                self.stream = None
            with contextlib.suppress(Exception):
                await stream.close()
            if self.viewer is viewer:  # a viewer replaced meanwhile is already closed
                with contextlib.suppress(Exception):
                    await viewer.send_json({"type": "stream", "state": "unavailable",
                                            "message": str(error)[:300]})
            return
        if self.viewer is not viewer and self.stream is stream:
            await self.close_stream()

    async def restart(self) -> None:
        """Hand the page a reason, then become the new code."""
        viewer = self.viewer
        if viewer is not None:
            with contextlib.suppress(Exception):
                await viewer.websocket.close(RESTARTING, "The agent is restarting with new code")
        await self.close_stream()
        reexec()

    async def detach(self, viewer: Viewer) -> None:
        if self.viewer is viewer:
            self.viewer = None
            await self.close_stream()

    async def close_stream(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.close()

    async def on_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        stream = self.stream
        # The user's hands on the tab, or Stop, end a driver's lease before anything else.
        if kind in {"pointer", "wheel", "key", "text"}:
            await self.drive.viewer_input()
        elif kind == "control" and message.get("action") == "stop":
            await self.drive.stop()
        elif kind == "control" and message.get("action") == "done":
            self.drive.mark_submitted("Submitted on the Hunt page")
        if kind == "pointer" and stream is not None:
            await stream.pointer(message)
        elif kind == "wheel" and stream is not None:
            await stream.wheel(message)
        elif kind == "key" and stream is not None:
            await stream.key(message)
        elif kind == "text" and stream is not None:
            await stream.text(str(message.get("text") or ""))
        elif kind == "tab" and stream is not None:
            await stream.switch(str(message.get("id") or ""))
        elif kind == "view" and stream is not None:
            await stream.resize(message)
        elif kind == "control":
            await self.runs.control(str(message.get("action") or ""))
        elif kind == "letter":
            await self.runs.letter(str(message.get("text") or ""))
        elif kind == "fact":
            await self.runs.fact(message)
        elif kind == "guide":
            await self.runs.guide(str(message.get("key") or ""), str(message.get("text") or ""))


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.casefold() == "bearer" else ""


def create_app(agent: Agent, *, background: bool = True) -> Starlette:
    async def authorized(request: Request) -> bool:
        return await agent.verifier.verify(bearer(request))

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def status(request: Request) -> Response:
        if not await authorized(request):
            return JSONResponse({"detail": "Authentication required"}, 401)
        return JSONResponse({"device": agent.device_status(), "run": agent.runs.snapshot()})

    async def start_run(request: Request) -> Response:
        if not await authorized(request):
            return JSONResponse({"detail": "Authentication required"}, 401)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        reference = body.get("reference")
        reference = str(reference).strip() or None if reference is not None else None
        try:
            await agent.runs.start(reference, fresh=bool(body.get("fresh")))
        except RunBusy as error:
            return JSONResponse({"detail": str(error)}, 409)
        except ValueError as error:
            return JSONResponse({"detail": str(error)}, 422)
        return JSONResponse({"run": agent.runs.snapshot()}, 202)

    async def session(websocket: WebSocket) -> None:
        await _serve_session(agent, websocket)

    async def drive_socket(websocket: WebSocket) -> None:
        await serve_drive(agent.drive, agent.verifier.verify, websocket)

    async def drive_switch(request: Request) -> Response:
        if not await authorized(request):
            return JSONResponse({"detail": "Authentication required"}, 401)
        if request.method == "POST":
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
                return JSONResponse({"detail": 'Send {"on": true} or {"on": false}'}, 422)
            await agent.drive.set_switch(body["on"])
        return JSONResponse(agent.drive.status())

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if not background:
            yield
            return
        async with _background(agent):
            yield

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/status", status),
            Route("/runs", start_run, methods=["POST"]),
            WebSocketRoute("/session", session),
            Route("/drive", drive_switch, methods=["GET", "POST"]),
            WebSocketRoute("/drive", drive_socket),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(PageOrigins, origins=agent.identity.page_origins)
    return app


async def _serve_session(agent: Agent, websocket: WebSocket) -> None:
    """The live session: the token first, then the viewer's messages until it leaves."""
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), AUTH_SECONDS)
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        with contextlib.suppress(Exception):
            await websocket.close(UNAUTHORIZED, "Send the access token first")
        return
    token = str(first.get("token") or "") if isinstance(first, dict) else ""
    if first.get("type") != "auth" or not await agent.verifier.verify(token):
        await websocket.close(UNAUTHORIZED, "The access token was not accepted")
        return
    viewer = Viewer(websocket)
    await agent.attach(viewer, dict(first.get("view") or {}))
    try:
        while True:
            message = await websocket.receive_json()
            if isinstance(message, dict) and agent.viewer is viewer:
                with contextlib.suppress(Exception):
                    await agent.on_message(message)
    except (WebSocketDisconnect, ValueError, KeyError, RuntimeError):
        pass
    finally:
        await agent.detach(viewer)


@contextlib.asynccontextmanager
async def _background(agent: Agent) -> AsyncIterator[None]:
    """The run link's socket and the heartbeat, restart and memory tasks, while the app runs."""
    path = socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(agent.runs.handle_link, str(path))
    path.chmod(0o600)
    package = Path(__file__).resolve().parents[1]
    tasks = [
        asyncio.create_task(heartbeats(agent)),
        asyncio.create_task(
            restart_when_changed(
                agent, lambda: source_fingerprint(package), restart=agent.restart
            )
        ),
    ]
    if agent.memory is not None:
        tasks.append(
            asyncio.create_task(watch_memory(agent, agent.memory, restart=agent.restart))
        )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        server.close()
        await agent.drive.end("the agent stopped")
        await agent.close_stream()
        path.unlink(missing_ok=True)


async def heartbeats(agent: Agent) -> None:
    """Beat every 30 seconds; a failed beat is logged and the next one tries again."""
    while True:
        try:
            await asyncio.to_thread(agent.presence.beat, agent.runs.active_run_id())
        except Exception as error:
            print(f"heartbeat failed: {error}", flush=True)
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def watch_memory(
    agent: Agent,
    watch: MemoryWatch,
    *,
    restart: Callable[[], Awaitable[None]],
    every: float = HEARTBEAT_SECONDS,
) -> None:
    """Sample memory every beat; a run's process past the limit is stopped and the run told."""
    while True:
        try:
            over = await asyncio.to_thread(watch.check, agent.runs.active_pid())
        except Exception as error:
            print(f"memory watch failed: {error}", flush=True)
            over = None
        if over is not None and over.pid == watch.agent_pid:
            if not agent.runs.busy():
                await restart()
                return
        elif over is not None:
            await agent.runs.note(
                f"Stopped {over.name} ({over.pid}): it held {gigabytes(over.rss)}, past the "
                f"{gigabytes(watch.limit)} limit. memory.log has the samples before it."
            )
        await asyncio.sleep(every)


async def restart_when_changed(
    agent: Agent,
    fingerprint: Callable[[], str],
    *,
    restart: Callable[[], Awaitable[None]],
    every: float = 30.0,
) -> None:
    """Re-exec into new code once the source changes, but only between runs."""
    started = fingerprint()
    while True:
        await asyncio.sleep(every)
        if not agent.runs.busy() and fingerprint() != started:
            await restart()
            return


def reexec() -> None:
    os.execv(sys.executable, [sys.executable, "-m", "personal_os_worker", "device", "serve"])
