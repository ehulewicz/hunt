"""Native-control strategies: selects, checkboxes, and plain value writes.

Button-group strategies: pressable aria buttons as a choice.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Error

from personal_os_worker.jobs.autofill import MULTI
from personal_os_worker.jobs.page_scripts import load
from personal_os_worker.jobs.registry.strategies import (
    CONTROL_TIMEOUT_MS,
    Attempt,
    CaptchaPresent,
    NoMatchingOption,
    captcha_challenge_present,
)
from personal_os_worker.jobs.sites import option_match


def _select_by_label(attempt: Attempt) -> str | None:
    options = [str(value) for value in attempt.field.get("options", [])]
    matched = option_match(options, str(attempt.fill.value))
    if matched is None:
        raise NoMatchingOption("no unambiguous select option")
    attempt.control.select_option(label=matched, timeout=CONTROL_TIMEOUT_MS)
    return matched


def _select_by_value(attempt: Attempt) -> str | None:
    attempt.control.select_option(value=attempt.fill.value, timeout=CONTROL_TIMEOUT_MS)
    return attempt.fill.value


def _select_multiple(attempt: Attempt) -> str | None:
    options = [str(value) for value in attempt.field.get("options", [])]
    value = str(attempt.fill.value)
    parts = [
        part.strip() for part in value.split(MULTI if MULTI in value else ",") if part.strip()
    ]
    matched = [option_match(options, part) for part in parts]
    chosen = [option for option in matched if option is not None]
    if not parts or len(chosen) != len(parts):
        raise NoMatchingOption("no unambiguous select options")
    attempt.control.select_option(label=chosen, timeout=CONTROL_TIMEOUT_MS)
    return MULTI.join(chosen)


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


def _check_control(attempt: Attempt) -> str | None:
    if captcha_challenge_present(attempt.page):
        raise CaptchaPresent("choice left manual while a captcha challenge is open")
    set_check_state(attempt.control, True)
    return attempt.fill.value


def _uncheck_control(attempt: Attempt) -> str | None:
    if captcha_challenge_present(attempt.page):
        raise CaptchaPresent("choice left manual while a captcha challenge is open")
    set_check_state(attempt.control, False)
    return None


def set_check_state(control: Any, checked: bool) -> None:
    """A styled option's own label can cover the input; keyboard events still work."""
    try:
        if checked:
            control.check(timeout=CONTROL_TIMEOUT_MS)
        else:
            control.uncheck(timeout=CONTROL_TIMEOUT_MS)
    except Error:
        if control.is_checked(timeout=CONTROL_TIMEOUT_MS) == checked:
            return
        control.focus(timeout=CONTROL_TIMEOUT_MS)
        if not control.evaluate("e => e.getRootNode().activeElement === e"):
            raise RuntimeError("choice input could not be focused") from None
        control.press("Space", timeout=CONTROL_TIMEOUT_MS)
        if control.is_checked(timeout=CONTROL_TIMEOUT_MS) != checked:
            raise RuntimeError("choice did not retain its checked state") from None


def _set_value(attempt: Attempt) -> str | None:
    set_registered_value(attempt.control, str(attempt.fill.value))
    return attempt.fill.value

def set_registered_value(control: Any, value: str) -> None:
    # Some controlled ATS inputs display a programmatic value without updating
    # form state until the control receives a real focus/blur interaction.
    control.focus(timeout=CONTROL_TIMEOUT_MS)
    control.evaluate(
        timeout=CONTROL_TIMEOUT_MS,
        expression=load("set_value"),
        arg=value,
    )
    control.click(timeout=CONTROL_TIMEOUT_MS)
    control.press("Tab", timeout=CONTROL_TIMEOUT_MS)
