"""Explicit browser-page states for interactive application sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error, Page

from personal_os_worker.browser import has_submit_control, looks_like_application_form


class ApplicationPageState(StrEnum):
    FORM = "form"
    OVERVIEW = "overview"
    SUBMITTED = "submitted"
    LOADING = "loading"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AshbyNavigation:
    overview_url: str
    application_url: str


STRONG_SUBMISSION = re.compile(
    r"thank(?:s| you) for applying|application (?:has been|was) submitted|"
    r"application submitted|received your application|submitted successfully|"
    r"successfully submitted",
    re.IGNORECASE,
)


def normalize_navigation_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, "", ""))


def capture_ashby_navigation(page: Page) -> AshbyNavigation | None:
    """Capture the exact Overview/Application pair for one Ashby posting."""
    found: dict[str, str] = {}
    try:
        tabs = page.get_by_role("tab")
        for index in range(tabs.count()):
            tab = tabs.nth(index)
            name = str(tab.inner_text(timeout=1_000)).strip().casefold()
            href = tab.get_attribute("href")
            if name in {"overview", "application"} and href:
                found[name] = normalize_navigation_url(urljoin(page.url, href))
    except Error:
        return None
    if set(found) != {"overview", "application"}:
        return None
    overview = urlsplit(found["overview"])
    application = urlsplit(found["application"])
    if overview.scheme not in {"http", "https"} or overview.netloc != application.netloc:
        return None
    if not overview.hostname or not overview.hostname.casefold().endswith("ashbyhq.com"):
        return None
    if application.path.rstrip("/") != f"{overview.path.rstrip('/')}/application":
        return None
    return AshbyNavigation(found["overview"], found["application"])


def _submitted(page: Page) -> bool:
    try:
        success = page.locator(
            ".ashby-application-form-success-container, [role='status'], [role='alert']"
        )
        for index in range(success.count()):
            text = success.nth(index).inner_text(timeout=1_000)
            if STRONG_SUBMISSION.search(text) or (
                re.search(r"\bsuccess\b", text, re.IGNORECASE)
                and re.search(r"\b(application|submitted)\b", text, re.IGNORECASE)
            ):
                return True
        body = page.locator("body").inner_text(timeout=2_000)
    except Error:
        return False
    return STRONG_SUBMISSION.search(body) is not None and not has_submit_control(page)


def classify_application_page(
    page: Page,
    fields: list[dict[str, str | bool | None]],
    navigation: AshbyNavigation | None = None,
) -> ApplicationPageState:
    """Classify with submission precedence over residual or invisible controls."""
    if _submitted(page):
        return ApplicationPageState.SUBMITTED
    if looks_like_application_form(fields) or has_submit_control(page):
        return ApplicationPageState.FORM
    current = normalize_navigation_url(page.url)
    if navigation is not None:
        if current == navigation.overview_url:
            return ApplicationPageState.OVERVIEW
        if current == navigation.application_url:
            return ApplicationPageState.LOADING
    try:
        if page.evaluate("document.readyState") != "complete":
            return ApplicationPageState.LOADING
    except Error:
        pass
    return ApplicationPageState.UNKNOWN
