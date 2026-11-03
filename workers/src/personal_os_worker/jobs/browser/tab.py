"""The tab: closed-browser detection, text, and identity."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright


class BrowserClosedError(RuntimeError):
    """The connected browser or its last usable application tab was closed."""


def is_browser_closed_error(page: Page, error: Error) -> bool:
    """Whether a Playwright operation failed because its page or browser closed."""
    try:
        if page.is_closed():
            return True
    except Error:
        return True
    message = str(error).casefold()
    return (
        type(error).__name__ == "TargetClosedError"
        or "target page, context or browser has been closed" in message
    )


# A single-page app answers `domcontentloaded` with an empty root, so the text is
# polled rather than read once; a quarter second between looks, a second of the
# same text to call it rendered.
POLL_SECONDS = 0.25
SETTLE_SECONDS = 1.0


def watch_console(page: Page, console_errors: list[str]) -> None:
    """Collect the page's console errors into the run's evidence."""
    page.on(
        "console",
        lambda message: console_errors.append(message.text[:2000])
        if message.type == "error"
        else None,
    )


def rendered_body_text(
    page: Page,
    *,
    timeout_ms: int = 30_000,
    accept: Callable[[str], bool] | None = None,
    limit: int = 100_000,
) -> str:
    """The page's visible text, once it has one.

    Workday and every other single-page board serve `<div id="root"></div>` and
    fill it from script, so text read straight after a navigation is empty and
    anything judged from it — which company this is, what the posting says — is
    judged from nothing. Return as soon as `accept` recognizes the page, else
    once the text has stopped changing, else whatever is there at the deadline.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    previous, settled_for = "", 0.0
    while True:
        text = _all_frames_text(page)[:limit]
        if accept is not None and accept(text):
            return text
        if text.strip() and text == previous:
            settled_for += POLL_SECONDS
            if settled_for >= SETTLE_SECONDS:
                return text
        else:
            settled_for = 0.0
        previous = text
        if time.monotonic() >= deadline:
            return text
        page.wait_for_timeout(int(POLL_SECONDS * 1000))


def _all_frames_text(page: Page) -> str:
    """The text of the page and every frame in it.

    iCIMS serves the posting and its form inside an iframe; the page itself
    says only "Skip Branding", so the top document alone never names the role.
    """
    parts: list[str] = []
    for frame in page.frames:
        try:
            parts.append(frame.locator("body").inner_text(timeout=5_000))
        except Error:
            continue
    return "\n".join(part for part in parts if part.strip())


def identifies_posting(text: str, *, company: str, title: str) -> bool:
    """Whether the live page visibly names the company or enough of the role."""
    body = text.casefold()
    tokens = {
        token.casefold().strip(".,/()-")
        for token in title.split()
        if len(token.strip(".,/()-")) >= 5
    }
    visible = {token for token in tokens if token in body}
    return company.casefold() in body or len(visible) >= min(2, len(tokens))


def tab_continues(tab_url: str, application_url: str) -> bool:
    """Whether an open tab is this application, so a re-run can pick it up there.

    Every board keeps the posting in the path once the form opens — Workday adds
    `/apply/...`, Ashby and Lever keep their posting id — so the application URL
    is a prefix of wherever the tab has reached. Query and fragment carry the
    referrer and the step, never the identity.
    """
    tab, wanted = urlsplit(tab_url), urlsplit(application_url)
    if tab.scheme not in ("http", "https") or (tab.netloc, tab.scheme) != (
        wanted.netloc,
        wanted.scheme,
    ):
        return False
    here, there = tab.path.rstrip("/"), wanted.path.rstrip("/")
    return here == there or here.startswith(f"{there}/")


def cdp_target_id(page: Page) -> str | None:
    """The CDP target id of a page: how another process connected to the same
    browser finds this tab."""
    try:
        session = page.context.new_cdp_session(page)
        try:
            info = session.send("Target.getTargetInfo")
        finally:
            session.detach()
        return str(info["targetInfo"]["targetId"])
    except (Error, KeyError, TypeError):
        return None


def registrable_site(url: str) -> str:
    """The registrable part of a URL's host, near enough: its last two labels."""
    host = (urlsplit(url).hostname or "").casefold()
    return ".".join(host.split(".")[-2:])


