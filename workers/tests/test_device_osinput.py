"""Human-shaped motion and the OS-input batch, with no Windows: a fake powershell.exe."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import Any

import pytest

from personal_os_worker.device import osinput
from personal_os_worker.device.motion import Box, Motion, Point
from personal_os_worker.device.osinput import (
    OsInput,
    OsInputError,
    ViewportOrigin,
    click_ops,
    key_op,
    type_ops,
    wheel_ops,
)


@pytest.mark.parametrize("seed", range(20))
def test_a_pointer_path_is_curved_short_and_ends_on_its_target(seed: int) -> None:
    motion = Motion(seed)
    start, end = Point(10, 10), Point(400, 300)
    steps = motion.path(start, end)
    assert 8 <= len(steps) <= 20
    assert 150 - len(steps) <= sum(step.delay_ms for step in steps) <= 450 + len(steps)
    assert (steps[-1].x, steps[-1].y) == (end.x, end.y)
    # Not a straight line: some step sits off the chord by more than the jitter.
    def off(step: Any) -> float:
        return abs((end.y - start.y) * step.x - (end.x - start.x) * step.y
                   + end.x * start.y - end.y * start.x) / 492.0
    assert max(off(step) for step in steps) > 2.0


def test_the_same_seed_draws_the_same_motion() -> None:
    start, end = Point(0, 0), Point(100, 50)
    assert Motion(7).path(start, end) == Motion(7).path(start, end)
    assert Motion(7).key_delays("hello, world") == Motion(7).key_delays("hello, world")


@pytest.mark.parametrize("seed", range(20))
def test_a_target_lies_in_the_inner_sixty_percent_of_its_box(seed: int) -> None:
    point = Motion(seed).target(Box(100, 200, 50, 20))
    assert 110 <= point.x <= 140 and 204 <= point.y <= 216


def test_a_box_from_a_quad() -> None:
    box = Box.from_quad([10, 20, 110, 20, 110, 60, 10, 60])
    assert (box.x, box.y, box.width, box.height) == (10, 20, 100, 40)
    assert box.center == Point(60, 40)


@pytest.mark.parametrize("seed", range(10))
def test_dwell_and_press_timings(seed: int) -> None:
    motion = Motion(seed)
    assert 80 <= motion.dwell_ms() <= 250 and 50 <= motion.press_ms() <= 120


def test_keys_come_40_to_140_ms_apart_with_pauses_after_punctuation() -> None:
    text = "Hi, there. " + "abcdefghijklmnopqrstuvwxyz" * 2
    delays = Motion(3).key_delays(text)
    assert len(delays) == len(text)
    assert all(40 <= delay <= 140 + 900 for delay in delays)
    assert delays[text.index(",") + 1] >= 340 and delays[text.index(".") + 1] >= 340
    # And a pause at least every 20 characters even with no punctuation.
    tail = delays[12:]
    assert all(any(delay >= 340 for delay in tail[i:i + 21]) for i in range(0, len(tail) - 21))


@pytest.mark.parametrize("dy", [0, 50, 333, -500])
def test_wheel_steps_add_up_and_stay_80_to_120(dy: int) -> None:
    steps = Motion(1).wheel(dy)
    assert sum(steps) == dy
    assert all(80 <= abs(step) <= 120 for step in steps[:-1])
    assert all((step > 0) == (dy > 0) for step in steps)


ORIGIN = ViewportOrigin(screen_x=100, screen_y=50, outer_width=1216, inner_width=1200,
                        outer_height=900, inner_height=779, dpr=1.5)


def test_a_css_point_maps_to_physical_screen_pixels() -> None:
    # The frame is 8 px a side; the tab strip and toolbar are 900 - 779 - 8 = 113 px.
    assert ORIGIN.to_screen(Point(0, 0)) == (round(108 * 1.5), round(163 * 1.5))
    assert ORIGIN.to_screen(Point(200, 100)) == (round(308 * 1.5), round(263 * 1.5))


def test_the_origin_is_read_from_the_one_line_expression() -> None:
    assert "\n" not in osinput.ORIGIN_EXPRESSION
    parsed = ViewportOrigin.parse(json.dumps([100, 50, 1216, 1200, 900, 779, 1.5]))
    assert parsed == ORIGIN
    with pytest.raises(OsInputError):
        ViewportOrigin.parse("[1, 2]")


class FakePowershell:
    """powershell.exe as the batch sees it: records each batch and answers one JSON line."""

    def __init__(self, *, ok: bool = True, error: str | None = None,
                 cursor: tuple[int, int] = (5000, 5000)) -> None:
        self.ok, self.error, self.cursor = ok, error, cursor
        self.batches: list[list[dict[str, Any]]] = []
        self.args: list[list[str]] = []

    def __call__(self, args: list[str], stdin: str, timeout: float) -> Any:
        self.args.append(args)
        assert stdin.count("\n") == 1 and timeout >= osinput.BATCH_TIMEOUT_SECONDS
        encoded = re.search(r"FromBase64String\('([^']+)'\)", stdin)
        assert encoded is not None
        body = base64.b64decode(encoded.group(1)).decode()
        assert "SetProcessDpiAwarenessContext" in body and "MOUSEEVENTF" not in body[:20]
        actions = re.match(r"\$Actions = '([^']+)'\n", body)
        assert actions is not None
        self.batches.append(json.loads(base64.b64decode(actions.group(1))))
        answer = {"ok": self.ok, "error": self.error, "cursor": list(self.cursor),
                  "rect": [0, 0, 2000, 1400], "hwnd": 4242}
        return subprocess.CompletedProcess(args, 0, "noise\n" + json.dumps(answer) + "\n", "")


def test_a_click_is_one_batch_that_focuses_then_moves_hovers_and_presses() -> None:
    fake = FakePowershell()
    runner = OsInput(fake, Motion(5))
    runner.click("Pivotal", ORIGIN, Point(200, 100))
    assert fake.args[0][-4:] == ["-NoProfile", "-NonInteractive", "-Command", "-"]
    focus, batch = fake.batches
    assert focus == [{"op": "focus", "hwnd": 0, "title": "Pivotal"}]
    assert batch[0] == {"op": "focus", "hwnd": 4242, "title": ""}
    kinds = [op["op"] for op in batch[1:]]
    assert kinds == ["move", "wait", "down", "wait", "up"]
    points = batch[1]["points"]
    assert points[-1][:2] == list(ORIGIN.to_screen(Point(200, 100)))
    # The cursor was outside the window, so the path starts inside it, near the target.
    assert all(0 <= x < 2000 and 0 <= y < 1400 for x, y, _delay in points)
    assert 80 <= batch[2]["ms"] <= 250 and 50 <= batch[4]["ms"] <= 120


def test_a_click_starts_from_the_cursor_when_it_is_in_the_window() -> None:
    fake = FakePowershell(cursor=(900, 700))
    OsInput(fake, Motion(2)).click(4242, ORIGIN, Point(10, 10))
    # The first batch only focuses and learns the window's bounds and the cursor.
    assert [len(batch) for batch in fake.batches] == [1, 6]
    first = fake.batches[1][1]["points"][0]
    assert abs(first[0] - 900) < 200 and abs(first[1] - 700) < 200


def test_a_point_outside_the_window_is_refused_before_anything_runs() -> None:
    fake = FakePowershell(cursor=(10, 10))
    with pytest.raises(OsInputError, match="outside the Chrome window"):
        OsInput(fake, Motion(1)).click(4242, ORIGIN, Point(5000, 10))
    assert [op["op"] for op in fake.batches[0]] == ["focus"]


def test_a_failed_batch_raises_what_it_said() -> None:
    fake = FakePowershell(ok=False, error="the Chrome window lost focus")
    with pytest.raises(OsInputError, match="lost focus"):
        OsInput(fake).focus_window("Pivotal")


def test_no_answer_from_powershell_raises() -> None:
    def silent(args: list[str], _stdin: str, _timeout: float) -> Any:
        return subprocess.CompletedProcess(args, 1, "", "Add-Type failed")

    with pytest.raises(OsInputError, match="Add-Type failed"):
        OsInput(silent).focus_window(1)


def test_typing_is_a_unicode_event_per_character_with_enter_and_tab_as_keys() -> None:
    ops = type_ops(Motion(4), "é,\nb\t")
    assert [op["op"] for op in ops] == ["wait", "text", "wait", "text", "wait", "key",
                                        "wait", "text", "wait", "key"]
    assert ops[1]["chunk"] == "é" and ops[5]["vk"] == 0x0D and ops[9]["vk"] == 0x09
    assert ops[4]["ms"] >= 340  # the pause after the comma


def test_named_keys() -> None:
    assert key_op("ctrl+a")["mods"] == [0x11] and key_op("ctrl+a")["vk"] == 0x41
    assert key_op("Down")["ext"] is True and key_op("Backspace")["vk"] == 0x08
    assert key_op("Escape")["vk"] == 0x1B
    with pytest.raises(OsInputError, match="unknown key"):
        key_op("f13")


def test_wheel_down_is_a_negative_delta() -> None:
    deltas = [op["delta"] for op in wheel_ops(Motion(1), 250) if op["op"] == "wheel"]
    assert sum(deltas) == -250


def test_the_script_is_package_data_and_checks_every_op() -> None:
    text = osinput.script()
    assert "SendInput" in text and "GetForegroundWindow() -ne $script:Hwnd" in text
    assert "VIRTUALDESK" in text and "SetProcessDPIAware" in text
    assert osinput.batch_ms(click_ops(Motion(1), Point(0, 0), Point(10, 10))) > 0
