"""Strategies: the named ways of operating each control kind, in candidate order.

This module is data. The operations themselves live in this package's family modules
(`OPERATIONS`); this table decides which are
candidates for a fill and in what order. The order mirrors the seeded
`jobs_strategies` precedence. A kind's candidates are tried in turn until one
operates; each attempt is recorded as a fill event.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.core.page_scripts import load
from personal_os_worker.jobs.autofill import FILE_KINDS, FormFill
from personal_os_worker.jobs.registry.kinds import BUTTON_GROUP

# Playwright auto-waits 30s for a control before an action fails; a control that
# vanished because the page moved on (the user submitted) must fail in seconds.
CONTROL_TIMEOUT_MS = 4_000


class NoMatchingOption(RuntimeError):
    """The value has no unambiguous counterpart among the control's options."""


class CaptchaPresent(RuntimeError):
    """An interactive captcha is on the page, so the control is left to the user."""


# An open captcha challenge: the image or puzzle frame a widget shows once it
# is clicked or decides to test you. Only then does clicking anywhere else risk
# dismissing or disturbing it, so only then are choices left to you (R64). A
# checkbox widget waiting for you, an invisible-mode badge, or a hidden enclave
# frame does not count, and nothing here ever solves a captcha.
CAPTCHA_SCRIPT = load("captcha_challenge")


def captcha_challenge_present(page: Page) -> bool:
    """Whether a challenge is open in any frame: iCIMS's opens inside its own."""
    for frame in page.frames:
        try:
            if frame.evaluate(CAPTCHA_SCRIPT):
                return True
        except Error:
            continue
    return False


@dataclass(frozen=True, slots=True)
class Attempt:
    page: Page
    control: Any
    field: dict[str, Any]
    fill: FormFill
    document: Path | None


CANDIDATES: dict[str, tuple[str, ...]] = {
    "file": ("upload_file",),
    "native_select": ("select_by_label", "select_by_value"),
    "multi_choice_group": ("select_multiple", "check_control", "uncheck_control"),
    "dynamic_choice": ("combobox_choose_from_menu", "combobox_type_and_select"),
    "choice_group": ("click_option_button", "check_control"),
    "toggle": ("check_control", "uncheck_control"),
    "text_entry": ("set_value", "type_and_pick_suggestion"),
}

Applies = Callable[[dict[str, Any], FormFill, bool], bool]


def _type(field: dict[str, Any]) -> str:
    return str(field.get("type") or "").casefold()


def _is_select(field: dict[str, Any]) -> bool:
    return str(field.get("tag") or "").casefold() == "select"


APPLIES: dict[str, Applies] = {
    "upload_file": lambda field, fill, has_document: fill.kind in FILE_KINDS and has_document,
    "select_by_label": lambda field, fill, _: _is_select(field) and fill.value is not None,
    "select_by_value": lambda field, fill, _: _is_select(field) and fill.value is not None,
    "select_multiple": lambda field, fill, _: (
        _is_select(field) and field.get("multiple") is True and fill.value is not None
    ),
    "combobox_choose_from_menu": lambda field, fill, _: fill.chooser is not None,
    "combobox_type_and_select": lambda field, fill, _: (
        fill.chooser is None and fill.value is not None
    ),
    "click_option_button": lambda field, fill, _: (
        _type(field) == BUTTON_GROUP and fill.value is not None
    ),
    "check_control": lambda field, fill, _: (
        _type(field) in {"radio", "checkbox"} and fill.value is not None and not fill.clear
    ),
    "uncheck_control": lambda field, fill, _: _type(field) == "checkbox" and fill.clear,
    "set_value": lambda field, fill, _: fill.value is not None and not _is_select(field),
    "type_and_pick_suggestion": lambda field, fill, _: (
        fill.value is not None
        and not _is_select(field)
        and _type(field) not in {"radio", "checkbox"}
    ),
}


def candidates(
    kind: str, field: dict[str, Any], fill: FormFill, *, has_document: bool
) -> list[str]:
    """The strategies to try for this fill, in order; empty when none applies."""
    return [
        name for name in CANDIDATES.get(kind, ()) if APPLIES[name](field, fill, has_document)
    ]


from personal_os_worker.jobs.registry.strategies import (  # noqa: E402
    combobox,
    native,
    upload,
)

# Every strategy in the registry, by name. `CANDIDATES` decides which are
# candidates for a fill and in what order.
OPERATIONS: dict[str, Callable[[Attempt], str | None]] = {
    "upload_file": upload._upload_file,
    "type_and_pick_suggestion": combobox._type_and_pick_suggestion,
    "select_by_label": native._select_by_label,
    "select_by_value": native._select_by_value,
    "select_multiple": native._select_multiple,
    "combobox_choose_from_menu": combobox._combobox_choose_from_menu,
    "combobox_type_and_select": combobox._combobox_type_and_select,
    "click_option_button": native._click_option_button,
    "check_control": native._check_control,
    "uncheck_control": native._uncheck_control,
    "set_value": native._set_value,
}
