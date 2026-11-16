"""`WS /drive`: one leased tab, an allowlist of CDP methods, and a stop the device enforces.

A driver of the user's own on the tailnet (Grok on the box) fills an
application through this device's real Chrome. Admission needs the token as a
bearer header only, refuses any request with an Origin, so no web page can open
it, and needs the drive switch on.

The first message leases one tab for one listing. The agent refuses it when the
switch is off, another lease or a page run is active, the URL is not on an ATS
host, the listing is locked, or the API says it was already applied to. It
attaches to that one tab with `Target.attachToTarget {flatten: true}` and never
`setAutoAttach`, so out-of-process frames (hCaptcha) are never attached, and on
its own browser connection, never the live view's or a run's.

Commands are forwarded to that tab only when `ALLOWED` names them and their
checks pass; everything else is refused and every message is audited. The lease
ends, and the socket closes, on Stop or any live-view input from the Hunt page,
the switch turning off, ten idle or 45 total minutes, the tab closing or leaving
the lease host, or the driver releasing it. `upload_file` writes a driver's
file into `files/`, the only place `DOM.setFileInputFiles` takes files from;
`submit` hands the lease to the press (`press.py`), after which nothing more
is forwarded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from personal_os_worker.device.cdp import Cdp, CdpError, EventHandler
from personal_os_worker.device.drive_state import (
    APPLIED_OR_LATER,
    AuditLog,
    DriveFiles,
    DriveSwitch,
    LockStore,
    Scorecard,
    is_ats_host,
    is_confirmation,
    normalize_posting,
    now_iso,
    summarize,
    url_host,
)

IDLE_SECONDS = 600.0
TOTAL_SECONDS = 2700.0
LEASE_SECONDS = 10.0
UNAUTHORIZED, REFUSED = 4401, 4403
# Forwarded to the leased tab's session, and nothing else ever is.
ALLOWED = frozenset({
    "Page.enable", "Page.navigate", "Page.reload", "Page.getNavigationHistory",
    "Page.captureScreenshot", "Page.getLayoutMetrics", "Page.setInterceptFileChooserDialog",
    "Page.createIsolatedWorld",
    "Input.dispatchMouseEvent", "Input.dispatchKeyEvent", "Input.insertText",
    "DOM.getDocument", "DOM.querySelector", "DOM.querySelectorAll", "DOM.getBoxModel",
    "DOM.getContentQuads", "DOM.describeNode", "DOM.getAttributes", "DOM.getOuterHTML",
    "DOM.setFileInputFiles",
    "Accessibility.getFullAXTree", "Accessibility.queryAXTree",
    "Runtime.evaluate",
})
# The leased tab's events the driver sees; Page.frameNavigated only for the main frame.
FORWARDED_EVENTS = frozenset({
    "Page.loadEventFired", "Page.frameNavigated", "Page.fileChooserOpened",
    "Page.navigatedWithinDocument",
})

Connect = Callable[[EventHandler], Awaitable[Cdp]]
ApplicationStatus = Callable[[str], "str | None"]


class DriveRefused(RuntimeError):
    """A lease the agent will not grant; the message is the reason."""


def chrome_file_path(path: Path) -> str:
    """The path Windows Chrome opens for a file in this distro, or the path as it is."""
    distro = os.environ.get("WSL_DISTRO_NAME")
    if not distro:
        return str(path)
    return str(PureWindowsPath(f"\\\\wsl.localhost\\{distro}", *path.parts[1:]))


@dataclass
class Lease:
    listing_id: str
    url: str
    host: str
    key: str
    started: float
    last: float
    since: str = field(default_factory=now_iso)
    cdp: Cdp | None = None
    target: str = ""
    session: str = ""
    tab_url: str = ""
    worlds: set[int] = field(default_factory=set)
    chooser_node: int | None = None
    pressed: bool = False
    reason: str | None = None
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    outbox: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)

    @property
    def active(self) -> bool:
        return self.reason is None

    def public(self) -> dict[str, Any]:
        return {"listing_id": self.listing_id, "url": self.url, "since": self.since}


class DriveManager:
    """The one lease this device may have, its switch, locks and audit."""

    def __init__(
        self,
        directory: Path,
        *,
        connect: Connect,
        ensure_chrome: Callable[[], object],
        busy: Callable[[], bool],
        application_status: ApplicationStatus | None,
        clock: Callable[[], float] = time.monotonic,
        chrome_path: Callable[[Path], str] = chrome_file_path,
    ) -> None:
        self.switch = DriveSwitch(directory)
        self.locks = LockStore(directory)
        self.audit = AuditLog(directory)
        self.uploads = DriveFiles(directory)
        self.files = self.uploads.root
        self.scorecard = Scorecard(directory)
        self.connect = connect
        self.ensure_chrome = ensure_chrome
        self.busy = busy
        self.application_status = application_status
        self.clock = clock
        self.chrome_path = chrome_path
        self.lease: Lease | None = None
        # Where the press plugs in (phase 1b): it gets the lease and the driver's message.
        self.on_submit: Callable[[Lease, dict[str, Any]], Awaitable[None]] | None = None
        self._granting = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def status(self) -> dict[str, Any]:
        lease = self.lease
        return {"on": self.switch.on(),
                "lease": lease.public() if lease is not None and lease.active else None,
                "attempts": self.scorecard.recent()}

    async def set_switch(self, on: bool) -> None:
        self.switch.set(on)
        if not on:
            await self.end("the drive switch was turned off")

    # ----- granting and ending a lease ---------------------------------------

    async def _refusal(self, listing_id: str, url: str) -> str | None:
        host = url_host(url)
        if not self.switch.on():
            return "the drive switch is off"
        if self.lease is not None:
            return "another lease is active"
        if self.busy():
            return "a page run is active"
        if not is_ats_host(host):
            return f"not an ATS host: {host or url[:200]}"
        lock = self.locks.state_for(listing_id, url)
        if lock is not None:
            return f"the listing is locked ({lock})"
        if self.application_status is None:
            return "the application status cannot be checked"
        try:
            status = await asyncio.to_thread(self.application_status, listing_id)
        except Exception as error:
            return f"the application status could not be read: {str(error)[:200]}"
        if status in APPLIED_OR_LATER:
            return f"the application is already {status}"
        return None

    async def begin(self, listing_id: str, url: str) -> Lease:
        async with self._granting:
            reason = await self._refusal(listing_id, url)
            if reason is not None:
                raise DriveRefused(reason)
            now = self.clock()
            lease = Lease(listing_id=listing_id, url=url, host=url_host(url),
                          key=normalize_posting(url), started=now, last=now)
            self.lease = lease
        try:
            await asyncio.to_thread(self.ensure_chrome)
            lease.cdp = await self.connect(self.on_event)
            await self._open_tab(lease, lease.cdp)
        except Exception as error:
            await self.end("the tab could not be opened", lease)
            raise DriveRefused(f"the tab could not be opened: {str(error)[:200]}") from None
        return lease

    async def _open_tab(self, lease: Lease, cdp: Cdp) -> None:
        await cdp.send("Target.setDiscoverTargets", {"discover": True})
        targets = (await cdp.send("Target.getTargets")).get("targetInfos") or []
        found = next(
            (info for info in targets if info.get("type") == "page"
             and self._on_posting(lease, str(info.get("url") or ""))),
            None,
        )
        if found is not None:
            lease.target, lease.tab_url = str(found["targetId"]), str(found.get("url") or "")
        else:
            created = await cdp.send("Target.createTarget", {"url": lease.url})
            lease.target, lease.tab_url = str(created["targetId"]), lease.url
        attached = await cdp.send(
            "Target.attachToTarget", {"targetId": lease.target, "flatten": True}
        )
        lease.session = str(attached["sessionId"])
        await cdp.send("Page.enable", {}, lease.session)

    @staticmethod
    def _on_posting(lease: Lease, url: str) -> bool:
        key = normalize_posting(url)
        return bool(key) and (key == lease.key or key.startswith(lease.key + "/"))

    def _finish(self, reason: str, lease: Lease | None = None) -> asyncio.Task[None] | None:
        """End the lease now: nothing more is forwarded; the teardown runs after."""
        lease = lease or self.lease
        if lease is None or not lease.active:
            return None
        lease.reason = reason
        lease.ended.set()
        lease.outbox.put_nowait({"type": "ended", "reason": reason})
        lease.outbox.put_nowait(None)
        if self.lease is lease:
            self.lease = None
        self.audit.write(lease.listing_id, "ended", {}, reason)
        # A press owns its connection until it has read the outcome, then tears down itself.
        return None if lease.pressed else self.spawn(self.teardown(lease))

    def spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Run `work` beside the relay, held until it finishes."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def teardown(self, lease: Lease) -> None:
        cdp = lease.cdp
        if cdp is None:
            return
        if lease.session:
            with contextlib.suppress(Exception):
                await cdp.send("Target.detachFromTarget", {"sessionId": lease.session})
        with contextlib.suppress(Exception):
            await cdp.close()

    async def end(self, reason: str, lease: Lease | None = None) -> None:
        task = self._finish(reason, lease)
        if task is not None:
            await task

    def remaining(self, lease: Lease) -> float:
        return min(lease.last + IDLE_SECONDS, lease.started + TOTAL_SECONDS) - self.clock()

    def expiry(self, lease: Lease) -> str | None:
        now = self.clock()
        if now >= lease.started + TOTAL_SECONDS:
            return "the lease reached 45 minutes"
        if now >= lease.last + IDLE_SECONDS:
            return "the lease was idle for 10 minutes"
        return None

    # ----- what the Hunt page and Chrome tell the drive -----------------------

    async def viewer_input(self) -> None:
        await self.end("the live view was used")

    async def stop(self) -> None:
        await self.end("stopped from the Hunt page")

    def mark_submitted(self, note: str) -> None:
        """The Hunt page's Submitted during a lease locks its listing."""
        lease = self.lease
        if lease is not None and lease.active:
            self.locks.lock(lease.listing_id, lease.url, "submitted", note)

    def on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        lease = self.lease
        if lease is None or not lease.active:
            return
        if method == "Stream.closed":
            self._finish("Chrome closed the drive connection", lease)
        elif session_id is None:
            self._browser_event(lease, method, params)
        elif session_id == lease.session and method in FORWARDED_EVENTS:
            self._tab_event(lease, method, params)

    def _browser_event(self, lease: Lease, method: str, params: dict[str, Any]) -> None:
        if method == "Target.targetInfoChanged":
            info = params.get("targetInfo") or {}
            url = str(info.get("url") or "")
            if is_confirmation(url):
                self._confirmed(lease, url)
            if info.get("targetId") == lease.target:
                self._tab_moved(lease, url)
        elif method == "Target.targetDestroyed" and params.get("targetId") == lease.target:
            self._finish("the leased tab closed", lease)
        elif method == "Target.detachedFromTarget" and params.get("sessionId") == lease.session:
            self._finish("the leased tab was detached", lease)

    def _tab_event(self, lease: Lease, method: str, params: dict[str, Any]) -> None:
        if method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            if frame.get("parentId"):
                return
            self._tab_moved(lease, str(frame.get("url") or ""))
        elif method == "Page.navigatedWithinDocument" and params.get("frameId") == lease.target:
            self._tab_moved(lease, str(params.get("url") or ""))
        elif method == "Page.fileChooserOpened":
            node = params.get("backendNodeId")
            lease.chooser_node = node if isinstance(node, int) else None
        if lease.active:
            lease.outbox.put_nowait({"type": "event", "method": method, "params": params})

    def _tab_moved(self, lease: Lease, url: str) -> None:
        if not url:
            return
        lease.tab_url = url
        if is_confirmation(url):
            self._confirmed(lease, url)
        if url_host(url) != lease.host:
            self._finish("the leased tab left the lease host", lease)

    def _confirmed(self, lease: Lease, url: str) -> None:
        listing = lease.listing_id if self._on_posting(lease, url) else self.locks.listing_for(url)
        if listing is not None:
            self.locks.lock(listing, url, "submitted", "confirmation page seen")

    # ----- the driver's messages ----------------------------------------------

    async def handle(self, lease: Lease, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if not self.switch.on():
            await self.end("the drive switch is off", lease)
        elif kind == "release":
            self.audit.write(lease.listing_id, "release", {}, "forwarded")
            await self.end("released by the driver", lease)
        elif lease.pressed:
            self.audit.write(lease.listing_id, str(kind or message.get("method")), {},
                             "refused: the press has begun")
            lease.outbox.put_nowait({"id": message.get("id"), "type": "error",
                                     "error": "the press has begun; commands are closed"})
        elif kind == "submit":
            await self.submit(lease, message)
        elif kind == "upload_file":
            lease.outbox.put_nowait(self.upload(lease, message))
        elif "method" in message:
            lease.outbox.put_nowait(await self.command(lease, message))
        else:
            self.audit.write(lease.listing_id, str(kind), {}, "refused: unknown message")
            lease.outbox.put_nowait({"type": "error", "error": "unknown message"})

    async def submit(self, lease: Lease, message: dict[str, Any]) -> None:
        """The press is its own message; the route that makes it plugs in at `on_submit`."""
        if self.on_submit is None:
            self.audit.write(lease.listing_id, "submit", {}, "refused: no press route yet")
            lease.outbox.put_nowait({"type": "error", "error": "submit is not available yet"})
            return
        self.audit.write(lease.listing_id, "submit",
                         {"experiment": message.get("experiment")}, "forwarded")
        await self.on_submit(lease, message)

    def upload(self, lease: Lease, message: dict[str, Any]) -> dict[str, Any]:
        """Write a driver's file into `files/`; its path is the only kind Chrome is handed."""
        name, data = message.get("name"), message.get("data_b64")
        summary = {"name": str(name)[:200], "data_length": len(str(data or ""))}
        try:
            if not isinstance(name, str) or not isinstance(data, str):
                raise ValueError("send a name and data_b64")
            path = self.uploads.write(name, data)
        except (ValueError, OSError) as error:
            self.audit.write(lease.listing_id, "upload_file", summary, f"refused: {error}")
            return {"type": "error", "error": str(error)}
        self.audit.write(lease.listing_id, "upload_file", summary, "written")
        return {"type": "uploaded", "path": self.chrome_path(path.resolve())}

    async def command(self, lease: Lease, message: dict[str, Any]) -> dict[str, Any]:
        number = message.get("id")
        method = str(message.get("method") or "")
        raw = message.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        lease.last = self.clock()
        summary = summarize(method, params)
        refusal, params = self._check(lease, method, params)
        if refusal is not None or lease.cdp is None:
            refusal = refusal or "the lease has no tab"
            self.audit.write(lease.listing_id, method, summary, f"refused: {refusal}")
            return {"id": number, "error": refusal}
        try:
            result = await lease.cdp.send(method, params, lease.session)
        except CdpError as error:
            self.audit.write(lease.listing_id, method, summary, f"forwarded: error {error}")
            return {"id": number, "error": str(error)}
        if method == "Page.createIsolatedWorld":
            context = result.get("executionContextId")
            if isinstance(context, int):
                lease.worlds.add(context)
        self.audit.write(lease.listing_id, method, summary, "forwarded")
        return {"id": number, "result": result}

    def _locked(self, lease: Lease, url: str) -> str | None:
        lock = self.locks.state_for(listing_id=lease.listing_id)
        return lock or (self.locks.state_for(url=url) if url else None)

    def _check(
        self, lease: Lease, method: str, params: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        """Why this command is refused, or None and the parameters to forward."""
        if not lease.active:
            return "the lease has ended", params
        if lease.pressed:
            return "the press has begun; commands are closed", params
        if method not in ALLOWED:
            return f"not allowed: {method}", params
        if method == "Page.navigate":
            url = str(params.get("url") or "")
            if url_host(url) != lease.host:
                return "navigate stays on the lease host", params
            lock = self._locked(lease, url) or self._locked(lease, lease.tab_url)
            return (f"the listing is locked ({lock})" if lock else None), params
        if method == "Page.reload" or method.startswith("Input."):
            lock = self._locked(lease, lease.tab_url)
            return (f"the listing is locked ({lock})" if lock else None), params
        if method == "Runtime.evaluate":
            if params.get("contextId") not in lease.worlds:
                return "Runtime.evaluate runs only in the lease's isolated world", params
            return None, {**params, "returnByValue": True}
        if method == "DOM.setFileInputFiles":
            return self._files(lease, params)
        return None, params

    def _files(
        self, lease: Lease, params: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        node = params.get("backendNodeId")
        if lease.chooser_node is None or node != lease.chooser_node or (
            "nodeId" in params or "objectId" in params
        ):
            return "files go only to the node of the last file chooser", params
        root = self.files.resolve()
        given = {self.chrome_path(path): path
                 for path in (root.iterdir() if root.is_dir() else ()) if path.is_file()}
        paths: list[str] = []
        for name in params.get("files") or []:
            path = given.get(str(name)) or (root / str(name)).resolve()
            if path.parent != root or not path.is_file():
                return "files come only from the agent's drive files", params
            paths.append(self.chrome_path(path))
        if not paths:
            return "no files named", params
        return None, {"backendNodeId": node, "files": paths}


async def serve_drive(
    drive: DriveManager, verify: Callable[[str], Awaitable[bool]], websocket: WebSocket
) -> None:
    """Admit one driver, grant its lease, and relay until the lease ends."""
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    if (
        "origin" in websocket.headers
        or scheme.casefold() != "bearer"
        or not await verify(token.strip())
    ):
        await websocket.close(UNAUTHORIZED, "A bearer token and no Origin are required")
        return
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), LEASE_SECONDS)
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        await _refuse(drive, websocket, None, "send a lease first")
        return
    first = first if isinstance(first, dict) else {}
    listing_id, url = first.get("listing_id"), first.get("url")
    if first.get("type") != "lease" or not isinstance(listing_id, str) or not listing_id \
            or not isinstance(url, str):
        await _refuse(drive, websocket, None, "the first message is a lease")
        return
    try:
        lease = await drive.begin(listing_id, url)
    except DriveRefused as error:
        await _refuse(drive, websocket, listing_id, str(error))
        return
    drive.audit.write(listing_id, "lease", {"url": url}, "granted")
    lease.outbox.put_nowait({"type": "leased", "target": lease.target})
    await _relay(drive, lease, websocket)


async def _refuse(
    drive: DriveManager, websocket: WebSocket, listing_id: str | None, reason: str
) -> None:
    drive.audit.write(listing_id, "lease", {}, f"refused: {reason}")
    with contextlib.suppress(Exception):
        await websocket.send_json({"type": "refused", "reason": reason})
        await websocket.close(REFUSED, reason[:120])


async def _relay(drive: DriveManager, lease: Lease, websocket: WebSocket) -> None:
    async def send() -> None:
        while (message := await lease.outbox.get()) is not None:
            await websocket.send_text(json.dumps(message, default=str))

    sender = asyncio.create_task(send())
    receive: asyncio.Task[Any] | None = None
    ended = asyncio.create_task(lease.ended.wait())
    try:
        while lease.active:
            receive = receive or asyncio.create_task(websocket.receive_json())
            done, _ = await asyncio.wait(
                {receive, ended}, timeout=max(drive.remaining(lease), 0.0),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not lease.active:
                break
            if receive not in done:
                if (reason := drive.expiry(lease)) is not None:
                    await drive.end(reason, lease)
                continue
            try:
                message = receive.result()
            except (WebSocketDisconnect, ValueError, KeyError, RuntimeError):
                await drive.end("the driver disconnected", lease)
                break
            receive = None
            if (reason := drive.expiry(lease)) is not None:
                await drive.end(reason, lease)
            elif isinstance(message, dict):
                await drive.handle(lease, message)
    finally:
        await drive.end("the driver disconnected", lease)
        for task in (receive, ended):
            if task is not None:
                task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sender, 5.0)
        sender.cancel()
        with contextlib.suppress(Exception):
            await websocket.close()


def api_application_status(make_api: Callable[[], Any]) -> ApplicationStatus:
    """The listing's application status from the API: the furthest along, or None."""

    def status(listing_id: str) -> str | None:
        api = make_api()
        try:
            rows = [row for row in api.applications()
                    if str(row.get("posting_id")) == listing_id]
        finally:
            api.close()
        statuses = [str(row.get("status")) for row in rows]
        return next((item for item in statuses if item in APPLIED_OR_LATER),
                    statuses[0] if statuses else None)

    return status
