"""Combobox strategies: the typeahead, its menu, and its suggestions."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.office import parse
from personal_os_worker.jobs.options import choose_option, choose_synonym, normalize_question
from personal_os_worker.jobs.registry.formats.choices import (
    affirmative_option,
    source_option,
    yes_no_option,
)
from personal_os_worker.jobs.registry.formats.dates import choose_date_range
from personal_os_worker.jobs.registry.strategies import CONTROL_TIMEOUT_MS, Attempt


def _combobox_choose_from_menu(attempt: Attempt) -> str | None:
    return choose_combobox_option(
        attempt.page, attempt.control, str(attempt.fill.chooser),
        referred=attempt.fill.referred,
    )


def _combobox_type_and_select(attempt: Attempt) -> str | None:
    return select_combobox_option(attempt.page, attempt.control, str(attempt.fill.value))

MONTH_PREFIXES = (
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
)


def _pick_suggestion(texts: list[str], value: str) -> str | None:
    """The suggestion that names the same place, else the one option that matches."""

    wanted = parse(value)
    if wanted.city and wanted.state:
        same = [
            text for text in dict.fromkeys(texts)
            if (place := parse(text)).city and place.city.casefold() == wanted.city.casefold()
            and place.state == wanted.state
        ]
        if same:
            return same[0]
    return _pick(texts, value)

COMBOBOX_SETTLE_MS = 300
# How long a plain suggestion list may take to appear after typing.
SUGGESTION_TIMEOUT_S = 4.0
COMBOBOX_LIST_TIMEOUT_MS = 600
COMBOBOX_OPTIONS_TIMEOUT_MS = 4_000

def _menu_options(control: Any) -> Any:
    # Scope to the control's own document so a form inside an iframe works too.
    scoped = control.locator("xpath=ancestor::body")
    try:
        in_document = scoped.count() > 0
    except Error:
        in_document = False
    if in_document:
        owned_id = control.get_attribute("aria-controls") or control.get_attribute("aria-owns")
        if owned_id and len(owned_id.split()) == 1:
            owned = scoped.locator(f"[id={json.dumps(owned_id)}]")
            # Some accessible menus identify options by their disabled state
            # and keyboard tab stop, without repeating role=option.
            choices = owned.locator('[role="option"], [aria-disabled][tabindex="-1"]')
            if choices.count():
                return choices
        return scoped.get_by_role("option")
    # Inside a shadow root (SmartRecruiters) XPath cannot climb to <body>; role
    # queries pierce shadow roots, so ask the page.
    return control.page.get_by_role("option")


# An option's text, wherever its component renders it: its own text, else
# what its <slot>s show, else its shadow host's light text (SmartRecruiters'
# dropdown items keep the text outside the shadow root that holds role=option).
OPTION_TEXT_SCRIPT = """
element => {
  const own = (element.innerText || '').trim();
  if (own) return own;
  const slotted = [...element.querySelectorAll('slot')]
    .flatMap(slot => slot.assignedNodes({flatten: true}))
    .map(node => node.textContent || '').join(' ').replace(/\\s+/g, ' ').trim();
  if (slotted) return slotted;
  const host = element.getRootNode().host;
  return host ? (host.innerText || host.textContent || '').replace(/\\s+/g, ' ').trim() : '';
}
"""


def _visible_texts(options: Any) -> tuple[list[int], list[str]]:
    # Pages keep other listboxes in the DOM (Greenhouse's phone-code list has
    # every country as a hidden option); only what the menu shows is a choice.
    visible = [index for index in range(options.count()) if options.nth(index).is_visible()]
    return visible, [str(options.nth(index).evaluate(OPTION_TEXT_SCRIPT)) for index in visible]


def _pick(texts: list[str], value: str) -> str | None:
    distinct = list(dict.fromkeys(text for text in texts if text))
    chosen = choose_option(distinct, value) or choose_synonym(distinct, value)
    if chosen is None and value.strip().casefold() in {"yes", "no"}:
        # The same reading of yes and no a select gets ("No" is "Not Applicable"
        # when that is the only option saying no).
        chosen = yes_no_option(distinct, value.strip().casefold() == "yes")
    month = re.fullmatch(r"\s*([A-Za-z]+)\s+(\d{4})\s*", value)
    if chosen is None and month and month.group(1)[:3].casefold() in MONTH_PREFIXES:
        # "December 2027" against "Between December 2027 and September 2028".
        when = date(int(month.group(2)), MONTH_PREFIXES.index(month.group(1)[:3].casefold()) + 1, 1)
        chosen = choose_date_range(distinct, when)
    if chosen is None:
        target = normalize_question(value)
        containing = [text for text in distinct if target and target in normalize_question(text)]
        chosen = containing[0] if len(containing) == 1 else None
    return chosen


def _await_options(page: Page, options: Any, timeout_ms: int) -> bool:
    # Any visible option, not the first in the DOM: a closed native <select>
    # earlier on the page contributes hidden options that never become visible.
    try:
        options.locator("visible=true").first.wait_for(state="visible", timeout=timeout_ms)
    except Error:
        return False
    page.wait_for_timeout(COMBOBOX_SETTLE_MS)
    return True


def open_combobox(control: Any) -> None:
    """Open the menu through its keyboard input when that input is too small to click."""
    box = control.bounding_box()
    if (control.get_attribute("aria-readonly") == "true" and box is not None
            and (box["width"] < 1 or box["height"] < 1)):
        control.focus(timeout=CONTROL_TIMEOUT_MS)
        control.press("ArrowDown", timeout=CONTROL_TIMEOUT_MS)
    else:
        control.click(timeout=CONTROL_TIMEOUT_MS)


def combobox_options(page: Page, control: Any) -> list[str]:
    """What a typeahead offers before anything is typed; empty for a search box."""
    open_combobox(control)
    options = _menu_options(control)
    texts: list[str] = []
    if _await_options(page, options, COMBOBOX_LIST_TIMEOUT_MS):
        texts = list(dict.fromkeys(text for text in _visible_texts(options)[1] if text))
    control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
    return texts


def choose_combobox_option(
    page: Page, control: Any, chooser: str, *, referred: bool = False
) -> str:
    """Open a typeahead and pick by standing rule: the agreeing option, or your source."""
    open_combobox(control)
    options = _menu_options(control)
    if not _await_options(page, options, COMBOBOX_LIST_TIMEOUT_MS):
        control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
        raise RuntimeError(f"no unambiguous combobox option for {chooser} (none listed)")
    visible, texts = _visible_texts(options)
    distinct = list(dict.fromkeys(text for text in texts if text))
    if chooser == "consent":
        chosen = affirmative_option(distinct)
    else:
        chosen = source_option(distinct, referred=referred)
    if chosen is None:
        control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
        raise RuntimeError(f"no unambiguous combobox option for {chooser}")
    options.nth(visible[texts.index(chosen)]).click()
    return chosen


def select_combobox_option(page: Page, control: Any, value: str) -> str:
    """Pick the one option a typeahead offers for the answer and return its text.

    Open the menu first: a fixed list (Yes/No, GPA ranges) is matched as it
    is, since typing "3.78" into a list of ranges filters it down to nothing.
    Only when nothing is listed, or nothing listed fits, is the answer typed,
    which is how a search box (Google Places) or a long list is queried.
    Keystrokes rather than a synthetic value: react-select and Places key
    their menus off real input events and close on blur.
    """
    open_combobox(control)
    options = _menu_options(control)
    if _await_options(page, options, COMBOBOX_LIST_TIMEOUT_MS):
        visible, texts = _visible_texts(options)
        chosen = _pick(texts, value)
        if chosen is not None:
            options.nth(visible[texts.index(chosen)]).click()
            return chosen
    if control.get_attribute("aria-readonly") == "true":
        control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
        raise RuntimeError("no unambiguous combobox option in read-only menu")
    # A place ("Atlanta, Georgia") is searched whole, then by its first part,
    # since a places search rarely knows the state's full name; either way the
    # option naming the same city and state is the one picked.
    tries = list(dict.fromkeys([value, value.split(",")[0].strip()]))
    for attempt, typed in enumerate(tries):
        control.fill("", timeout=CONTROL_TIMEOUT_MS)
        control.press_sequentially(typed, delay=20, timeout=CONTROL_TIMEOUT_MS)
        if not _await_options(page, options, COMBOBOX_OPTIONS_TIMEOUT_MS):
            if attempt + 1 < len(tries):
                continue
            # A free-entry typeahead offers nothing to pick; Enter commits the text.
            control.press("Enter", timeout=CONTROL_TIMEOUT_MS)
            if combobox_value(control).casefold() == value.strip().casefold():
                return value.strip()
            raise RuntimeError("no unambiguous combobox option (none appeared)")
        visible, texts = _visible_texts(options)
        chosen = _pick_suggestion(texts, value)
        if chosen is not None:
            options.nth(visible[texts.index(chosen)]).click()
            return chosen
    control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
    raise RuntimeError("no unambiguous combobox option")

COMBOBOX_VALUE_SCRIPT = """
    element => {
      const own = String(element.value || '').trim();
      if (own) return own;
      let shell = element.parentElement;
      for (let depth = 0; shell && depth < 4; depth += 1) {
        const chosen = shell.querySelector('[class*="single-value"], [class*="multi-value"]');
        if (chosen && chosen.textContent.trim()) return chosen.textContent.trim();
        const mirror = shell.querySelector('input[aria-hidden="true"]');
        if (mirror && String(mirror.value || '').trim()) return String(mirror.value).trim();
        shell = shell.parentElement;
      }
      return '';
    }
"""


def combobox_value(control: Any) -> str:
    """What a typeahead currently holds, wherever the widget keeps it.

    A plain typeahead leaves the chosen text in the input. react-select
    empties the search input and renders the choice in a sibling
    `single-value`/`multi-value` element, or mirrors it into an aria-hidden
    input. Whichever is present is the value.
    """
    return str(control.evaluate(COMBOBOX_VALUE_SCRIPT, timeout=CONTROL_TIMEOUT_MS)).strip()


def combobox_shows(control: Any, expected: str) -> bool:
    """Whether a typeahead holds the picked option.

    The option was clicked by its text, so a rendered choice is the evidence;
    its display text may differ from the menu text (Greenhouse's phone-code
    picker lists "United States +1" but shows a flag and "+1").
    """
    held = combobox_value(control)
    return bool(held) and (
        _pick([expected], held) is not None
        or control.evaluate(
            "element => !String(element.value || '').trim()", timeout=CONTROL_TIMEOUT_MS
        )
    )
