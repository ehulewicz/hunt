"""Suggestion strategies: type into a box, click the matching suggestion."""

from __future__ import annotations

import time

from personal_os_worker.jobs.registry.strategies import (
    CONTROL_TIMEOUT_MS,
    Attempt,
    NoMatchingOption,
)
from personal_os_worker.jobs.registry.strategies.combobox import _pick_suggestion

# The visible, childless texts inside a field's own container: what a
# suggestion list without ARIA roles shows once something is typed.
FIELD_TEXTS_SCRIPT = """
element => {
  const box = element.closest(
    'li, fieldset, [class*="question" i], [class*="field" i], [class*="form-group" i]'
  ) || element.parentElement;
  const out = [];
  for (const node of box.querySelectorAll('*')) {
    if (node.children.length || node === element || node.closest('label > :first-child')) continue;
    const text = (node.textContent || '').trim();
    const rect = node.getBoundingClientRect();
    if (text && rect.width > 0 && rect.height > 0 && text.length < 160) out.push(text);
  }
  return out;
}
"""

# How long a plain suggestion list may take to appear after typing.
SUGGESTION_TIMEOUT_S = 4.0

def _type_and_pick_suggestion(attempt: Attempt) -> str | None:
    """Type into a text box whose suggestions are plain elements, then click the match.

    Some autocompletes (Lever's location) render suggestions with no ARIA
    roles and clear the box on blur unless one is chosen. What appears in
    the field's own container after typing is the suggestion list; the full
    answer is typed first, then only its first part ("Atlanta"), since a
    places search rarely knows "Atlanta, Georgia, United States".
    """
    control = attempt.control
    value = str(attempt.fill.value)
    before = set(control.evaluate(FIELD_TEXTS_SCRIPT, timeout=CONTROL_TIMEOUT_MS))
    for typed in dict.fromkeys([value, value.split(",")[0].strip()]):
        control.fill("", timeout=CONTROL_TIMEOUT_MS)
        control.press_sequentially(typed, delay=30, timeout=CONTROL_TIMEOUT_MS)
        deadline = time.monotonic() + SUGGESTION_TIMEOUT_S
        while time.monotonic() < deadline:
            attempt.page.wait_for_timeout(250)
            texts = [
                text
                for text in control.evaluate(FIELD_TEXTS_SCRIPT, timeout=CONTROL_TIMEOUT_MS)
                if text not in before
            ]
            chosen = _pick_suggestion(texts, value) if texts else None
            if chosen is not None:
                box = control.locator(
                    "xpath=ancestor::*[self::li or self::fieldset or contains(@class,'question')"
                    " or contains(@class,'field')][1]"
                )
                box.get_by_text(chosen, exact=True).first.click(timeout=CONTROL_TIMEOUT_MS)
                attempt.page.wait_for_timeout(300)
                shown = str(control.input_value(timeout=CONTROL_TIMEOUT_MS)).strip()
                return shown or chosen
    raise NoMatchingOption("no suggestion matched what was typed")