def _same_application(page: Page, current: Page) -> bool:
    if page == current:
        return True
    try:
        opener = page.opener()
    except Error:
        opener = None
    if opener is not None and opener == current:
        return True
    site = registrable_site(page.url)
    return bool(site) and site == registrable_site(current.url)


class VisibleBrowser:
    def __init__(self, cdp_url: str) -> None:
        self._cdp_url = cdp_url
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> VisibleBrowser:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
        return self

    def __exit__(self, *_: object) -> None:
        if self._playwright is not None:
            self._playwright.stop()

    def capture_application(self, url: str) -> dict[str, Any]:
        from personal_os_worker.jobs.browser.capture import inspect_fields
        from personal_os_worker.jobs.browser.transitions import reveal_application_form

        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        context = self._browser.contexts[0]
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        reveal_application_form(page)
        result = {
            "url": page.url,
            "title": page.title(),
            "description": page.locator("body").inner_text(timeout=15_000)[:50000],
            "fields": inspect_fields(page),
        }
        # Deliberately leave the company page open in the dedicated visible profile.
        return result

    def active_page(self, current_page: Page | None = None) -> Page:
        """The tab currently in the foreground of the automation browser.

        Playwright exposes no notion of an active tab, but only the foreground
        tab of a focused window reports a visible document, so ask the pages
        themselves. Falls back to the most recently opened tab when the window is
        unfocused and every tab reports hidden. A closed current page ends its
        owning session even when an unrelated browser tab remains open.
        """
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        try:
            if current_page is not None and current_page.is_closed():
                raise BrowserClosedError("The application tab was closed")
            contexts = self._browser.contexts
            if not contexts:
                raise BrowserClosedError("The automation browser was closed")
            pages = [
                page
                for page in contexts[0].pages
                if not page.url.startswith(("chrome://", "devtools://", "about:"))
            ]
        except Error as error:
            raise BrowserClosedError("The automation browser was closed") from error
        if not pages:
            raise BrowserClosedError("The application tab was closed")
        if current_page is not None:
            # Follow only tabs that belong to this application: one it opened,
            # or one on the same site. An extension's "what's new" tab, or any
            # other tab that takes focus, is not the application.
            pages = [page for page in pages if _same_application(page, current_page)]
        for page in reversed(pages):
            try:
                if page.evaluate("document.visibilityState") == "visible":
                    return page
            except Error:
                continue
        return current_page if current_page is not None else pages[-1]

    def isolated_page(self, url: str, *, guard: Callable[[Page], None]) -> Page:
        """A page in a new browser context: no shared cookies or drafts (R22).

        The guard is installed before the page loads anything; the context closes
        with `page.context.close()`.
        """
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        page = self._browser.new_context().new_page()
        guard(page)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        return page

    def open_page(
        self,
        url: str,
        *,
        console_errors: list[str] | None = None,
        guard: Callable[[Page], None] | None = None,
    ) -> Page:
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        page = self._browser.contexts[0].new_page()
        if guard is not None:
            guard(page)
        if console_errors is not None:
            watch_console(page, console_errors)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        return page

    def application_page(
        self,
        url: str,
        *,
        console_errors: list[str] | None = None,
        fresh: bool = False,
    ) -> tuple[Page, bool]:
        """The tab this application is already on, else a new one; True when reused.

        A tab left open by an earlier run still holds everything typed into it,
        so re-running the same listing — after a code change, or after stopping
        — picks the form up where it stands instead of navigating away from it.
        `fresh` always opens a new tab.
        """
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        if not fresh:
            existing = self.tab_on(url)
            if existing is not None:
                if console_errors is not None:
                    watch_console(existing, console_errors)
                with contextlib.suppress(Error):
                    existing.bring_to_front()
                return existing, True
        return self.open_page(url, console_errors=console_errors), False

    def tab_on(self, url: str) -> Page | None:
        """The most recently opened tab that is this application, if one is open."""
        if self._browser is None:
            return None
        try:
            contexts = self._browser.contexts
            pages = list(contexts[0].pages) if contexts else []
        except Error:
            return None
        for page in reversed(pages):
            try:
                if not page.is_closed() and tab_continues(page.url, url):
                    return page
            except Error:
                continue
        return None
