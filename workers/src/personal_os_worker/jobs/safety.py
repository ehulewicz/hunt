"""What keeps the worker from ever submitting an application, in one protected place.

R23: a stray submit is the one irreversible mistake, so it is refused by
mechanisms that do not depend on each other. The page script cancels every form
submission and every click on a control labelled as submitting; the network
guard aborts any non-GET request that names a submission; and the worker
refuses to operate a control that reads as submitting.

A repair or validation page is guarded from before it loads (`install_guards`).
Your own application tab is guarded only while the worker operates it
(`armed`): the script stays in place but acts only while armed, and the network
guard is routed for the operation and removed after it, so your Submit works
the moment the worker stops.

The fixer (`jobs/fix/`) may never change this module: a model-written fix that
touches it is refused before it is tested.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from playwright.sync_api import Error, Page, Route

from personal_os_worker.jobs.page_scripts import load

SUBMITTING = re.compile(
    r"^\s*submit\b|\bsubmit\s+(?:my\s+|your\s+)?application\b|\bsend\s+application\b"
    r"|\bapply\s+now\b|\bfinish\s+application\b",
    re.IGNORECASE,
)
# The same words as `SUBMITTING`, as JavaScript. `ARMED` names the condition the
# guard acts on: always for a guarded repair page, only while armed on yours.
_GUARD_TEMPLATE = load("submit_guard")
GUARD_SCRIPT = _GUARD_TEMPLATE.replace("ARMED", "true")
ARMABLE_GUARD_SCRIPT = _GUARD_TEMPLATE.replace("ARMED", "window.__personalOsArmed === true")
_ARMABLE_PAGES = "__personal_os_armable"


def names_a_submission(method: str, url: str, body: str | None) -> bool:
    """Whether a request would send an application, judged by method, path, and body."""
    if method.upper() == "GET":
        return False
    text = f"{url} {body or ''}".casefold()
    return "submit" in text or ("/apply" in text and method.upper() == "POST")


def _refuse(route: Route) -> None:
    request = route.request
    try:
        body = request.post_data
    except (UnicodeDecodeError, ValueError):
        body = None
    if names_a_submission(request.method, request.url, body):
        route.abort("blockedbyclient")
    else:
        route.continue_()


def install_guards(page: Page) -> None:
    """Both guards, always on, before the page loads anything."""
    page.add_init_script(GUARD_SCRIPT)
    page.route("**/*", _refuse)


def guarded(page: Any) -> bool:
    """Whether the page script is in place (both run before any page script)."""
    try:
        return bool(page.evaluate("() => window.__personalOsSubmitGuard === true"))
    except Exception:
        return False


def _set_armed(page: Page, value: bool) -> None:
    script = f"() => {{ window.__personalOsArmed = {'true' if value else 'false'}; }}"
    for frame in page.frames:
        try:
            if value:
                frame.evaluate(ARMABLE_GUARD_SCRIPT)
            frame.evaluate(script)
        except Error:
            continue


@contextmanager
def armed(page: Page) -> Iterator[None]:
    """Guard your tab for one automated operation, then hand it back unguarded.

    The script is installed for every document the tab loads from now on and
    in every frame already open; it does nothing until armed. The network
    guard exists only inside this block, because a route on your tab would hold
    every request while the worker is not answering Playwright events.
    """
    if not getattr(page, _ARMABLE_PAGES, False):
        page.add_init_script(ARMABLE_GUARD_SCRIPT)
        setattr(page, _ARMABLE_PAGES, True)
    _set_armed(page, True)
    page.route("**/*", _refuse)
    try:
        yield
    finally:
        with suppress(Error):
            page.unroute("**/*", _refuse)
        _set_armed(page, False)


def refuse_submit(field: dict[str, Any]) -> None:
    """The worker's own refusal: never operate a control that reads as submitting."""
    label = str(field.get("label") or "") + " " + str(field.get("value") or "")
    if str(field.get("type") or "").casefold() == "submit" or SUBMITTING.search(label):
        raise RuntimeError("refusing to operate a submit control")
