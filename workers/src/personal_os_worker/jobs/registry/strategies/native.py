"""Native-control strategies: selects, checkboxes, and plain value writes."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Error

from personal_os_worker.jobs.autofill import MULTI
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
        expression="""
        (element, value) => {
          const proto = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
          if (setter) setter.call(element, value); else element.value = value;
          element.dispatchEvent(new Event('input', {bubbles: true}));
          element.dispatchEvent(new Event('change', {bubbles: true}));
        }
        """,
        arg=value,
    )
    control.click(timeout=CONTROL_TIMEOUT_MS)
    control.press("Tab", timeout=CONTROL_TIMEOUT_MS)
