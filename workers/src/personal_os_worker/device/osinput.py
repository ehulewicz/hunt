"""OS-level input on Windows, from WSL: trusted pointer and keyboard events through `SendInput`.

The press routes that keep CDP off the tab (experiments 2-4) click and type
with this. Python plans a whole action as a list of ops (paths and timings from
`motion.py`); one `powershell.exe -NoProfile -NonInteractive -Command -` runs
the batch, because PowerShell starts slowly. The script is `osinput.ps1` beside
this module, package data as page scripts are: it declares the `user32` calls
through `Add-Type` C#, makes the process per-monitor DPI aware (V2, falling
back to system aware) so every coordinate is a physical pixel, and moves with
`MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` normalized over the virtual
screen. Its first op restores and foregrounds the Chrome window; every later op
stops the batch if that window lost the foreground or a point falls outside it,
so nothing outside the lease's window is clicked or typed into.

A page's CSS point becomes a screen pixel through a viewport origin measured in
the leased tab (`ORIGIN_EXPRESSION`, evaluated in an isolated world):

    screen_x = (screenX + (outerWidth - innerWidth) / 2 + css_x) * dpr
    screen_y = (screenY + (outerHeight - innerHeight) - border + css_y) * dpr

where `border = (outerWidth - innerWidth) / 2`, the frame on each side, and the
rest of the height difference is Chrome's tab strip and toolbar. It assumes
browser zoom at 100% and no device-metrics override: the live view's
`Emulation` override changes `innerWidth` and `devicePixelRatio`, so an OS
route closes the live-view stream before it measures.
"""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from personal_os_worker.core.service import POWERSHELL
from personal_os_worker.device.motion import Motion, Point

# A one-line read of the window geometry; returns a JSON array string.
ORIGIN_EXPRESSION = (
    "JSON.stringify([window.screenX, window.screenY, window.outerWidth, window.innerWidth,"
    " window.outerHeight, window.innerHeight, window.devicePixelRatio])"
)
# Named keys: virtual-key code, whether it is an extended key, and modifiers held.
KEYS: dict[str, tuple[int, bool, tuple[int, ...]]] = {
    "tab": (0x09, False, ()), "enter": (0x0D, False, ()), "escape": (0x1B, False, ()),
    "backspace": (0x08, False, ()), "space": (0x20, False, ()),
    "left": (0x25, True, ()), "up": (0x26, True, ()), "right": (0x27, True, ()),
    "down": (0x28, True, ()), "shift+tab": (0x09, False, (0x10,)),
    "ctrl+a": (0x41, False, (0x11,)),
}
# What `type` sends as a key rather than as a Unicode character.
TYPED_KEYS = {"\n": "enter", "\t": "tab"}
BATCH_TIMEOUT_SECONDS = 30.0

Runner = Callable[[list[str], str, float], "subprocess.CompletedProcess[str]"]
Window = int | str  # an HWND, or a substring of the Chrome window's title


class OsInputError(RuntimeError):
    """The batch did not run to its end; the message says where it stopped."""


@cache
def script() -> str:
    return resources.files(__package__).joinpath("osinput.ps1").read_text(encoding="utf-8")


def run_powershell(args: list[str], stdin: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, input=stdin, capture_output=True, text=True, timeout=timeout, check=False,
    )


@dataclass(frozen=True, slots=True)
class ViewportOrigin:
    """Where the leased tab's viewport sits on screen, in CSS pixels, and its scale."""

    screen_x: float
    screen_y: float
    outer_width: float
    inner_width: float
    outer_height: float
    inner_height: float
    dpr: float

    @classmethod
    def parse(cls, value: str | list[float]) -> ViewportOrigin:
        """From what `ORIGIN_EXPRESSION` returned."""
        values = json.loads(value) if isinstance(value, str) else value
        if not isinstance(values, list) or len(values) != 7:
            raise OsInputError(f"unexpected window geometry: {str(value)[:200]}")
        numbers = [float(item) for item in values]
        if numbers[6] <= 0:
            raise OsInputError("the device pixel ratio is not positive")
        return cls(*numbers)

    def to_screen(self, point: Point) -> tuple[int, int]:
        """A CSS point in the viewport, as a physical screen pixel."""
        border = (self.outer_width - self.inner_width) / 2
        top = self.outer_height - self.inner_height - border
        return (round((self.screen_x + border + point.x) * self.dpr),
                round((self.screen_y + top + point.y) * self.dpr))


def focus_op(window: Window) -> dict[str, Any]:
    if isinstance(window, int):
        return {"op": "focus", "hwnd": window, "title": ""}
    if not window.strip():
        raise OsInputError("a window title to find is empty")
    return {"op": "focus", "hwnd": 0, "title": window}


def wait_op(ms: int) -> dict[str, Any]:
    return {"op": "wait", "ms": max(0, int(ms))}


def move_op(motion: Motion, start: Point, end: Point) -> dict[str, Any]:
    """A human path in screen pixels, each point with the wait before it."""
    return {"op": "move", "points": [
        [round(step.x), round(step.y), step.delay_ms] for step in motion.path(start, end)
    ]}


def click_ops(motion: Motion, start: Point, end: Point) -> list[dict[str, Any]]:
    """Move along a path, hover, then press and release the left button."""
    return [
        move_op(motion, start, end), wait_op(motion.dwell_ms()),
        {"op": "down"}, wait_op(motion.press_ms()), {"op": "up"},
    ]


def key_op(name: str, motion: Motion | None = None) -> dict[str, Any]:
    known = KEYS.get(name.strip().lower())
    if known is None:
        raise OsInputError(f"unknown key {name!r}; known: {', '.join(sorted(KEYS))}")
    vk, extended, mods = known
    hold = (motion or Motion()).press_ms()
    return {"op": "key", "vk": vk, "ext": extended, "mods": list(mods), "hold": hold}


