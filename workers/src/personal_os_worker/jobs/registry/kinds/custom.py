"""Custom kinds: a widget no built-in kind captures, taught to the worker as one module.

The seven built-in kinds are found by `inspect_fields` among form inputs. A
widget that has none (an ARIA slider, a star rating, a calendar of buttons) is
taught by a module in this package that registers a `CustomKind`:

    register(CustomKind(
        name="slider",
        selector='[role="slider"]',
        read="element => ({value: element.getAttribute('aria-valuenow') || ''})",
        strategies={"slider_keys": press_arrows_until_value},
    ))

`selector` finds the widget's root; `read` is a JavaScript function of that
element returning what it holds now: `value` (what a readback compares), and
when it knows them `label` (the question), `options`, `required`, and `filled`.
Each strategy is an operation like `browser.OPERATIONS`'s, taking an `Attempt`
whose `control` is the widget's root and whose `fill.value` is the value the
fact was written as; it raises when the widget refuses, and the readback is
`read` again. Registering a kind adds its strategies to the registry's tables,
so the fill, the repair lane, and the probe all operate it, and it is never an
`unknown_control` again. The fixer (`jobs/fix/`) adds a kind as a new module
here, with a test against the page it met it on.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Operation = Callable[[Any], str | None]


@dataclass(frozen=True)
class CustomKind:
    name: str
    selector: str
    read: str
    strategies: dict[str, Operation] = field(default_factory=dict)
    # Passed to evaluate as an argument beside the element, never interpolated
    # into the script text (an "attribute:" read's name travels this way).
    read_arg: str = ""
    # The learned declaration this kind came from, for trial bookkeeping.
    declaration: Any = None


KINDS: dict[str, CustomKind] = {}
_loaded = False


def register(kind: CustomKind) -> CustomKind:
    """Make a kind known to every pass that captures, plans, operates, or repairs."""
    from personal_os_worker.jobs.registry.strategies import APPLIES, CANDIDATES, OPERATIONS

    KINDS[kind.name] = kind
    CANDIDATES[kind.name] = tuple(kind.strategies)
    for name, operation in kind.strategies.items():
        OPERATIONS[name] = operation
        APPLIES[name] = lambda _field, fill, _document: fill.value is not None
    return kind


def load() -> dict[str, CustomKind]:
    """Import every kind module once, so each registers itself."""
    global _loaded
    if not _loaded:
        _loaded = True
        # Custom kinds are ordinary modules beside this file; a site's learned
        # kinds load only through `registry.learned`, never here.
        package = sys.modules[__name__.rsplit(".", 1)[0]]
        for module in pkgutil.iter_modules(package.__path__):
            if module.name in {"custom", "learned"} or module.name.startswith("_"):
                continue
            importlib.import_module(f"{package.__name__}.{module.name}")
    return KINDS


# How a widget's question is found: its own attribute, a referenced or
# explicit label, or nearby text — one logic shared by the custom-kind capture
# below and the unknown-control scan (`browser.inspect_unknown_controls`), so a
# widget is never reported unknown for a label capture would itself find.
# `native` is the form-input selector questionOf needs for its nearby-text rule.
QUESTION_OF = r"""
const native = 'input, textarea, select';
const questionOf = node => {
  const labelledBy = node.getAttribute('aria-labelledby');
  const referenced = labelledBy
    ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || '')
        .join(' ').trim()
    : '';
  const explicit = node.id
    ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`)?.textContent || '' : '';
  let nearby = '';
  let wrapper = node.parentElement;
  for (let depth = 0; wrapper && !nearby && depth < 5; depth += 1) {
    const heading = [...wrapper.children].find(child => (
      ['LABEL', 'LEGEND', 'P', 'SPAN', 'DIV', 'H3', 'H4'].includes(child.tagName)
      && !child.contains(node) && (child.textContent || '').trim()
      && !child.querySelector(native + ', [role]')
      && (child.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)
    ));
    nearby = heading ? heading.textContent : '';
    wrapper = wrapper.parentElement;
  }
  return (node.getAttribute('aria-label') || referenced || explicit || nearby || '')
    .replace(/\s+/g, ' ').trim().replace(/\s*\*\s*$/, '');
};
"""


# The script that captures every widget of one custom kind in a frame. The
# kind's `read` is composed into the expression Playwright sends over CDP, never
# evaluated by the page, so a page's content security policy cannot refuse it.
CAPTURE_SCRIPT = (
    r"""
(elements, read, readArg) => {
"""
    + QUESTION_OF
    + r"""
  return elements.map((element, position) => {
    if (!(element.offsetWidth || element.offsetHeight || element.getClientRects().length)) {
      return null;
    }
    let held = {};
    try { held = read(element, readArg) || {}; } catch (error) { held = {}; }
    const label = String(held.label || questionOf(element) || '')
      .replace(/\s+/g, ' ').trim().replace(/\s*\*\s*$/, '');
    const value = held.value === undefined || held.value === null ? '' : String(held.value);
    return {
      position,
      label,
      value,
      options: Array.isArray(held.options) ? held.options.map(String) : [],
      required: Boolean(held.required) || element.getAttribute('aria-required') === 'true',
      filled: held.filled === undefined ? value.trim().length > 0 : Boolean(held.filled),
      selector: element.id ? `#${CSS.escape(element.id)}` : '',
      role: element.getAttribute('role'),
      tag: element.tagName.toLowerCase(),
    };
  }).filter(Boolean);
}
"""
)


def capture(frame: Any, kind: CustomKind) -> list[dict[str, Any]]:
    """Every visible widget of this kind in a frame, as a field the planner can fill."""
    raw = frame.locator(kind.selector).evaluate_all(
        f"elements => ({CAPTURE_SCRIPT})(elements, {kind.read})", kind.read_arg
    )
    found: list[dict[str, Any]] = []
    for item in raw:
        position = item.pop("position")
        label = str(item.get("label") or "")
        found.append(item | {
            "type": kind.name,
            "custom": kind.name,
            "custom_index": position,
            "group": "",
            "fingerprint": f"custom|{kind.name}|{label}".casefold(),
        })
    return found


def read(control: Any, kind: CustomKind) -> dict[str, Any]:
    """What one widget holds now, by its kind's own `read`."""
    held = control.evaluate(
        "(element, read_arg) => {"
        f"const held = ({kind.read})(element, read_arg) || {{}}; return held;"
        "}",
        kind.read_arg,
    )
    return held if isinstance(held, dict) else {}
