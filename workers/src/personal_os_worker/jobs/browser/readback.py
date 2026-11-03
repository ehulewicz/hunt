"""Reading controls back: verification, snapshots, audit."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.autofill import (
    MULTI,
    choice_group_key,
    is_sensitive_field,
)
from personal_os_worker.jobs.registry.events import form_of
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.registry.strategies import (
    CONTROL_TIMEOUT_MS,
)
from personal_os_worker.jobs.registry.strategies.combobox import (
    combobox_shows,
    combobox_value,
)
from personal_os_worker.jobs.registry.strategies.native import (
    BUTTON_GROUP_XPATH,
    _button_group,
    button_choice,
)
from personal_os_worker.jobs.registry.strategies.upload import _verify_upload
from personal_os_worker.jobs.sites import option_match


def fresh_control(page: Page, field: dict[str, Any]) -> Any:
    from personal_os_worker.jobs.browser.tab import registrable_site
    frame_index = int(field.get("frame_index") or 0)
    if frame_index >= len(page.frames):
        raise RuntimeError("field frame is no longer present")
    frame = page.frames[frame_index]
    selector = str(field.get("selector") or "")
    if selector:
        locator = frame.locator(selector)
        if locator.count() == 1:
            return locator
    if field.get("custom"):
        from personal_os_worker.jobs.registry.kinds import learned_kind
        from personal_os_worker.jobs.registry.kinds.custom import load

        kind = load().get(str(field["custom"])) or learned_kind(
            str(field["custom"]), registrable_site(page.url)
        )
        if kind is None:
            raise RuntimeError(f"no custom kind {field['custom']!r} is registered")
        widgets = frame.locator(kind.selector)
        position = int(field.get("custom_index") or 0)
        if position >= widgets.count():
            raise RuntimeError("control is no longer present")
        return widgets.nth(position)
    controls = frame.locator("input, textarea, select, [role=combobox]")
    position = int(field.get("control_index", field["index"]))
    if position >= controls.count():
        raise RuntimeError("control is no longer present")
    return controls.nth(position)






def verify_control(
    page: Page,
    field: dict[str, Any],
    value: str | None,
    *,
    resume: bool = False,
    filename: str | None = None,
) -> bool:
    from personal_os_worker.jobs.browser.tab import registrable_site
    if resume:
        return _verify_upload(page, field, filename)
    control = fresh_control(page, field)
    try:
        if field.get("custom"):
            from personal_os_worker.jobs.registry.kinds import learned_kind
            from personal_os_worker.jobs.registry.kinds.custom import load, read

            kind = load().get(str(field["custom"])) or learned_kind(
                str(field["custom"]), registrable_site(page.url)
            )
            if kind is None:
                return False
            held = str(read(control, kind).get("value") or "")
            expected = str(value or "")
            return same_entry(held, expected) or option_match([held], expected) is not None
        if field.get("type") == "yesno":
            buttons, texts = _button_group(control)
            chosen = button_choice(texts, str(value or ""))
            if chosen is None:
                return False
            return bool(buttons.nth(texts.index(chosen)).get_attribute("aria-pressed") == "true")
        if field.get("tag") == "select" and field.get("multiple") is True:
            selected = control.evaluate(
                "element => [...element.selectedOptions].map(o => o.textContent.trim())",
                timeout=CONTROL_TIMEOUT_MS,
            )
            text = str(value or "")
            wanted = {
                part.strip() for part in text.split(MULTI if MULTI in text else ",") if part.strip()
            }
            return bool(wanted) and wanted <= set(selected)
        if field.get("type") in {"radio", "checkbox"}:
            return bool(control.is_checked(timeout=CONTROL_TIMEOUT_MS))
        if field.get("tag") == "select" and value is not None:
            # The option's text is the answer; its value attribute is often an id.
            shown = str(control.evaluate(
                "element => element.selectedOptions[0]?.textContent?.trim() || ''",
                timeout=CONTROL_TIMEOUT_MS,
            ))
            chosen = str(control.input_value(timeout=CONTROL_TIMEOUT_MS)).strip()
            # select_by_value reports the value attribute it set, not the text.
            return bool(chosen) and (same_entry(shown, value) or same_entry(chosen, value))
        observed = str(control.input_value(timeout=CONTROL_TIMEOUT_MS)).strip()
        if field.get("role") == "combobox":
            return combobox_shows(control, str(value or ""))
        return same_entry(observed, str(value or ""))
    except Error:
        return False


PHONE_LIKE = re.compile(r"^[\d\s()+.\-]{7,}$")


def same_entry(shown: str, expected: str) -> bool:
    """Whether the control holds the value, allowing for how widgets reformat it.

    A phone input masks `5550100199` as `(555) 010-0199` or `+1 555-010-0199`:
    the digits are the value, and a country code of up to three digits may lead.
    """
    shown, expected = shown.strip(), expected.strip()
    if shown == expected or " ".join(shown.casefold().split()) == " ".join(
        expected.casefold().split()
    ):
        return True
    if PHONE_LIKE.match(expected) and PHONE_LIKE.match(shown):
        want, got = re.sub(r"\D", "", expected), re.sub(r"\D", "", shown)
        return len(want) >= 7 and got.endswith(want) and len(got) - len(want) <= 3
    return False
def snapshot_entries(page: Page, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read controls separately; empty values are explicit, failed reads omitted."""
    entries: list[dict[str, Any]] = []
    for index, field in enumerate(fields):
        field_type = str(field.get("type") or "").casefold()
        tag = str(field.get("tag") or "").casefold()
        if field_type in {"file", "submit", "button", "reset", "hidden"} or tag == "button":
            continue
        label = str(field.get("label") or "").strip()
        group = str(field.get("group") or "").strip()
        try:
            control = fresh_control(page, field)
            if field_type == "yesno":
                pressed = control.locator(BUTTON_GROUP_XPATH).locator(
                    'button[aria-pressed="true"]'
                )
                count = pressed.count()
                if count > 1:
                    continue
                question = label
                value = str(pressed.first.inner_text()) if count else ""
            elif field_type in {"radio", "checkbox"}:
                question = group or label
                value = (
                    (label or str(field.get("value") or ""))
                    if control.is_checked(timeout=CONTROL_TIMEOUT_MS) else ""
                )
            elif tag == "select":
                question = label
                value = str(
                    control.evaluate(
                        "element => element.value ? "
                        "(element.selectedOptions[0]?.textContent?.trim() || '') : ''",
                        timeout=CONTROL_TIMEOUT_MS,
                    )
                )
            elif field.get("role") == "combobox":
                question = label
                value = combobox_value(control)
            else:
                question = label
                value = str(control.input_value(timeout=CONTROL_TIMEOUT_MS))
        except (Error, RuntimeError):
            continue
        question = question.strip()
        value = value.strip()
        if not question:
            continue
        entries.append({
            "question": question,
            "answer_text": value,
            "control_kind": field_type or tag,
            "form": form_of(field),
            "field_fingerprint": (
                f"{field.get('fingerprint') or ''}#{field.get('index', index)}"
            ),
        })
    return entries