def type_ops(motion: Motion, text: str) -> list[dict[str, Any]]:
    """Each character after its human delay: a Unicode key event, or Enter and Tab as keys."""
    ops: list[dict[str, Any]] = []
    for char, delay in zip(text, motion.key_delays(text), strict=True):
        ops.append(wait_op(delay))
        named = TYPED_KEYS.get(char)
        ops.append(key_op(named, motion) if named else {"op": "text", "chunk": char})
    return ops


def wheel_ops(motion: Motion, dy: float) -> list[dict[str, Any]]:
    """Scroll by `dy` CSS pixels (down is positive) in wheel steps with short gaps."""
    ops: list[dict[str, Any]] = []
    for step in motion.wheel(dy):
        ops.append(wait_op(motion.between((40, 160))))
        # A wheel delta is positive away from the user, which scrolls up.
        ops.append({"op": "wheel", "delta": -step})
    return ops


def batch_ms(ops: list[dict[str, Any]]) -> int:
    """How long the waits in a batch add up to."""
    total = 0
    for op in ops:
        if op["op"] == "wait":
            total += int(op["ms"])
        elif op["op"] == "move":
            total += sum(int(point[2]) for point in op["points"])
        elif op["op"] == "key":
            total += int(op["hold"])
    return total


def command(ops: list[dict[str, Any]]) -> tuple[list[str], str]:
    """The powershell.exe arguments and the one line it reads: the batch, then the script."""
    actions = base64.b64encode(json.dumps(ops).encode()).decode()
    body = f"$Actions = '{actions}'\n{script()}"
    encoded = base64.b64encode(body.encode("utf-8")).decode()
    # `-Command -` reads stdin line by line, so the script arrives as one line.
    line = f"iex ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))\n"
    return [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "-"], line


@dataclass(frozen=True, slots=True)
class Ran:
    """What a batch left behind: the cursor, the Chrome window's bounds and its HWND."""

    cursor: Point
    rect: tuple[int, int, int, int]
    hwnd: int

    def inside(self, x: float, y: float) -> bool:
        left, top, right, bottom = self.rect
        return left <= x < right and top <= y < bottom


def start_point(motion: Motion, ran: Ran, end: Point) -> Point:
    """Where a path begins: the cursor if it is in the window, else a point near the end."""
    if ran.inside(ran.cursor.x, ran.cursor.y):
        return ran.cursor
    left, top, right, bottom = ran.rect
    rng = motion.rng
    x = end.x + rng.choice((-1, 1)) * rng.uniform(60, 220)
    y = end.y + rng.choice((-1, 1)) * rng.uniform(40, 160)
    return Point(min(max(x, left + 1), right - 2), min(max(y, top + 1), bottom - 2))


class OsInput:
    """Batches of OS input into one Chrome window; `runner` is injectable for tests."""

    def __init__(self, runner: Runner = run_powershell, motion: Motion | None = None) -> None:
        self.runner = runner
        self.motion = motion or Motion()
        self.last: Ran | None = None

    def run(self, window: Window, ops: list[dict[str, Any]]) -> Ran:
        """Focus `window`, then run `ops` in one powershell.exe; raises if it stopped short."""
        batch = [focus_op(window), *ops]
        args, line = command(batch)
        timeout = BATCH_TIMEOUT_SECONDS + batch_ms(batch) / 1000
        try:
            done = self.runner(args, line, timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OsInputError(f"powershell.exe did not run the batch: {error}") from None
        answer = _answer(done.stdout)
        if answer is None:
            said = (done.stderr or done.stdout or "").strip()[-300:]
            raise OsInputError(f"powershell.exe gave no answer: {said or done.returncode}")
        cursor, rect = answer.get("cursor") or [0, 0], answer.get("rect") or [0, 0, 0, 0]
        ran = Ran(Point(float(cursor[0]), float(cursor[1])),
                  (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
                  int(answer.get("hwnd") or 0))
        if ran.hwnd:
            self.last = ran
        if answer.get("ok") is not True:
            raise OsInputError(str(answer.get("error") or "the batch failed"))
        return ran

    def focus_window(self, window: Window) -> Ran:
        """Restore Chrome's window and bring it to the foreground, verified."""
        return self.run(window, [])

    def _window(self, window: Window) -> tuple[Window, Ran]:
        ran = self.last
        if ran is None or (isinstance(window, int) and window != ran.hwnd):
            ran = self.focus_window(window)
        return ran.hwnd, ran

    def click(self, window: Window, origin: ViewportOrigin, css: Point) -> Ran:
        """A human-shaped left click on a CSS point of the leased tab's viewport."""
        hwnd, ran = self._window(window)
        x, y = origin.to_screen(css)
        if not ran.inside(x, y):
            raise OsInputError(f"the point {x},{y} is outside the Chrome window {ran.rect}")
        end = Point(float(x), float(y))
        return self.run(hwnd, click_ops(self.motion, self._start(ran, end), end))

    def _start(self, ran: Ran, end: Point) -> Point:
        return start_point(self.motion, ran, end)

    def type(self, window: Window, text: str) -> Ran:
        hwnd, _ran = self._window(window)
        return self.run(hwnd, type_ops(self.motion, text))

    def key(self, window: Window, name: str) -> Ran:
        hwnd, _ran = self._window(window)
        return self.run(hwnd, [key_op(name, self.motion)])

    def wheel(self, window: Window, dy: float) -> Ran:
        hwnd, _ran = self._window(window)
        return self.run(hwnd, wheel_ops(self.motion, dy))


def _answer(stdout: str) -> dict[str, Any] | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            return value if isinstance(value, dict) else None
    return None
