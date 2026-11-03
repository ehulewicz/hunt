"""Pure mapping between the viewer's pane and the tab: layout, text fit, and input events."""

from __future__ import annotations

import contextlib
from typing import Any

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
# Zoomed out to fit a page that scrolls sideways, it is shown no smaller than this.
MIN_ZOOM = 0.5
# Ignoring the outermost few percent of text on each side drops a skip link or a
# stray icon at the very edge without dropping a real column.
FIT_TRIM = 0.02
LINE_PIXELS = 40

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
    and is shown smaller. `fit` brings that layout toward the pane, never past
    it, so the text is never shown larger than 1x, or widens it to zoom out a
    page that scrolls sideways. None when the viewer gave no usable size.
    """
    try:
        width = float(view["width"])
        height = float(view["height"])
        ratio = float(view.get("dpr") or 1)
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    layout_width = max(round(width), fit or MIN_LAYOUT_WIDTH)
    return {
        "width": layout_width,
        "height": round(layout_width * height / width),
        "deviceScaleFactor": round(min(MAX_SCALE, max(1.0, ratio)), 3),
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
  return {width, boxes, scroll: document.documentElement.scrollWidth};
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


def fit_width(
    current: int, pane: int, client_width: float, span: tuple[float, float], scroll_width: float
) -> int:
    """The layout width that leaves FIT_MARGIN either side of the text, at no more than 1x.

    A page that scrolls sideways is widened until it fits, which zooms it out,
    down to MIN_ZOOM. Otherwise both sides narrow by the same amount, so a
    centered form ends up centered with the margin on each side and a form
    against one edge is not narrowed at all. It never narrows past the pane, so
    a thin form keeps the page's whitespace, and text wider than the page widens
    it again, up to the desktop layout or the width it was already zoomed out to.
    Text a clipped box cuts off does not scroll the page, so it zooms nothing out.
    """
    base = max(MIN_LAYOUT_WIDTH, pane)
    overflow = scroll_width - client_width
    if overflow > 1:
        return min(round(base / MIN_ZOOM), round(current + overflow) + 2 * FIT_MARGIN)
    left, right = span
    spare = min(left, client_width - right) - FIT_MARGIN
    return max(MIN_FIT_WIDTH, pane, min(max(base, current), round(current - 2 * spare)))


def laid_out(meta: dict[str, Any], metrics: dict[str, Any]) -> bool:
    """Whether a frame shows the tab at the layout the stream set for it."""
    try:
        width = float(meta.get("deviceWidth") or 0) - float(metrics["width"])
        height = float(meta.get("deviceHeight") or 0) - float(metrics["height"])
    except (TypeError, ValueError):
        return True
    return abs(width) <= 1 and abs(height) <= 1


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