def snapshot_values(page: Page, fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the legacy question-keyed view of the current control snapshot."""
    snapshot: dict[str, dict[str, Any]] = {}
    for entry in snapshot_entries(page, fields):
        question = str(entry["question"])
        value = str(entry["answer_text"])
        if not value:
            continue
        if entry["control_kind"] == "checkbox" and question in snapshot:
            value = f"{snapshot[question]['answer_text']}, {value}"
        snapshot[question] = {
            "question": question,
            "answer_text": value,
            "control_kind": entry["control_kind"],
        }
    return snapshot


def audit_filled_form(
    fields: list[dict[str, str | bool | None]],
) -> dict[str, list[str]]:
    """Which questions a form has answered, and which are still open."""
    filled: list[str] = []
    required_unfilled: list[str] = []
    optional_unfilled: list[str] = []
    # A choice group is one question: it is filled once any option is checked,
    # and it is reported by the group question rather than each option's label.
    answered_groups: set[str] = set()
    pending_groups: list[tuple[str, str]] = []
    optional_groups: list[tuple[str, str]] = []
    for field in fields:
        field_type = str(field.get("type") or "").casefold()
        if field_type in {"submit", "button", "reset", "hidden"}:
            continue
        group = str(field.get("group") or "").strip()
        is_choice = field_type == "radio" or (field_type == "checkbox" and bool(group))
        label = question_of(field) if is_choice else str(
            field.get("label") or field.get("name") or field.get("index") or "field"
        )
        # Two questions worded alike stay two groups (choice_group_key).
        key = choice_group_key(field) if is_choice else label
        if field.get("filled") is True:
            if is_choice:
                if key in answered_groups:
                    continue
                answered_groups.add(key)
            filled.append(label)
        elif field.get("required") is True:
            if is_choice:
                pending_groups.append((key, label))
            else:
                required_unfilled.append(label)
        elif not is_sensitive_field(field):
            if is_choice:
                optional_groups.append((key, label))
            else:
                optional_unfilled.append(label)
    required_unfilled.extend(
        dict.fromkeys(label for key, label in pending_groups if key not in answered_groups)
    )
    optional_unfilled.extend(
        dict.fromkeys(label for key, label in optional_groups if key not in answered_groups)
    )
    return {
        "filled_fields": filled,
        "required_unfilled": list(dict.fromkeys(required_unfilled)),
        "optional_unfilled": [
            label for label in dict.fromkeys(optional_unfilled) if label not in required_unfilled
        ],
    }
