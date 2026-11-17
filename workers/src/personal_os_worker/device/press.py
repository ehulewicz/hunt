"""The press: a driver's `submit`, made by one experiment's route, and the outcome it reads.

`{"type": "submit", "experiment": k, "selector": css, "knobs": {}}` names the
control. The agent finds it in the leased tab through DOM, checks its accessible
name (or its text) matches `SUBMIT_CONTROL`, and records its box; a selector
that matches nothing, names another control, or lies outside the viewport is
refused and the driver keeps the lease. Otherwise the listing's lock becomes
`pending` and the driver's commands are closed for good: the socket stays open
only for the outcome. The route then presses, and the agent watches the page
for up to 30 seconds through a fresh isolated world: the URL from the target's
info, the body's text, challenge frames and field errors, read by
`classify_outcome`. `OUTCOME_LOCKS` sets the lock, one scorecard line is
written, the driver gets `{"type": "outcome", "outcome", "url", "screenshot"}`
(base64 JPEG) and the lease ends. A Stop before the click releases the lock and
nothing is pressed; after it, the outcome is still read and recorded.

Routes by experiment, in `ROUTES` order:

- 1, `cdp_click`: `DOM.disable` and `Accessibility.disable`, a 3-8 s wait, then
  one human-shaped click through `Input.dispatchMouseEvent` on the page
  session, with the debugger attached.
- 2, `os_click`; 3, `os_click_slow` (the slower fill is the driver's); 4,
  `os_fill` (filled with `os` messages, `drive_os.py`): the live view closed,
  the viewport origin and the control's box measured, every CDP session on the
  tab detached and the drive's browser socket closed, a 3-8 s wait, the Chrome
  window focused by its tab's title, then one human-shaped OS click through
  `OsInput`. Only after it does a new session attach to watch the outcome.

Any other experiment (5 and 6 plug into `routes`) is refused as not implemented.

A linked `os hunt apply --submit` run presses the same way with no `/drive`
socket (`for_link`): `press_ready` asks whether the agent would press (the
switch on, no lease, a `DEVICE_PRESS_HOSTS` posting, the listing unlocked);
`press` names the run's tab and selector after the run has let go of Chrome.
The agent leases that tab itself, on a fresh session, runs experiment 1 exactly
as for a driver, and answers `{"type": "press_outcome", "outcome", "url",
"error"}`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from personal_os_worker.core.page_scripts import load
from personal_os_worker.core.wording import SUBMIT_CONTROL
from personal_os_worker.device.cdp import CdpError
from personal_os_worker.device.drive import DriveManager, Lease
from personal_os_worker.device.drive_os import OsDrive
from personal_os_worker.device.drive_state import (
    DEVICE_PRESS_HOSTS,
    OUTCOME_LOCKS,
    classify_outcome,
    is_challenge_frame,
    normalize_posting,
    url_host,
)
from personal_os_worker.device.motion import Box, Motion, Point
from personal_os_worker.device.osinput import OsInput, move_op, start_point, wait_op

WAIT_SECONDS = (3.0, 8.0)
WATCH_POLLS = 30
POLL_SECONDS = 1.0
# Outcomes that end the watch at once; validation must read the same twice.
DECISIVE = frozenset({"submitted", "captcha_rejected", "challenge_shown"})
TAGS = re.compile(r"<[^>]+>")

Sleep = Callable[[float], Awaitable[None]]


class PressRefused(RuntimeError):
    """A submit the agent will not press; the driver keeps its lease."""


@dataclass
class Control:
    """The control a submit names: its node, box and the viewport it sits in."""

    node: int
    box: Box
    viewport: tuple[float, float]


@dataclass
class Attempt:
    lease: Lease
    experiment: int
    control: Control
    knobs: dict[str, Any]
    before_url: str
    input_kind: str = "cdp"
    debugger_attached: bool = True
    clicked: bool = False
    agent_knobs: dict[str, Any] = field(default_factory=dict)


Route = Callable[[Attempt], Awaitable[None]]


class Press:
    """`DriveManager.on_submit`: checks the submit, locks the listing, then runs its route."""

    def __init__(
        self, drive: DriveManager, *, motion: Motion | None = None, sleep: Sleep = asyncio.sleep,
        os_input: OsInput | None = None,
    ) -> None:
        self.drive = drive
        self.motion = motion or Motion()
        self.sleep = sleep
        self.os = OsDrive(drive, os_input or OsInput(motion=self.motion))
        # Experiments 5 and 6 plug in here when they exist; until then they are refused.
        self.routes: dict[int, Route] = {
            1: self.cdp_click, 2: self.os_click, 3: self.os_click, 4: self.os_click,
        }

    async def __call__(self, lease: Lease, message: dict[str, Any]) -> None:
        experiment = message.get("experiment")
        selector = message.get("selector")
        knobs = message.get("knobs")
        try:
            route = self.routes.get(experiment) if type(experiment) is int else None
            if route is None:
                raise PressRefused("not implemented yet")
            if not isinstance(selector, str) or not selector.strip():
                raise PressRefused("send the submit control's selector")
            lock = self.drive.locks.state_for(lease.listing_id, lease.url) or (
                self.drive.locks.state_for(url=lease.tab_url))
            if lock is not None:
                raise PressRefused(f"the listing is locked ({lock})")
            control = await self.resolve(lease, selector)
        except PressRefused as error:
            self.drive.audit.write(lease.listing_id, "press", {}, f"refused: {error}")
            lease.outbox.put_nowait({"type": "error", "error": str(error)})
            return
        assert isinstance(experiment, int)
        self.drive.locks.lock(lease.listing_id, lease.tab_url or lease.url, "pending",
                              f"experiment {experiment}")
        lease.pressed = True
        self.drive.audit.write(lease.listing_id, "press", {"experiment": experiment},
                               "pending: commands closed")
        attempt = Attempt(lease, experiment, control,
                          knobs if isinstance(knobs, dict) else {}, lease.tab_url or lease.url)
        self.drive.spawn(self.run(attempt, route))

    # ----- a linked apply run's press -----------------------------------------

    def run_refusal(self, listing_id: str, url: str) -> str | None:
        """Why a linked run's press is refused before anything attaches, or None."""
        host = url_host(url)
        if not self.drive.switch.on():
            return "the drive switch is off"
        if self.drive.lease is not None:
            return "another lease is active"
        if host not in DEVICE_PRESS_HOSTS:
            return f"not a device press host: {host or url[:200]}"
        lock = self.drive.locks.state_for(listing_id, url)
        return f"the listing is locked ({lock})" if lock is not None else None

    async def for_link(self, message: dict[str, Any]) -> dict[str, Any]:
        """A linked run's `press_ready` or `press`, answered for the link."""
        listing_id, url = str(message.get("listing_id") or ""), str(message.get("url") or "")
        refusal = self.run_refusal(listing_id, url) if listing_id else "send the listing"
        if message.get("type") == "press_ready":
            # A lock stops any press, Playwright's too, whatever the switch says.
            locked = self.drive.locks.state_for(listing_id, url) if listing_id else None
            return {"type": "press_ready", "ok": refusal is None, "reason": refusal,
                    "locked": locked}
        target, selector = str(message.get("target") or ""), message.get("selector")
        if refusal is None and message.get("experiment") != 1:
            refusal = "a run presses by experiment 1 only"
        if refusal is None and not target:
            refusal = "send the run's tab"
        if refusal is not None:
            self.drive.audit.write(listing_id or None, "press", {"run": True},
                                   f"refused: {refusal}")
            return {"type": "press_outcome", "outcome": None, "url": url, "error": refusal}
        now = self.drive.clock()
        fill = message.get("fill_seconds")
        lease = Lease(listing_id=listing_id, url=url, host=url_host(url),
                      key=normalize_posting(url), last=now, target=target, tab_url=url,
                      started=now - (float(fill) if isinstance(fill, int | float) else 0.0))
        self.drive.lease = lease
        try:
            lease.cdp = await self.drive.connect(self.drive.on_event)
            await lease.cdp.send("Target.setDiscoverTargets", {"discover": True})
            attached = await lease.cdp.send("Target.attachToTarget",
                                            {"targetId": target, "flatten": True})
            lease.session = str(attached["sessionId"])
        except Exception as error:
            await self.drive.end("the run's tab could not be attached", lease)
            return {"type": "press_outcome", "outcome": None, "url": url,
                    "error": f"the run's tab could not be attached: {str(error)[:200]}"}
        await self(lease, {"experiment": 1, "selector": selector,
                           "knobs": {"via": "os hunt apply --submit"}})
        if not lease.pressed:
            await self.drive.end("the press was refused", lease)
        return await self._link_outcome(lease)

    @staticmethod
    async def _link_outcome(lease: Lease) -> dict[str, Any]:
        """The press's answer, read from the lease's outbox until the lease ends."""
        reply: dict[str, Any] = {"type": "press_outcome", "outcome": None, "url": lease.url,
                                 "error": "the press ended with no outcome"}
        while (message := await lease.outbox.get()) is not None:
            if message.get("type") == "outcome":
                reply.update(outcome=message.get("outcome"), url=message.get("url"), error=None)
            elif message.get("type") == "error" and reply["outcome"] is None:
                reply["error"] = str(message.get("error") or reply["error"])
        return reply

    async def resolve(self, lease: Lease, selector: str) -> Control:
        """The node `selector` names, if it is a submit control in view."""
        cdp, session = lease.cdp, lease.session
        if cdp is None:
            raise PressRefused("the lease has no tab")
        try:
            root = (await cdp.send("DOM.getDocument", {"depth": 0}, session))["root"]["nodeId"]
            found = await cdp.send("DOM.querySelector",
                                   {"nodeId": root, "selector": selector}, session)
            node = found.get("nodeId")
            if not isinstance(node, int) or not node:
                raise PressRefused("the selector matches nothing")
            if not await self._is_submit(lease, node):
                raise PressRefused("the selector is not a submit control")
            model = (await cdp.send("DOM.getBoxModel", {"nodeId": node}, session))["model"]
            metrics = await cdp.send("Page.getLayoutMetrics", {}, session)
        except (CdpError, KeyError, TypeError) as error:
            raise PressRefused(f"the submit control could not be read: {error}") from None
        box = Box.from_quad([float(value) for value in model["border"]])
        layout = metrics.get("cssLayoutViewport") or {}
        width = float(layout.get("clientWidth") or 0)
        height = float(layout.get("clientHeight") or 0)
        if box.width < 1 or box.height < 1 or box.x < 0 or box.y < 0 \
                or box.x + box.width > width or box.y + box.height > height:
            raise PressRefused("the submit control is not in view")
        return Control(node, box, (width, height))

    async def _is_submit(self, lease: Lease, node: int) -> bool:
        assert lease.cdp is not None
        tree = await lease.cdp.send("Accessibility.getPartialAXTree",
                                    {"nodeId": node, "fetchRelatives": False}, lease.session)
        nodes = tree.get("nodes") or [{}]
        name = str(((nodes[0] or {}).get("name") or {}).get("value") or "")
        if SUBMIT_CONTROL.search(name):
            return True
        html = await lease.cdp.send("DOM.getOuterHTML", {"nodeId": node}, lease.session)
        text = " ".join(TAGS.sub(" ", str(html.get("outerHTML") or "")).split())
        return bool(SUBMIT_CONTROL.search(text))

    async def run(self, attempt: Attempt, route: Route) -> None:
        """Press, read and record; the press's connection is torn down only after."""
        lease = attempt.lease
        try:
            try:
                await route(attempt)
            except Exception as error:
                if not attempt.clicked:
                    self._abandon(attempt, f"the press failed before the click: {error}")
                    return
            if not attempt.clicked:
                self._abandon(attempt, lease.reason or "the lease ended before the click")
                return
            outcome, url = await self.watch(attempt)
            await self.record(attempt, outcome, url)
        finally:
            await self.drive.end("the press finished", lease)
            await self.drive.teardown(lease)

    def _abandon(self, attempt: Attempt, reason: str) -> None:
        """Nothing was pressed: the pending lock goes and the driver hears why."""
        lease = attempt.lease
        self.drive.locks.release(lease.listing_id)
        self.drive.audit.write(lease.listing_id, "press", {}, f"abandoned: {reason}")
        lease.outbox.put_nowait({"type": "error", "error": f"nothing was pressed: {reason}"})

    async def cdp_click(self, attempt: Attempt) -> None:
        """Experiment 1: the agent's DOM and AX state off, a wait, one CDP click."""
        lease = attempt.lease
        assert lease.cdp is not None
        for method in ("DOM.disable", "Accessibility.disable"):
            with contextlib.suppress(CdpError):
                await lease.cdp.send(method, {}, lease.session)
        wait = self.motion.rng.uniform(*WAIT_SECONDS)
        attempt.agent_knobs["wait_s"] = round(wait, 1)
        await self.sleep(wait)
        await self.click(attempt)

    async def os_click(self, attempt: Attempt) -> None:
        """Experiments 2-4: measured, every CDP session gone, a wait, one OS click."""
        lease = attempt.lease
        origin = await self.os.prepare(lease)
        box = await self.box(lease, attempt.control.node)
        if not self.os.in_view(origin, box):
            raise PressRefused("the submit control is not in view")
        await self.os.detach(lease)
        attempt.debugger_attached, attempt.input_kind = False, "os"
        wait = self.motion.rng.uniform(*WAIT_SECONDS)
        attempt.agent_knobs["wait_s"] = round(wait, 1)
        await self.sleep(wait)
        if not lease.active:
            return
        ran = await self.os.focus(lease)
        x, y = origin.to_screen(self.motion.target(box))
        if not ran.inside(x, y):
            raise PressRefused(f"the point {x},{y} is outside the Chrome window {ran.rect}")
        end = Point(float(x), float(y))
        await self.os.run(lease, [move_op(self.motion, start_point(self.motion, ran, end), end),
                                  wait_op(self.motion.dwell_ms())])
        if not lease.active:
            return
        # From here the button may be down, so the outcome is read whatever happens.
        attempt.clicked = True
        try:
            await self.os.run(lease, [{"op": "down"}, wait_op(self.motion.press_ms()),
                                      {"op": "up"}])
        finally:
            with contextlib.suppress(Exception):
                await self.os.attach(lease)

    async def box(self, lease: Lease, node: int) -> Box:
        """The control's box again, now the live view's override is cleared."""
        if lease.cdp is None:
            raise PressRefused("the lease has no tab")
        model = (await lease.cdp.send("DOM.getBoxModel", {"nodeId": node}, lease.session))
        return Box.from_quad([float(value) for value in model["model"]["border"]])

    async def click(self, attempt: Attempt) -> None:
        """One human-shaped left click on the control, abandoned if the lease ends first."""
        lease, control = attempt.lease, attempt.control
        assert lease.cdp is not None
        target = self.motion.target(control.box)
        width, height = control.viewport
        start = Point(
            min(max(target.x + self.motion.rng.uniform(-260, 260), 1.0), width - 1),
            min(max(target.y + self.motion.rng.uniform(80, 240), 1.0), height - 1),
        )

        async def mouse(kind: str, point: Point, **extra: Any) -> None:
            assert lease.cdp is not None
            await lease.cdp.send("Input.dispatchMouseEvent", {
                "type": kind, "x": round(point.x, 1), "y": round(point.y, 1), **extra,
            }, lease.session)

        await mouse("mouseMoved", start)
        for step in self.motion.path(start, target):
            await self.sleep(step.delay_ms / 1000)
            if not lease.active:
                return
            await mouse("mouseMoved", Point(step.x, step.y))
        await self.sleep(self.motion.dwell_ms() / 1000)
        if not lease.active:
            return
        attempt.clicked = True
        await mouse("mousePressed", target, button="left", buttons=1, clickCount=1)
        await self.sleep(self.motion.press_ms() / 1000)
        await mouse("mouseReleased", target, button="left", buttons=0, clickCount=1)

    async def watch(self, attempt: Attempt) -> tuple[str, str]:
        """The outcome of the press and the URL it left, read for up to 30 seconds."""
        url, validations = attempt.before_url, 0
        for _ in range(WATCH_POLLS):
            await self.sleep(POLL_SECONDS)
            read = await self.read_page(attempt.lease)
            if read is None:
                continue
            url = read["url"] or url
            outcome = classify_outcome(url, read["text"], attempt.before_url,
                                       challenge=read["challenge"],
                                       field_errors=read["errors"] or None)
            if outcome in DECISIVE:
                return outcome, url
            validations = validations + 1 if outcome == "validation" else 0
            if validations >= 2:
                return outcome, url
        return "unconfirmed", url

    async def read_page(self, lease: Lease) -> dict[str, Any] | None:
        """The page's URL, text, challenge and field errors, through a fresh isolated world."""
        cdp = lease.cdp
        if cdp is None:
            return None
        try:
            info = await cdp.send("Target.getTargetInfo", {"targetId": lease.target})
            world = await cdp.send("Page.createIsolatedWorld",
                                   {"frameId": lease.target, "worldName": "drive-outcome"},
                                   lease.session)
            evaluated = await cdp.send("Runtime.evaluate", {
                "expression": f"({load('press_outcome')})()",
                "contextId": world["executionContextId"], "returnByValue": True,
            }, lease.session)
            value = evaluated["result"]["value"]
        except (CdpError, KeyError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        frames = [frame for frame in value.get("frames") or [] if isinstance(frame, dict)]
        return {
            "url": str((info.get("targetInfo") or {}).get("url") or value.get("url") or ""),
            "text": str(value.get("text") or ""),
            "challenge": any(is_challenge_frame(
                str(frame.get("src") or ""), float(frame.get("width") or 0),
                float(frame.get("height") or 0), frame.get("visible") is True,
            ) for frame in frames),
            "errors": int(value.get("errors") or 0),
        }

    async def screenshot(self, lease: Lease) -> tuple[str | None, str | None]:
        """The page after the press as base64 JPEG, and where it was saved."""
        if lease.cdp is None:
            return None, None
        try:
            shot = await lease.cdp.send("Page.captureScreenshot",
                                        {"format": "jpeg", "quality": 60}, lease.session)
            data = str(shot["data"])
            raw = base64.b64decode(data, validate=True)
        except (CdpError, KeyError, ValueError):
            return None, None
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        name = re.sub(r"[^\w-]", "_", lease.listing_id)[:80]
        path = self.drive.files.parent / "shots" / f"{name}-{stamp}.jpg"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        except OSError:
            return data, None
        return data, str(path)

    async def record(self, attempt: Attempt, outcome: str, url: str) -> None:
        """Lock by outcome, write the scorecard line, tell the driver, end the lease."""
        lease = attempt.lease
        data, path = await self.screenshot(lease)
        state = OUTCOME_LOCKS[outcome]
        if state is None:
            self.drive.locks.release(lease.listing_id)
        else:
            self.drive.locks.lock(lease.listing_id, attempt.before_url, state,
                                  f"experiment {attempt.experiment}: {outcome}")
        self.drive.audit.write(lease.listing_id, "press", {"experiment": attempt.experiment},
                               f"outcome: {outcome}")
        self.drive.scorecard.write(
            listing_id=lease.listing_id, url=attempt.before_url, experiment=attempt.experiment,
            knobs={**attempt.knobs, **{f"agent_{key}": value
                                       for key, value in attempt.agent_knobs.items()}},
            debugger_attached_at_press=attempt.debugger_attached,
            input_kind=attempt.input_kind, fill_seconds=self.drive.clock() - lease.started,
            outcome=outcome, screenshot=path,
        )
        lease.outbox.put_nowait({"type": "outcome", "outcome": outcome, "url": url,
                                 "screenshot": data})
        await self.drive.end(f"the press finished: {outcome}", lease)
