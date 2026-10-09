"""Strategies: the named ways of operating each control kind, in candidate order.

This module is data. The operations themselves live beside the browser code
that performs them (`browser.OPERATIONS`); this table decides which are
candidates for a fill and in what order. The order mirrors the seeded
`jobs_strategies` precedence. A kind's candidates are tried in turn until one
operates; each attempt is recorded as a fill event.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from personal_os_worker.jobs.autofill import FILE_KINDS, FormFill
from personal_os_worker.jobs.registry.kinds import BUTTON_GROUP

CANDIDATES: dict[str, tuple[str, ...]] = {
    "file": ("upload_file",),
    "native_select": ("select_by_label", "select_by_value"),
    "multi_choice_group": ("select_multiple", "check_control", "uncheck_control"),
    "dynamic_choice": ("combobox_choose_from_menu", "combobox_type_and_select"),
    "choice_group": ("click_option_button", "check_control"),
    "toggle": ("check_control", "uncheck_control"),
    "text_entry": ("set_value",),
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
}


def candidates(
    kind: str, field: dict[str, Any], fill: FormFill, *, has_document: bool
) -> list[str]:
    """The strategies to try for this fill, in order; empty when none applies."""
    return [
        name for name in CANDIDATES.get(kind, ()) if APPLIES[name](field, fill, has_document)
    ]
