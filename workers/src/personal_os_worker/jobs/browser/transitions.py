"""Transitions: submit controls, reveals, and outlines."""

from __future__ import annotations

import contextlib
import re
import time
from typing import Any, Literal

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.page_scripts import load
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.registry.strategies import (
    CONTROL_TIMEOUT_MS,
)


def _distinct_destinations(candidates: list[Any]) -> list[Any]:
    """Collapse several links to the same href into the first of them."""
    hrefs: set[str | None] = set()
    for candidate in candidates:
        try:
            hrefs.add(candidate.get_attribute("href"))
        except Error:
            return candidates
    if None not in hrefs and len(hrefs) == 1:
        return candidates[:1]
    return candidates


TRANSITION_ROLES: tuple[Literal["link", "button"], ...] = ("link", "button")


def click_safe_transition(page: Page, *, kind: str) -> str | None:
    """Click one unambiguous non-submission navigation control."""
    patterns = {
        "apply": re.compile(
            r"^\s*(?:apply(?: for this (?:job|position|role))?(?: online| now)?"
            r"|start application)\s*$",
            re.I,
        ),
        "next": re.compile(r"^\s*(?:next|continue|save and continue)\s*$", re.I),
    }
    pattern = patterns.get(kind)
    if pattern is None:
        raise ValueError(f"Unknown transition kind: {kind}")
    if kind == "next" and page.locator(
        'iframe[src*="captcha" i], [data-sitekey], [class*="captcha" i]'
    ).count():
        return None

    # iCIMS serves the posting, Apply link included, inside an iframe.
    for role, frame in ((role, frame) for frame in page.frames for role in TRANSITION_ROLES):
        candidates: list[Any] = []
        try:
            locator = frame.get_by_role(role, name=pattern)
            count = locator.count()
        except Error:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    candidates.append(candidate)
            except Error:
                continue
        if role == "link" and len(candidates) > 1:
            # Lever and others repeat the same Apply link at the top and bottom
            # of the posting; several links to the identical destination are
            # one control, not an ambiguous choice.
            candidates = _distinct_destinations(candidates)
        if role == "button" and len(candidates) == 2 and len({
            str(candidate.get_attribute("aria-label") or candidate.inner_text()).strip().casefold()
            for candidate in candidates
        }) == 1:
            # ADP repeats an identical Apply button above and below the posting.
            # Two with the same name are one action; three or more, or two with
            # different names, may be different roles and stay ambiguous.
            candidates = candidates[:1]
        if len(candidates) > 1:
            return None
        if len(candidates) == 1:
            candidate = candidates[0]
            label = str(candidate.get_attribute("aria-label") or candidate.inner_text()).strip()
            candidate.click(timeout=5_000)
            return label or kind.title()
    return None


SUBMIT_CONTROL = re.compile(
    r"^\s*submit\s*$|\b(?:submit|send|complete|finish)\b.*\b(?:application|form)\b",
    re.IGNORECASE,
)


def has_submit_control(page: Page) -> bool:
    """Whether the current page visibly offers the user's final submit action."""
    for frame in page.frames:
        locator = frame.get_by_role("button", name=SUBMIT_CONTROL)
        for index in range(locator.count()):
            try:
                if locator.nth(index).is_visible():
                    return True
            except Error:
                continue
    return False


def reveal_application_form(page: Page, *, timeout_ms: int = 15_000) -> bool:
    """Click through to the form when a posting hides it behind an Apply step.

    Ashby and several other boards render the posting and the application as
    separate views, so the job page has no controls at all no matter how long it
    is given. Waiting longer never helps; the form has to be asked for.
    Returns whether controls are present once this has run.
    """
    from personal_os_worker.jobs.browser.capture import (
        inspect_fields,
        looks_like_application_form,
    )
    if looks_like_application_form(inspect_fields(page)):
        return True
    try:
        clicked = click_safe_transition(page, kind="apply")
    except Error:
        return False
    if clicked is None:
        return False
    # The form may load in the page or in a frame (iCIMS), so poll for it.
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            if looks_like_application_form(inspect_fields(page)):
                return True
            page.wait_for_timeout(500)
        except Error:
            return False
    return False
CLEAR_OUTLINES_SCRIPT = load("clear_outlines")
# A control nobody can see (a hidden input styled over, a file in a closed
# panel) is outlined by the nearest part of the field that shows.
OUTLINE_SCRIPT = load("outline")


def highlight_unfilled(page: Page, fields: list[dict[str, Any]], questions: set[str]) -> None:
    """Outline exactly the questions left for you, and nothing the audit did not list.

    A choice is outlined option by option. Emptiness is the audit's call, never
    the page's: JazzHR names each Yes/No box apart, so a box can be unchecked
    on a question that is answered.
    """
    from personal_os_worker.jobs.browser.readback import fresh_control
    for frame in page.frames:
        with contextlib.suppress(Error):
            frame.evaluate(CLEAR_OUTLINES_SCRIPT)
    for field in fields:
        if question_of(field) not in questions:
            continue
        with contextlib.suppress(Error, RuntimeError):
            fresh_control(page, field).evaluate(OUTLINE_SCRIPT, timeout=CONTROL_TIMEOUT_MS)
