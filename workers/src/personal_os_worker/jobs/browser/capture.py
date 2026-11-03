"""Field capture: the form's controls and unknown widgets."""

from __future__ import annotations

from typing import Any, cast

from playwright.sync_api import Error, Page

from personal_os_worker.jobs import page_scripts, registry
from personal_os_worker.jobs.registry.kinds.custom import QUESTION_OF


def looks_like_application_form(fields: list[dict[str, str | bool | None]]) -> bool:
    """Whether a captured page actually offers an application to fill in.

    A company careers index answers HTTP 200 and yields plenty of text, so
    without this check a listing page is indistinguishable from a real form and
    preparation reports success having accomplished nothing.
    """
    if not fields:
        return False
    if any(str(field.get("type") or "").casefold() == "file" for field in fields):
        return True
    identifying = {"name", "first", "last", "email", "phone", "resume", "cv"}
    entries = 0
    for field in fields:
        if str(field.get("tag") or "") != "input":
            continue
        if str(field.get("type") or "text").casefold() not in {"text", "email", "tel"}:
            continue
        haystack = f"{field.get('label') or ''} {field.get('name') or ''}".casefold()
        if any(token in haystack for token in identifying):
            entries += 1
    return entries >= 3


def inspect_fields(page: Page) -> list[dict[str, Any]]:
    from personal_os_worker.jobs.browser.readback import fresh_control
    from personal_os_worker.jobs.browser.tab import registrable_site
    results: list[dict[str, Any]] = []
    script = page_scripts.load("inspect_fields")
    for frame_index, frame in enumerate(page.frames):
        try:
            raw = frame.locator("input, textarea, select, [role=combobox]").evaluate_all(script)
        except Error:
            continue
        for item in cast(list[dict[str, Any]], raw):
            item["index"] = len(results)
            item["frame_index"] = frame_index
            item["frame_url"] = frame.url
            results.append(item)
    # Widgets a custom kind captures (`registry/kinds/custom.py`), after the form inputs.
    from personal_os_worker.jobs.registry.kinds import learned_kinds
    from personal_os_worker.jobs.registry.kinds.custom import capture, load

    # Built-ins first: a site's learned kinds capture only after them.
    learned = learned_kinds(registrable_site(page.url))
    for kind in [*load().values(), *learned]:
        for frame_index, frame in enumerate(page.frames):
            try:
                captured = capture(frame, kind)
            except Error:
                continue
            for item in captured:
                item["index"] = len(results)
                item["frame_index"] = frame_index
                item["frame_url"] = frame.url
                results.append(item)
            if captured and kind in learned:
                registry.report(kind.declaration, True)
    # A radio group with no question of its own asks the site's learned readers.
    from personal_os_worker.jobs.registry.kinds import learned_question

    for item in results:
        if (
            str(item.get("type") or "").casefold() == "radio"
            and not str(item.get("group") or "").strip()
        ):
            try:
                control = fresh_control(page, item)
                text, reader = learned_question(control, registrable_site(page.url))
            except (Error, RuntimeError):
                continue
            if text:
                registry.report(reader, True)
                item["group"] = text
    return results


VISIBLE_ACTIONS_SCRIPT = page_scripts.load("visible_actions")


def visible_actions(page: Page, *, limit: int = 40) -> list[str]:
    """The distinct links and buttons a page shows, in order: what a wall offered."""
    seen: dict[str, None] = {}
    for frame in page.frames:
        try:
            texts = cast(list[str], frame.evaluate(VISIBLE_ACTIONS_SCRIPT))
        except Error:
            continue
        for text in texts:
            seen.setdefault(text, None)
    return list(seen)[:limit]


# Widgets `inspect_fields` cannot see: interactive by ARIA role or by being
# editable, with no form input of their own. Site chrome (navigation, header,
# footer), captchas, hidden elements, and a combobox's own popup are not.
UNKNOWN_CONTROLS_SCRIPT = (
    "\n(customSelectors) => {\n"
    + QUESTION_OF
    + page_scripts.load("unknown_controls")
)


def inspect_unknown_controls(page: Page) -> list[dict[str, Any]]:
    """Every widget on the page that no control kind captures, with its question.

    An empty unknown control is an `unknown_control` finding: no strategy can
    operate it until code is written for it (`jobs/fix/`). A widget a custom
    kind captures is a control like any other.
    """
    from personal_os_worker.jobs.browser.tab import registrable_site
    from personal_os_worker.jobs.registry.kinds import learned_kinds
    from personal_os_worker.jobs.registry.kinds.custom import load

    selectors = [kind.selector for kind in load().values()]
    selectors += [
        kind.selector for kind in learned_kinds(registrable_site(page.url))
    ]
    results: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(page.frames):
        try:
            raw = frame.evaluate(UNKNOWN_CONTROLS_SCRIPT, selectors)
        except Error:
            continue
        for item in cast(list[dict[str, Any]], raw):
            item["frame_index"] = frame_index
            item["frame_url"] = frame.url
            item["fingerprint"] = "|".join(
                ["unknown", str(item.get("role") or ""), str(item.get("question") or "")]
            ).casefold()
            results.append(item)
    return results
