"""Submit pressed by this device's agent, with no Playwright session attached at the press.

Experiment 1 of the device-submit plan (`device/press.py`'s `cdp_click`) passed
on Lever where Playwright's click was refused by its invisible hCaptcha. So
when an `os hunt apply --submit` run is linked to the agent, the posting's host
is in `DEVICE_PRESS_HOSTS`, the button is in the page's own document, and the
agent says it would press (`press_ready`: the drive switch on, no lease, the
listing unlocked), the run does not click. It names the button by a selector
(its id, or a mark it sets), scrolls it into view, lets go of Chrome entirely,
and asks the agent to `press` that tab. The agent's `press_outcome` decides
the run (`jobs/submit.py`), and the run connects again to read the tab.

Anything that fails before the request is sent leaves the press to Playwright
as before. Once it is sent, nothing here ever clicks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error, Locator, Page
from rich.markup import escape

from personal_os_worker.core.config import console
from personal_os_worker.device.drive_state import DEVICE_PRESS_HOSTS, url_host
from personal_os_worker.jobs.browser.tab import cdp_target_id

if TYPE_CHECKING:
    from personal_os_worker.jobs.browser import VisibleBrowser
    from personal_os_worker.jobs.remote import RunLink

READY_SECONDS = 10.0
PRESS_SECONDS = 180.0
EXPERIMENT = 1
MARK = "data-personal-os-press"
_PLAIN_ID = re.compile(r"^[A-Za-z][\w-]{0,80}$")


class ListingLocked(RuntimeError):
    """The agent holds this listing locked: it may have gone in, so nothing presses it."""

    def __init__(self, state: str) -> None:
        super().__init__(f"the device holds this listing locked ({state})")
        self.state = state


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    """The tab and the button the agent is to press, named before the run lets go."""

    listing_id: str
    url: str
    target: str
    selector: str


def device_target(
    link: RunLink | None, listing_id: str, page: Page, button: Locator
) -> DeviceTarget | None:
    """The press to hand the agent, or None to press with Playwright as before.

    Raises `ListingLocked` when the agent says the listing is locked.
    """
    if link is None or not listing_id or url_host(page.url) not in DEVICE_PRESS_HOSTS:
        return None
    try:
        if not button.evaluate("el => el.ownerDocument.defaultView === window.top"):
            return None
        target = cdp_target_id(page)
        if target is None:
            return None
        url = page.url
        answer = link.request({"type": "press_ready", "listing_id": listing_id, "url": url},
                              "press_ready", timeout=READY_SECONDS)
        if answer is not None and answer.get("locked"):
            raise ListingLocked(str(answer["locked"]))
        if answer is None or answer.get("ok") is not True:
            reason = answer.get("reason") if answer else "the agent did not answer"
            console.print(f"  [dim]not pressing through the device: {escape(str(reason))}[/dim]")
            return None
        selector = _selector(page, button)
        button.scroll_into_view_if_needed(timeout=5_000)
    except Error:
        return None
    return DeviceTarget(listing_id, url, target, selector)


def _selector(page: Page, button: Locator) -> str:
    """A selector naming only this button: its id when that is plain and unique, else a mark."""
    ident = button.get_attribute("id") or ""
    if _PLAIN_ID.fullmatch(ident) and page.locator(f"#{ident}").count() == 1:
        return f"#{ident}"
    button.evaluate(f"el => el.setAttribute('{MARK}', '')")
    return f"[{MARK}]"


def press_through_device(
    link: RunLink,
    browser: VisibleBrowser,
    pressed: DeviceTarget,
    *,
    fill_seconds: float,
    console_errors: list[str] | None = None,
) -> tuple[Page | None, dict[str, Any] | None]:
    """Let go of Chrome, have the agent press, then take the tab back.

    The agent's `press_outcome`, or None when it never answered; the tab is
    None when it could not be found again.
    """
    console.print(f"  [bold]pressing Submit through the device (experiment {EXPERIMENT})[/bold]")
    browser.let_go()
    answer: dict[str, Any] | None = None
    page: Page | None = None
    try:
        answer = link.request({
            "type": "press", "listing_id": pressed.listing_id, "url": pressed.url,
            "target": pressed.target, "selector": pressed.selector, "experiment": EXPERIMENT,
            "fill_seconds": round(fill_seconds, 1),
        }, "press_outcome", timeout=PRESS_SECONDS)
    finally:
        try:
            page = browser.take_back(pressed.target, console_errors)
        except Exception as error:  # the outcome stands; the tab is only for reading
            console.print(f"  [dim]could not reconnect to the tab: {escape(str(error))}[/dim]")
    return page, answer
