"""Button-group strategies: pressable aria buttons as a choice."""

from __future__ import annotations

from typing import Any

from personal_os_worker.jobs.registry.strategies import (
    CONTROL_TIMEOUT_MS,
    Attempt,
    NoMatchingOption,
)
from personal_os_worker.jobs.sites import option_match

# The nearest ancestor holding a group of aria-pressed buttons: the same
# generic test the detector uses, so no site's class names are involved.
BUTTON_GROUP_XPATH = "xpath=ancestor::*[count(.//button[@aria-pressed]) >= 2][1]"

def button_choice(texts: list[str], value: str) -> str | None:
    """The one pressable button that answers the value."""
    chosen = option_match(texts, value)
    if chosen is None and {text.casefold() for text in texts} == {"yes", "no"}:
        wanted = "yes" if value.strip().casefold() == "yes" else "no"
        chosen = next(text for text in texts if text.casefold() == wanted)
    return chosen


def _button_group(control: Any) -> tuple[Any, list[str]]:
    buttons = control.locator(BUTTON_GROUP_XPATH).locator("button[aria-pressed]")
    texts = [str(buttons.nth(index).inner_text()).strip() for index in range(buttons.count())]
    return buttons, texts

def _click_option_button(attempt: Attempt) -> str | None:
    buttons, texts = _button_group(attempt.control)
    chosen = button_choice(texts, str(attempt.fill.value))
    if chosen is None:
        raise NoMatchingOption("no unambiguous option button")
    buttons.nth(texts.index(chosen)).click(timeout=CONTROL_TIMEOUT_MS)
    return chosen
