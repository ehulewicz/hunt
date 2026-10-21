from __future__ import annotations

import contextlib
import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright

from personal_os_worker.core.secrets import loaded_values
from personal_os_worker.jobs.autofill import (
    BLANK_KIND,
    FILE_KINDS,
    MULTI,
    AutofillPlan,
    FormFill,
    Resolves,
    build_autofill_plan,
    choice_group_key,
    is_sensitive_field,
    offered_options,
)
from personal_os_worker.jobs.formatters import (
    affirmative_option,
    choose_date_range,
    source_option,
    yes_no_option,
)
from personal_os_worker.jobs.options import choose_option, choose_synonym, normalize_question
from personal_os_worker.jobs.registry.events import fill_event, form_of
from personal_os_worker.jobs.registry.kinds import classify, control_key, question_of
from personal_os_worker.jobs.registry.strategies import candidates
from personal_os_worker.jobs.sites import failure_signature, option_match

# Playwright auto-waits 30s for a control before an action fails; a control that
# vanished because the page moved on (the user submitted) must fail in seconds.
CONTROL_TIMEOUT_MS = 4_000

# The nearest ancestor holding a group of aria-pressed buttons: the same
# generic test the detector uses, so no site's class names are involved.
BUTTON_GROUP_XPATH = "xpath=ancestor::*[count(.//button[@aria-pressed]) >= 2][1]"


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


def plan_autofill(
    page: Page, *, resolver: Resolves | None = None
) -> tuple[list[dict[str, str | bool | None]], AutofillPlan]:
    """Work out what would be filled, without touching the page."""
    reveal_application_form(page)
    fields = inspect_fields(page)
    return fields, build_autofill_plan(fields, resolver=resolver)


class NoMatchingOption(RuntimeError):
    """The value has no unambiguous counterpart among the control's options."""


class CaptchaPresent(RuntimeError):
    """An interactive captcha is on the page, so the control is left to the user."""


# An open captcha challenge: the image or puzzle frame a widget shows once it
# is clicked or decides to test you. Only then does clicking anywhere else risk
# dismissing or disturbing it, so only then are choices left to you (R64). A
# checkbox widget waiting for you, an invisible-mode badge, or a hidden enclave
# frame does not count, and nothing here ever solves a captcha.
CAPTCHA_SCRIPT = """
() => [...document.querySelectorAll(
  'iframe[src*="recaptcha/api2/bframe" i], iframe[src*="recaptcha/enterprise/bframe" i],'
  + ' iframe[src*="hcaptcha" i][src*="frame=challenge" i], iframe[title*="challenge" i]'
)].some(element => {
  const box = element.getBoundingClientRect();
  if (box.width < 2 || box.height < 2) return false;
  const style = getComputedStyle(element);
  return style.visibility !== 'hidden' && style.display !== 'none'
    && Number(style.opacity || 1) > 0;
})
"""


def captcha_challenge_present(page: Page) -> bool:
    try:
        return bool(page.evaluate(CAPTCHA_SCRIPT))
    except Error:
        return False


@dataclass(frozen=True, slots=True)
class Attempt:
    page: Page
    control: Any
    field: dict[str, Any]
    fill: FormFill
    document: Path | None


def button_choice(texts: list[str], value: str) -> str | None:
    """The one pressable button that answers the value."""
    chosen = option_match(texts, value)
    if chosen is None and {text.casefold() for text in texts} == {"yes", "no"}:
        wanted = "yes" if value.strip().casefold() == "yes" else "no"
        chosen = next(text for text in texts if text.casefold() == wanted)
    return chosen


def _button_group(control: Any) -> tuple[Any, list[str]]:
    buttons = control.locator(BUTTON_GROUP_XPATH).locator("button[aria-pressed]")
    texts = [str(buttons.nth(index).inner_text()).strip() for index in range(buttons.count())]
    return buttons, texts


# Whether a file control's upload is something you can see: its file is chosen,
# and the control sits in a rendered part of the page (a styled-over input in a
# visible dropzone counts) or the file's name shows. A file in a panel nobody
# opened — JazzHR's "Attach resume" — is not shown to you, whatever the input holds.
UPLOAD_SHOWN = """
element => {
  const files = [...(element.files || [])];
  if (!files.length) return false;
  if (element.parentElement && element.parentElement.getClientRects().length) return true;
  const text = element.ownerDocument.body ? element.ownerDocument.body.innerText : '';
  return files.some(file => text.includes(file.name));
}
"""

# The one control that opens a file input's closed panel: a visible link or
# button saying attach or upload in the field around it, which holds no other
# file input. Never one that navigates, submits, pastes, or reaches a cloud drive.
UPLOAD_OPENER = """
element => {
  if (element.parentElement && element.parentElement.getClientRects().length) return null;
  let block = element.parentElement;
  while (block && !block.getClientRects().length) block = block.parentElement;
  if (!block || block === document.body || block === document.documentElement) return null;
  if (block.querySelectorAll('input[type="file"]').length !== 1) return null;
  const openers = [...block.querySelectorAll('a, button, [role="button"]')].filter(node => {
    if (!node.getClientRects().length) return false;
    const text = (node.textContent || '').trim();
    if (!/\\b(attach|upload|browse|choose|select)\\b/i.test(text)) return false;
    if (/paste|remove|delete|linkedin|dropbox|drive|indeed|submit|apply/i.test(text)) return false;
    if (node.tagName === 'BUTTON' && node.type === 'submit') return false;
    const href = (node.getAttribute('href') || '').trim();
    return !(node.tagName === 'A' && href && !/^(#|javascript:)/i.test(href));
  });
  return openers.length === 1 ? openers[0] : null;
}
"""
FILE_CHOOSER_WAIT_MS = 1_000


def upload_into(page: Page, control: Any, document: Path) -> None:
    """Choose a file the way you would: open the field's panel first when it is shut.

    An opener that raises the browser's file dialog is answered with the file,
    so no dialog ever waits in your window.
    """
    opener = control.evaluate_handle(UPLOAD_OPENER, timeout=CONTROL_TIMEOUT_MS).as_element()
    if opener is not None:
        try:
            with page.expect_file_chooser(timeout=FILE_CHOOSER_WAIT_MS) as chooser:
                opener.click(timeout=CONTROL_TIMEOUT_MS)
            chooser.value.set_files(str(document), timeout=CONTROL_TIMEOUT_MS)
            return
        except Error:
            pass  # no dialog came (the panel opened, or the click did not take)
    control.set_input_files(str(document), timeout=CONTROL_TIMEOUT_MS)


def _upload_file(attempt: Attempt) -> str | None:
    upload_into(attempt.page, attempt.control, cast(Path, attempt.document))
    attempt.page.wait_for_timeout(750)
    return None


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


def _combobox_choose_from_menu(attempt: Attempt) -> str | None:
    return choose_combobox_option(
        attempt.page, attempt.control, str(attempt.fill.chooser),
        referred=attempt.fill.referred,
    )


def _combobox_type_and_select(attempt: Attempt) -> str | None:
    return select_combobox_option(attempt.page, attempt.control, str(attempt.fill.value))


def _click_option_button(attempt: Attempt) -> str | None:
    buttons, texts = _button_group(attempt.control)
    chosen = button_choice(texts, str(attempt.fill.value))
    if chosen is None:
        raise NoMatchingOption("no unambiguous option button")
    buttons.nth(texts.index(chosen)).click(timeout=CONTROL_TIMEOUT_MS)
    return chosen


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


# The visible, childless texts inside a field's own container: what a
# suggestion list without ARIA roles shows once something is typed.
FIELD_TEXTS_SCRIPT = """
element => {
  const box = element.closest(
    'li, fieldset, [class*="question" i], [class*="field" i], [class*="form-group" i]'
  ) || element.parentElement;
  const out = [];
  for (const node of box.querySelectorAll('*')) {
    if (node.children.length || node === element || node.closest('label > :first-child')) continue;
    const text = (node.textContent || '').trim();
    const rect = node.getBoundingClientRect();
    if (text && rect.width > 0 && rect.height > 0 && text.length < 160) out.push(text);
  }
  return out;
}
"""


MONTH_PREFIXES = (
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
)


def _pick_suggestion(texts: list[str], value: str) -> str | None:
    """The suggestion that names the same place, else the one option that matches."""
    from personal_os_worker.jobs.office import parse

    wanted = parse(value)
    if wanted.city and wanted.state:
        same = [
            text for text in dict.fromkeys(texts)
            if (place := parse(text)).city and place.city.casefold() == wanted.city.casefold()
            and place.state == wanted.state
        ]
        if same:
            return same[0]
    return _pick(texts, value)


def _type_and_pick_suggestion(attempt: Attempt) -> str | None:
    """Type into a text box whose suggestions are plain elements, then click the match.

    Some autocompletes (Lever's location) render suggestions with no ARIA
    roles and clear the box on blur unless one is chosen. What appears in
    the field's own container after typing is the suggestion list; the full
    answer is typed first, then only its first part ("Atlanta"), since a
    places search rarely knows "Atlanta, Georgia, United States".
    """
    control = attempt.control
    value = str(attempt.fill.value)
    before = set(control.evaluate(FIELD_TEXTS_SCRIPT, timeout=CONTROL_TIMEOUT_MS))
    for typed in dict.fromkeys([value, value.split(",")[0].strip()]):
        control.fill("", timeout=CONTROL_TIMEOUT_MS)
        control.press_sequentially(typed, delay=30, timeout=CONTROL_TIMEOUT_MS)
        deadline = time.monotonic() + SUGGESTION_TIMEOUT_S
        while time.monotonic() < deadline:
            attempt.page.wait_for_timeout(250)
            texts = [
                text
                for text in control.evaluate(FIELD_TEXTS_SCRIPT, timeout=CONTROL_TIMEOUT_MS)
                if text not in before
            ]
            chosen = _pick_suggestion(texts, value) if texts else None
            if chosen is not None:
                box = control.locator(
                    "xpath=ancestor::*[self::li or self::fieldset or contains(@class,'question')"
                    " or contains(@class,'field')][1]"
                )
                box.get_by_text(chosen, exact=True).first.click(timeout=CONTROL_TIMEOUT_MS)
                attempt.page.wait_for_timeout(300)
                shown = str(control.input_value(timeout=CONTROL_TIMEOUT_MS)).strip()
                return shown or chosen
    raise NoMatchingOption("no suggestion matched what was typed")


# Every strategy in the registry, by name. `registry.strategies` decides which
# are candidates for a fill and in what order.
OPERATIONS: dict[str, Callable[[Attempt], str | None]] = {
    "upload_file": _upload_file,
    "type_and_pick_suggestion": _type_and_pick_suggestion,
    "select_by_label": _select_by_label,
    "select_by_value": _select_by_value,
    "select_multiple": _select_multiple,
    "combobox_choose_from_menu": _combobox_choose_from_menu,
    "combobox_type_and_select": _combobox_type_and_select,
    "click_option_button": _click_option_button,
    "check_control": _check_control,
    "uncheck_control": _uncheck_control,
    "set_value": _set_value,
}


def stopped_stage(error: BaseException) -> str:
    """Where a failed attempt stopped: the value fit no option, or the widget refused."""
    if isinstance(error, NoMatchingOption) or "unambiguous" in str(error):
        return "format"
    return "strategy"


def failure_category(message: str) -> str:
    return (
        "ambiguous_mapping"
        if "unambiguous" in message
        else "captcha_guard"
        if "captcha" in message.casefold()
        else "stale_locator"
        if "no longer present" in message or "strict mode" in message
        else "fill_exception"
    )


def _attempt_event(
    field: dict[str, Any],
    fill: FormFill,
    strategy: str | None,
    stopped_at: str | None,
    operated_ok: bool | None,
    options: list[str],
) -> dict[str, Any]:
    return fill_event(
        field, strategy=strategy, stopped_at=stopped_at, operated_ok=operated_ok,
        value=fill.value, resolution=fill.resolution, source=fill.chooser, options=options,
    )


class FillObserver(Protocol):
    """Watches each planned fill: `--explain` prints it, `--step` pauses on it."""

    def before(self, field: dict[str, Any], fill: FormFill, strategies: list[str]) -> bool:
        """Called before a control is operated; False leaves it untouched."""
        ...

    def after(self, field: dict[str, Any], fill: FormFill, outcome: dict[str, Any]) -> None:
        """What happened: each attempt and its error, the readback, the value seen."""
        ...


def apply_autofill(
    page: Page,
    fields: list[dict[str, Any]],
    plan: AutofillPlan,
    *,
    resume_path: Path | None,
    assert_active: Callable[[], None],
    transcript_path: Path | None = None,
    preferences: dict[str, str] | None = None,
    observer: FillObserver | None = None,
) -> dict[str, Any]:
    """Operate every planned fill through its control kind's candidate strategies.

    Candidates are tried in order until one operates; every attempt, failed or
    not, becomes a fill event. A failure is recorded and the run moves on. A
    strategy a validated repair found for this exact control goes first.
    """
    filled_labels: list[str] = []
    skipped: list[str] = []
    applied: dict[int, str] = {}  # values that passed readback, keyed by control
    unanswered = list(plan.unanswered_required_questions)
    failures: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    documents = {"resume": resume_path, "transcript": transcript_path}
    # Uploads first: some forms re-render after one, and none minds the order.
    ordered_fills = sorted(plan.fills, key=lambda fill: 0 if fill.kind in FILE_KINDS else 1)
    for fill in ordered_fills:
        assert_active()
        field = fields[fill.index]
        offered = offered_options(fields, field)
        label = str(field.get("label") or field.get("name") or fill.index)
        document = documents.get(fill.kind)
        if fill.kind == BLANK_KIND:
            # A none fact: the answer is to leave it blank, so nothing is operated.
            events.append(_attempt_event(field, fill, None, None, None, offered))
            if observer is not None:
                observer.after(field, fill, {"skipped": "left blank on purpose"})
            continue
        if fill.kind in FILE_KINDS and document is None:
            skipped.append(f"{label} (no {fill.kind} provided)")
            events.append(_attempt_event(field, fill, None, None, None, offered))
            if observer is not None:
                observer.after(field, fill, {"skipped": f"no {fill.kind} provided"})
            continue
        names = candidates(classify(field), field, fill, has_document=document is not None)
        preferred = (preferences or {}).get(control_key(field))
        if preferred in names:
            names = [preferred, *(name for name in names if name != preferred)]
        if observer is not None and not observer.before(field, fill, names):
            skipped.append(f"{label} (skipped while stepping)")
            continue
        control = fresh_control(page, field)
        expected = fill.value
        operated: str | None = None
        first_error: Exception | None = None
        attempts: list[tuple[str, str | None]] = []
        for name in names:
            try:
                result = OPERATIONS[name](Attempt(page, control, field, fill, document))
            except (Error, RuntimeError) as error:
                first_error = first_error or error
                attempts.append((name, str(error).splitlines()[0][:200] if str(error) else
                                 type(error).__name__))
                events.append(
                    _attempt_event(field, fill, name, stopped_stage(error), False, offered)
                )
                continue
            attempts.append((name, None))
            operated = name
            if result is not None:
                expected = result
            break
        if operated is None and first_error is not None:
            message = str(first_error)
            if field.get("required") is True:
                unanswered.append(label)
            failures.append(
                _failure(
                    field, failure_category(message), message, intended=fill.value,
                    stopped_at=stopped_stage(first_error), fill=fill,
                )
            )
            if observer is not None:
                observer.after(field, fill, {
                    "attempts": attempts, "operated": None,
                    "stopped_at": stopped_stage(first_error),
                })
            continue
        is_upload = fill.kind in FILE_KINDS
        observed: str | None = None
        if fill.clear:
            try:
                verified = not fresh_control(page, field).is_checked(timeout=CONTROL_TIMEOUT_MS)
            except Error:
                verified = False
        else:
            verified = verify_control(
                page,
                field,
                expected,
                resume=is_upload,
                filename=document.name if is_upload and document is not None else None,
            )
        events.append(_attempt_event(
            field, fill, operated, None if verified else "strategy", verified, offered
        ))
        if verified:
            filled_labels.append(label)
            if expected is not None and not is_upload:
                applied[fill.index] = (
                    str(field.get("label") or field.get("value") or expected)
                    if field.get("type") in {"radio", "checkbox"} else expected
                )
        else:
            try:
                observed = fresh_control(page, field).input_value(timeout=CONTROL_TIMEOUT_MS)
            except Error:
                observed = None
            failures.append(
                _failure(
                    field,
                    "readback_mismatch",
                    "value did not register",
                    intended=fill.value,
                    observed=observed,
                    stopped_at="strategy",
                    fill=fill,
                )
            )
            if field.get("required") is True:
                unanswered.append(label)
        if observer is not None:
            observer.after(field, fill, {
                "attempts": attempts, "operated": operated, "expected": expected,
                "verified": verified,
                "observed": observed,
                "stopped_at": None if verified else "strategy",
            })
    # The page remains open in the dedicated visible profile for manual review.
    return {
        "captured_url": page.url,
        "filled_fields": filled_labels,
        "skipped_fields": skipped,
        "applied": applied,
        "unanswered_required_questions": list(dict.fromkeys(unanswered)),
        "failures": failures,
        "events": events,
        "submitted": False,
    }


def fresh_control(page: Page, field: dict[str, Any]) -> Any:
    frame_index = int(field.get("frame_index") or 0)
    if frame_index >= len(page.frames):
        raise RuntimeError("field frame is no longer present")
    frame = page.frames[frame_index]
    selector = str(field.get("selector") or "")
    if selector:
        locator = frame.locator(selector)
        if locator.count() == 1:
            return locator
    controls = frame.locator("input, textarea, select, [role=combobox]")
    position = int(field.get("control_index", field["index"]))
    if position >= controls.count():
        raise RuntimeError("control is no longer present")
    return controls.nth(position)


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


COMBOBOX_SETTLE_MS = 300
# How long a plain suggestion list may take to appear after typing.
SUGGESTION_TIMEOUT_S = 4.0
COMBOBOX_LIST_TIMEOUT_MS = 600
COMBOBOX_OPTIONS_TIMEOUT_MS = 4_000


def _menu_options(control: Any) -> Any:
    # Scope to the control's own document so a form inside an iframe works too.
    scoped = control.locator("xpath=ancestor::body")
    try:
        in_document = scoped.count() > 0
    except Error:
        in_document = False
    if in_document:
        owned_id = control.get_attribute("aria-controls") or control.get_attribute("aria-owns")
        if owned_id and len(owned_id.split()) == 1:
            owned = scoped.locator(f"[id={json.dumps(owned_id)}]")
            # Some accessible menus identify options by their disabled state
            # and keyboard tab stop, without repeating role=option.
            choices = owned.locator('[role="option"], [aria-disabled][tabindex="-1"]')
            if choices.count():
                return choices
        return scoped.get_by_role("option")
    # Inside a shadow root (SmartRecruiters) XPath cannot climb to <body>; role
    # queries pierce shadow roots, so ask the page.
    return control.page.get_by_role("option")


# An option's text, wherever its component renders it: its own text, else
# what its <slot>s show, else its shadow host's light text (SmartRecruiters'
# dropdown items keep the text outside the shadow root that holds role=option).
OPTION_TEXT_SCRIPT = """
element => {
  const own = (element.innerText || '').trim();
  if (own) return own;
  const slotted = [...element.querySelectorAll('slot')]
    .flatMap(slot => slot.assignedNodes({flatten: true}))
    .map(node => node.textContent || '').join(' ').replace(/\\s+/g, ' ').trim();
  if (slotted) return slotted;
  const host = element.getRootNode().host;
  return host ? (host.innerText || host.textContent || '').replace(/\\s+/g, ' ').trim() : '';
}
"""


def _visible_texts(options: Any) -> tuple[list[int], list[str]]:
    # Pages keep other listboxes in the DOM (Greenhouse's phone-code list has
    # every country as a hidden option); only what the menu shows is a choice.
    visible = [index for index in range(options.count()) if options.nth(index).is_visible()]
    return visible, [str(options.nth(index).evaluate(OPTION_TEXT_SCRIPT)) for index in visible]


def _pick(texts: list[str], value: str) -> str | None:
    distinct = list(dict.fromkeys(text for text in texts if text))
    chosen = choose_option(distinct, value) or choose_synonym(distinct, value)
    if chosen is None and value.strip().casefold() in {"yes", "no"}:
        # The same reading of yes and no a select gets ("No" is "Not Applicable"
        # when that is the only option saying no).
        chosen = yes_no_option(distinct, value.strip().casefold() == "yes")
    month = re.fullmatch(r"\s*([A-Za-z]+)\s+(\d{4})\s*", value)
    if chosen is None and month and month.group(1)[:3].casefold() in MONTH_PREFIXES:
        # "December 2027" against "Between December 2027 and September 2028".
        when = date(int(month.group(2)), MONTH_PREFIXES.index(month.group(1)[:3].casefold()) + 1, 1)
        chosen = choose_date_range(distinct, when)
    if chosen is None:
        target = normalize_question(value)
        containing = [text for text in distinct if target and target in normalize_question(text)]
        chosen = containing[0] if len(containing) == 1 else None
    return chosen


def _await_options(page: Page, options: Any, timeout_ms: int) -> bool:
    # Any visible option, not the first in the DOM: a closed native <select>
    # earlier on the page contributes hidden options that never become visible.
    try:
        options.locator("visible=true").first.wait_for(state="visible", timeout=timeout_ms)
    except Error:
        return False
    page.wait_for_timeout(COMBOBOX_SETTLE_MS)
    return True


def open_combobox(control: Any) -> None:
    """Open the menu through its keyboard input when that input is too small to click."""
    box = control.bounding_box()
    if (control.get_attribute("aria-readonly") == "true" and box is not None
            and (box["width"] < 1 or box["height"] < 1)):
        control.focus(timeout=CONTROL_TIMEOUT_MS)
        control.press("ArrowDown", timeout=CONTROL_TIMEOUT_MS)
    else:
        control.click(timeout=CONTROL_TIMEOUT_MS)


def combobox_options(page: Page, control: Any) -> list[str]:
    """What a typeahead offers before anything is typed; empty for a search box."""
    open_combobox(control)
    options = _menu_options(control)
    texts: list[str] = []
    if _await_options(page, options, COMBOBOX_LIST_TIMEOUT_MS):
        texts = list(dict.fromkeys(text for text in _visible_texts(options)[1] if text))
    control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
    return texts


def choose_combobox_option(
    page: Page, control: Any, chooser: str, *, referred: bool = False
) -> str:
    """Open a typeahead and pick by standing rule: the agreeing option, or your source."""
    open_combobox(control)
    options = _menu_options(control)
    if not _await_options(page, options, COMBOBOX_LIST_TIMEOUT_MS):
        control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
        raise RuntimeError(f"no unambiguous combobox option for {chooser} (none listed)")
    visible, texts = _visible_texts(options)
    distinct = list(dict.fromkeys(text for text in texts if text))
    if chooser == "consent":
        chosen = affirmative_option(distinct)
    else:
        chosen = source_option(distinct, referred=referred)
    if chosen is None:
        control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
        raise RuntimeError(f"no unambiguous combobox option for {chooser}")
    options.nth(visible[texts.index(chosen)]).click()
    return chosen


def select_combobox_option(page: Page, control: Any, value: str) -> str:
    """Pick the one option a typeahead offers for the answer and return its text.

    Open the menu first: a fixed list (Yes/No, GPA ranges) is matched as it
    is, since typing "3.78" into a list of ranges filters it down to nothing.
    Only when nothing is listed, or nothing listed fits, is the answer typed,
    which is how a search box (Google Places) or a long list is queried.
    Keystrokes rather than a synthetic value: react-select and Places key
    their menus off real input events and close on blur.
    """
    open_combobox(control)
    options = _menu_options(control)
    if _await_options(page, options, COMBOBOX_LIST_TIMEOUT_MS):
        visible, texts = _visible_texts(options)
        chosen = _pick(texts, value)
        if chosen is not None:
            options.nth(visible[texts.index(chosen)]).click()
            return chosen
    if control.get_attribute("aria-readonly") == "true":
        control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
        raise RuntimeError("no unambiguous combobox option in read-only menu")
    # A place ("Atlanta, Georgia") is searched whole, then by its first part,
    # since a places search rarely knows the state's full name; either way the
    # option naming the same city and state is the one picked.
    tries = list(dict.fromkeys([value, value.split(",")[0].strip()]))
    for attempt, typed in enumerate(tries):
        control.fill("", timeout=CONTROL_TIMEOUT_MS)
        control.press_sequentially(typed, delay=20, timeout=CONTROL_TIMEOUT_MS)
        if not _await_options(page, options, COMBOBOX_OPTIONS_TIMEOUT_MS):
            if attempt + 1 < len(tries):
                continue
            # A free-entry typeahead offers nothing to pick; Enter commits the text.
            control.press("Enter", timeout=CONTROL_TIMEOUT_MS)
            if combobox_value(control).casefold() == value.strip().casefold():
                return value.strip()
            raise RuntimeError("no unambiguous combobox option (none appeared)")
        visible, texts = _visible_texts(options)
        chosen = _pick_suggestion(texts, value)
        if chosen is not None:
            options.nth(visible[texts.index(chosen)]).click()
            return chosen
    control.press("Escape", timeout=CONTROL_TIMEOUT_MS)
    raise RuntimeError("no unambiguous combobox option")


def _verify_upload(page: Page, field: dict[str, Any], filename: str | None) -> bool:
    """Whether a resume/transcript upload took.

    Greenhouse's own resume widget removes the `<input type=file>` from the
    DOM on a successful upload and replaces it with a filename-plus-remove-
    button summary, shifting every control after it by one position. Asking
    `fresh_control` for the same field afterward silently lands on whatever
    control now sits at that stale index instead of raising, so a real
    success reads as an empty, unrelated control. When the direct file-count
    check comes back empty, look for the uploaded filename rendered anywhere
    on the page before calling it a failure. A file the input holds in a panel
    nobody opened is not shown to you, so it does not count (`UPLOAD_SHOWN`).
    """
    try:
        control = fresh_control(page, field)
        if control.evaluate(UPLOAD_SHOWN, timeout=CONTROL_TIMEOUT_MS) is True:
            return True
    except (Error, RuntimeError):
        pass
    if not filename:
        return False
    # The filename shows in the field's own frame (Greenhouse embedded on an
    # employer's site lives in an iframe).
    frame_index = int(field.get("frame_index") or 0)
    frame = page.frames[frame_index] if frame_index < len(page.frames) else page.main_frame
    try:
        return frame.get_by_text(filename, exact=False).first.is_visible()
    except Error:
        return False


def verify_control(
    page: Page,
    field: dict[str, Any],
    value: str | None,
    *,
    resume: bool = False,
    filename: str | None = None,
) -> bool:
    if resume:
        return _verify_upload(page, field, filename)
    control = fresh_control(page, field)
    try:
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


COMBOBOX_VALUE_SCRIPT = """
    element => {
      const own = String(element.value || '').trim();
      if (own) return own;
      let shell = element.parentElement;
      for (let depth = 0; shell && depth < 4; depth += 1) {
        const chosen = shell.querySelector('[class*="single-value"], [class*="multi-value"]');
        if (chosen && chosen.textContent.trim()) return chosen.textContent.trim();
        const mirror = shell.querySelector('input[aria-hidden="true"]');
        if (mirror && String(mirror.value || '').trim()) return String(mirror.value).trim();
        shell = shell.parentElement;
      }
      return '';
    }
"""


def combobox_value(control: Any) -> str:
    """What a typeahead currently holds, wherever the widget keeps it.

    A plain typeahead leaves the chosen text in the input. react-select
    empties the search input and renders the choice in a sibling
    `single-value`/`multi-value` element, or mirrors it into an aria-hidden
    input. Whichever is present is the value.
    """
    return str(control.evaluate(COMBOBOX_VALUE_SCRIPT, timeout=CONTROL_TIMEOUT_MS)).strip()


def combobox_shows(control: Any, expected: str) -> bool:
    """Whether a typeahead holds the picked option.

    The option was clicked by its text, so a rendered choice is the evidence;
    its display text may differ from the menu text (Greenhouse's phone-code
    picker lists "United States +1" but shows a flag and "+1").
    """
    held = combobox_value(control)
    return bool(held) and (
        _pick([expected], held) is not None
        or control.evaluate(
            "element => !String(element.value || '').trim()", timeout=CONTROL_TIMEOUT_MS
        )
    )


def _failure(
    field: dict[str, Any],
    category: str,
    message: str,
    *,
    intended: str | None = None,
    observed: str | None = None,
    stopped_at: str | None = None,
    fill: FormFill | None = None,
) -> dict[str, Any]:
    fingerprint = str(field.get("fingerprint") or field.get("selector") or field.get("index"))
    resolution = fill.resolution if fill is not None else None
    return {
        # Where it stopped decides whether Repair can retry it (design §10.3).
        "stopped_at": stopped_at,
        "question": question_of(field),
        "fact_key": resolution.fact_key if resolution is not None else None,
        "formatter": resolution.formatter if resolution is not None else None,
        "signature": failure_signature(str(field.get("ats") or "generic"), category, fingerprint),
        "category": category,
        "field_label": field.get("label"),
        "control_kind": field.get("type") or field.get("tag"),
        "field_fingerprint": fingerprint,
        "error_message": message[:2000],
        "intended_value": intended,
        "observed_value": observed,
        "dom_signals": {key: field.get(key) for key in ("tag", "type", "name", "label", "role")},
    }


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
    results: list[dict[str, Any]] = []
    script = """
        elements => {
        const uploadShown = """ + UPLOAD_SHOWN.strip() + """;
        return elements.filter(element => {
          const type = (element.getAttribute('type') || '').toLowerCase();
          // React-select mirrors the chosen value into an aria-hidden input that
          // carries the required flag; the visible combobox is the real control.
          if (element.getAttribute('aria-hidden') === 'true') return false;
          // Responsive widgets can keep a hidden native select beside their
          // visible combobox. That is one question, never a second filled field.
          if (element.tagName === 'SELECT' && !element.getClientRects().length
              && element.parentElement?.querySelector('[role="combobox"]')) return false;
          // A captcha's own response box answers the captcha, never a question.
          const naming = `${element.getAttribute('name') || ''} ${element.id || ''}`.toLowerCase();
          if (/captcha|turnstile/.test(naming)) return false;
          if (element.closest('[data-sitekey], .g-recaptcha, .h-captcha, .cf-turnstile')) {
            return false;
          }
          // A text box nobody can see (an XML mirror, a "paste your resume" tab
          // that is not open) is not a question. Hidden radios, checkboxes,
          // files and selects stay: widgets style over them and still use them.
          const textLike = element.tagName === 'TEXTAREA' || (
            element.tagName === 'INPUT' && !['radio', 'checkbox', 'file'].includes(type)
          );
          if (textLike && !(element.offsetWidth || element.offsetHeight
              || element.getClientRects().length)) {
            return false;
          }
          // A collapsed popup's own search box (a phone field's country picker)
          // belongs to that control, never to the form.
          const popup = element.closest('[role="dialog"], [role="listbox"], [role="menu"]');
          if (popup && !popup.offsetWidth && !popup.offsetHeight) return false;
          return !['hidden', 'password', 'submit', 'button', 'reset'].includes(type);
        }).map((element, index) => {
          const id = element.id;
          // The document, or the shadow root the control lives in (SmartRecruiters).
          const home = element.getRootNode();
          const explicit = id ? home.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          // A label that names another, existing control by its id is that
          // control's (Ashby's education labels name ids that do not exist).
          const ownLabel = node => !node.htmlFor || node.htmlFor === id
            || !(home.getElementById ? home.getElementById(node.htmlFor)
              : document.getElementById(node.htmlFor));
          const wrapping = element.closest('label');
          const choice = ['radio', 'checkbox'].includes(element.type);
          // A container a choice's question is read from must hold that one
          // question: every radio in it shares this one's name, and at most
          // one label or legend in it is no option's own. A section ("Job
          // specific questions") holds several, and its heading asks nothing.
          const oneQuestion = container => {
            if (!choice || !container) return true;
            const options = [...container.querySelectorAll(`input[type="${element.type}"]`)];
            if (element.type === 'radio' && options.some(option => option.name !== element.name)) {
              return false;
            }
            const nodes = [...container.querySelectorAll('label, legend')].filter(node => (
              (node.textContent || '').trim() && !options.some(
                option => node.contains(option) || (node.htmlFor && node.htmlFor === option.id)
              )
            ));
            return nodes.filter(
              node => !nodes.some(other => other !== node && other.contains(node))
            ).length <= 1;
          };
          const fieldset = element.closest('fieldset');
          // Ashby writes the group question as a heading label, not a legend.
          const legend = oneQuestion(fieldset)
            ? fieldset?.querySelector(':scope > legend, :scope > label') : null;
          const marksRequired = node => Boolean(node && (
            node.getAttribute('aria-required') === 'true'
            || /required/i.test(String(node.className))
            || /\\*\\s*$/.test(node.textContent || '')
            || node.querySelector('[class*="required"], [aria-required="true"]')
          ));
          const groupRequired = marksRequired(legend);
          const group = element.closest('[role="group"], [role="radiogroup"]');
          // A group names its question by aria-label, or by the element its
          // aria-labelledby points at.
          const groupNames = group?.getAttribute('aria-labelledby');
          const groupLabel = group && oneQuestion(group)
            ? (group.getAttribute('aria-label') || (choice && groupNames
                ? groupNames.split(/\\s+/)
                    .map(id => document.getElementById(id)?.textContent || '').join(' ').trim()
                : '')) || null
            : null;
          const nearby = element.closest('[class*="field"], [class*="question"], [data-testid]');
          const nearbyChild = nearby
            ? [...nearby.children].find(child => {
                const text = String(child.textContent || '').trim();
                return text && !child.contains(element)
                  && !child.querySelector('input, textarea, select, [role="combobox"]');
              }) || null
            : null;
          const nearbyQuestion = oneQuestion(nearby) ? nearbyChild : null;
          // Composite controls (notably Ashby's Education History widget) do
          // not associate every nested input with its label. Walk outward for
          // the nearest label that is a direct child of a control-local
          // wrapper before falling back to the outer field/section heading.
          let localLabel = null;
          let localWrapper = element.parentElement || (home !== document ? home : null);
          for (let depth = 0; localWrapper && depth < 5; depth += 1) {
            localLabel = [...localWrapper.children].find(
              child => child.tagName === 'LABEL' && !child.contains(element) && ownLabel(child)
            ) || null;
            if (localLabel) break;
            localWrapper = localWrapper.parentElement;
          }
          const labelledBy = element.getAttribute('aria-labelledby');
          const referenced = labelledBy
            ? labelledBy.split(/\\s+/).map(id => document.getElementById(id)?.textContent || '')
                .join(' ')
            : '';
          // Lever's custom questions and its EEO section put the question text
          // in a sibling `.application-label`, never associated with the control
          // by id or by wrapping it together with a single-input field; a radio
          // or checkbox is additionally wrapped in its own tiny label holding
          // only that option's text, which is what `wrapping` above already
          // captures and must keep winning for those.
          const leverQuestion = element.closest('.application-question');
          const leverQuestionText = (
            leverQuestion?.querySelector('.application-label')?.textContent || ''
          ).replace(/[*\\u2731]\\s*$/, '').trim() || null;
          // The EEO section nests a long description inside the same label as
          // the option text; this span is the option's own text alone.
          const leverOptionLabel = wrapping
            ?.querySelector('.application-answer-alternative')?.textContent?.trim() || null;
          // A choice rendered as aria-pressed buttons with a hidden checkbox that
          // mirrors their state (Ashby's yes/no is one). Found by the ARIA state
          // that screen readers rely on rather than by a class name, so the same
          // detection holds on any site: the nearest ancestor holding two or more
          // pressable buttons and no other form input.
          let yesnoContainer = null;
          if (element.type === 'checkbox') {
            let ancestor = element.parentElement;
            for (let depth = 0; ancestor && depth < 3 && !yesnoContainer; depth += 1) {
              const pressable = ancestor.querySelectorAll('button[aria-pressed]').length;
              const inputs = ancestor.querySelectorAll(
                'input:not([type="hidden"]), textarea, select'
              ).length;
              if (pressable >= 2 && inputs === 1) yesnoContainer = ancestor;
              ancestor = ancestor.parentElement;
            }
          }
          const educationEntry = element.closest(
            '.ashby-application-form-input-education-entry'
          );
          // The question sits beside the button group: Ashby's title label, or
          // the first label or legend among the group's siblings.
          const yesnoLabel = yesnoContainer
            ? (
                yesnoContainer.parentElement?.querySelector(
                  'label.ashby-application-form-question-title'
                )
                || [...(yesnoContainer.parentElement?.children || [])].find(
                  child => ['LABEL', 'LEGEND'].includes(child.tagName)
                )
                || null
              )
            : null;
          const yesnoButtons = yesnoContainer
            ? [...yesnoContainer.querySelectorAll('button[aria-pressed]')] : [];
          const yesnoPressed = yesnoButtons.find(
            button => button.getAttribute('aria-pressed') === 'true'
          ) || null;
          const finalType = yesnoContainer ? 'yesno' : element.getAttribute('type');
          // A placeholder such as "Start typing..." is the last resort; Ashby
          // comboboxes have no id, so their question lives on the field's label.
          // A Lever structured question (notably the location typeahead) nests
          // the whole field inside the question's own label, so the wrapping
          // label carries the live dropdown text too. `.application-label` is
          // the question alone; only a radio or checkbox still needs the
          // wrapping label, which there holds just that option's text.
          const optionWrapping = ['radio', 'checkbox'].includes(element.type)
            ? wrapping?.textContent : null;
          const baseLabel = (
              yesnoLabel?.textContent
              || explicit?.textContent
              || leverOptionLabel
              || optionWrapping
              || leverQuestionText
              || wrapping?.textContent
              || element.getAttribute('aria-label')
              || referenced
              || localLabel?.textContent
              || [...(nearby?.querySelectorAll('label') || [])].find(ownLabel)?.textContent
              || element.getAttribute('placeholder')
              || ''
            ).trim();
          // A pair of native selects under one date label is otherwise
          // indistinguishable. Its empty first option supplies the missing
          // Month/Year part of the label, but is not itself a valid answer.
          const selectPart = element.tagName === 'SELECT'
            ? String(element.options[0]?.textContent || '').trim().replace(/\\.{2,}$/, '')
            : '';
          const rawLabel = (
            selectPart && /^(month|year)$/i.test(selectPart)
              && !baseLabel.toLowerCase().split(/\\s+/).includes(selectPart.toLowerCase())
              ? `${baseLabel} ${selectPart}` : baseLabel
          ).trim();
          // A trailing asterisk marks a required field on Greenhouse boards.
          const label = rawLabel.replace(/\\s*\\*\\s*$/, '');
          let shell = element.parentElement;
          let mirroredRequired = false;
          let mirroredValue = '';
          for (let depth = 0; shell && depth < 4; depth += 1) {
            const mirror = shell.querySelector('input[aria-hidden="true"]');
            if (mirror) {
              mirroredRequired = mirroredRequired || mirror.required === true;
              mirroredValue = mirroredValue || String(mirror.value || '').trim();
              if (mirroredRequired) break;
            }
            shell = shell.parentElement;
          }
          // How many inputs answer the same question: options sharing a name,
          // or, unnamed, sharing a fieldset or group. One checkbox is a toggle;
          // several are a multi-choice group.
          // Options with no shared name, fieldset or group role (JazzHR's Yes/No
          // checkboxes): the smallest ancestor holding two or more of them is the
          // question's block, and the question is the nearest label before them
          // that is not an option's own.
          let blockOptions = [];
          let blockQuestion = null;
          if (choice && !legend && !groupLabel && !yesnoContainer) {
            let ancestor = element.parentElement;
            for (let depth = 0; ancestor && depth < 5; depth += 1) {
              if (!oneQuestion(ancestor)) break;
              const options = [...ancestor.querySelectorAll(`input[type="${element.type}"]`)];
              if (options.length >= 2) {
                const optionLabel = node => options.some(
                  option => node.contains(option) || (node.htmlFor && node.htmlFor === option.id)
                );
                // One question only: a container holding several questions'
                // options is a form, not a question's block.
                const questions = [...ancestor.querySelectorAll('label, legend')].filter(
                  node => !optionLabel(node) && (node.textContent || '').trim()
                );
                if (questions.length === 1 && (questions[0].compareDocumentPosition(element)
                    & Node.DOCUMENT_POSITION_FOLLOWING)) {
                  blockOptions = options;
                  blockQuestion = questions[0];
                } else if (questions.length === 0 && element.type === 'radio'
                    && options.every(option => option.name === element.name)) {
                  // A radio group whose question is plain text before its
                  // options ("<p>Are you 18?</p>"): the block's first child
                  // that holds text and no control. A lone Yes is no question.
                  blockQuestion = [...ancestor.children].find(child => (
                    (child.textContent || '').trim()
                    && !child.querySelector('input, textarea, select, [role="combobox"]')
                    && !optionLabel(child)
                    && (child.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
                  )) || null;
                  if (blockQuestion) blockOptions = options;
                }
                // The first shared ancestor may contain only the options.
                // Keep walking to their question, but never across questions.
                if (blockQuestion) break;
              }
              ancestor = ancestor.parentElement;
            }
          }
          // What a file control sits in, crossing shadow roots: a dropzone
          // labelled only "Choose a file" inside <oc-resume-upload> is the resume.
          const containerHints = [];
          if (element.type === 'file') {
            let node = element;
            for (let depth = 0; depth < 8; depth += 1) {
              const root = node.getRootNode();
              node = node.parentElement || (root instanceof ShadowRoot ? root.host : null);
              if (!node || node === document.body) break;
              const classes = typeof node.className === 'string' ? node.className : '';
              containerHints.push(`${node.tagName} ${node.id || ''} ${classes}`.toLowerCase());
            }
          }
          let groupSize = 1;
          if (['radio', 'checkbox'].includes(element.type) && !yesnoContainer) {
            const sameType = `input[type="${element.type}"]`;
            groupSize = element.name
              ? (element.form || document).querySelectorAll(
                  `${sameType}[name="${CSS.escape(element.name)}"]`
                ).length
              : element.closest('fieldset, [role="group"], [role="radiogroup"]')
                  ?.querySelectorAll(sameType).length || 1;
            if (groupSize < 2 && blockOptions.length >= 2) groupSize = blockOptions.length;
          }
          const isCombobox = element.getAttribute('role') === 'combobox';
          // react-select empties its search input after a pick and renders the
          // choice in a sibling; that sibling is the value of the control.
          let chosenText = '';
          if (isCombobox) {
            let holder = element.parentElement;
            for (let depth = 0; holder && depth < 4; depth += 1) {
              const chosen = holder.querySelector(
                '[class*="single-value"], [class*="multi-value"]'
              );
              if (chosen) { chosenText = chosen.textContent.trim(); break; }
              holder = holder.parentElement;
            }
          }
          // A radio or checkbox shares its name with its group: its value
          // tells it apart from the other options.
          const optionValue = ['radio', 'checkbox'].includes(element.type)
            && element.hasAttribute('value')
            ? `[value="${CSS.escape(element.getAttribute('value'))}"]` : '';
          const selector = id
            ? `#${CSS.escape(id)}`
            : element.getAttribute('name')
              ? `${element.tagName.toLowerCase()}[name="${
                  CSS.escape(element.getAttribute('name'))
                }"]${optionValue}`
              : '';
          return {
            index,
            // The position among every control the frame holds, filtered or
            // not: what `fresh_control` counts when the selector is ambiguous.
            control_index: elements.indexOf(element),
            tag: element.tagName.toLowerCase(),
            type: finalType,
            name: element.getAttribute('name'),
            label,
            group: (
              legend?.textContent
              || groupLabel
              || nearbyQuestion?.textContent
              || (['radio', 'checkbox'].includes(element.type) ? leverQuestionText : null)
              || blockQuestion?.textContent
              || ''
            ).trim().replace(/\\s*\\*\\s*$/, ''),
            required: element.required === true
              || element.getAttribute('aria-required') === 'true'
              || marksRequired(localLabel)
              || /\\*\\s*$/.test(rawLabel)
              || (['radio', 'checkbox'].includes(element.type) && groupRequired)
              || marksRequired(blockQuestion)
              || Boolean(group && oneQuestion(group)
                && group.getAttribute('aria-required') === 'true')
              || (element.getAttribute('role') === 'combobox' && mirroredRequired)
              || Boolean(yesnoContainer && /_required_/.test(yesnoLabel?.className || '')),
            selector,
            role: element.getAttribute('role'),
            // -1 when the control declares no cap; a draft has to fit what it does.
            max_length: element.maxLength > 0 ? element.maxLength : null,
            placeholder: element.getAttribute('placeholder'),
            autocomplete: element.getAttribute('autocomplete'),
            section: educationEntry ? 'education' : null,
            options: yesnoContainer
              ? yesnoButtons.map(button => button.textContent.trim()).filter(Boolean)
              : element.tagName === 'SELECT'
                ? [...element.options]
                    .filter(option => option.value !== '' && !option.disabled)
                    .map(option => option.textContent.trim()).filter(Boolean)
                : [],
            value: yesnoContainer
              ? (yesnoPressed ? yesnoPressed.textContent.trim() : null)
              : ['radio', 'checkbox'].includes(element.type)
                ? (element.getAttribute('value') || '') : null,
            multiple: element.tagName === 'SELECT' && element.multiple === true,
            group_size: groupSize,
            container: containerHints.join(' | ').slice(0, 600) || null,
            aria: {
              autocomplete: element.getAttribute('aria-autocomplete'),
              haspopup: element.getAttribute('aria-haspopup'),
            },
            fingerprint: [element.tagName, finalType,
              element.getAttribute('name'), label, element.getAttribute('role')
            ].join('|').toLowerCase(),
            filled: yesnoContainer
              ? Boolean(yesnoPressed)
              : element.type === 'file'
                ? uploadShown(element)
                : ['checkbox', 'radio'].includes(element.type)
                  ? element.checked === true
                  // A phone box prefilled with its dial code ("+1") or a mask
                  // ("(___) ___-____") holds no number yet.
                  : element.type === 'tel'
                    ? String(element.value || '').replace(/\\D/g, '').length >= 7
                    : String(element.value || '').trim().length > 0
                      || (isCombobox && (mirroredValue.length > 0 || chosenText.length > 0)),
            // The option a select or a typeahead shows, read for the follow-up below it.
            selected: element.tagName === 'SELECT'
              ? (element.value ? (element.selectedOptions[0]?.textContent || '').trim() : null)
              : isCombobox ? (chosenText || mirroredValue || null) : null,
          };
        });
        }
        """
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
    return results


def page_evidence(
    page: Page,
    fields: list[dict[str, Any]],
    *,
    console_errors: list[str] | None = None,
) -> dict[str, bytes]:
    """Capture the form as it stands: every control, its value, and the page.

    A failure is diagnosed from what the form actually held, and a captured
    page with its values stripped out could not show why a fill went wrong. So
    only two things are taken out: the captcha widget, so a saved page does not
    try to render a live challenge, and passwords. A password box is emptied
    and masked, and every secret the worker has read is replaced wherever it
    appears, in whichever escaping the capture wrote it.
    """
    captured_fields = [
        {
            key: field.get(key)
            for key in (
                "index",
                "tag",
                "type",
                "name",
                "label",
                "group",
                "required",
                "selector",
                "role",
                "max_length",
                "placeholder",
                "autocomplete",
                "section",
                "options",
                "value",
                "filled",
                "fingerprint",
            )
        }
        for field in fields
    ]
    html_script = """
        body => {
          const clone = body.cloneNode(true);
          clone.querySelectorAll(
            '[data-sitekey], iframe[src*="captcha" i]'
          ).forEach(e => e.remove());
          clone.querySelectorAll('input[type="password"]').forEach(e => e.removeAttribute('value'));
          return clone.outerHTML;
        }
        """
    evidence = {
        "controls.json": json.dumps(captured_fields, indent=2, default=str).encode(),
        "page.json": json.dumps({"url": page.url, "title": page.title()}, indent=2).encode(),
        "console-errors.json": json.dumps(console_errors or [], indent=2).encode(),
    }
    # A screenshot is the first thing to time out on a busy page; the controls
    # and HTML still say what happened without it.
    with contextlib.suppress(Error):
        evidence["screen.png"] = page.screenshot(
            full_page=True, timeout=10_000, mask=[page.locator('input[type="password"]')]
        )
    for frame_index, frame in enumerate(page.frames):
        try:
            body = frame.locator("body")
            if body.count():
                evidence[f"frame-{frame_index}.html"] = str(body.evaluate(html_script)).encode()
        except Error:
            continue
    return redact_secrets(evidence)


REDACTED = b"[redacted]"


def _attribute_escaped(text: str, *, angles: bool) -> str:
    """How Chrome writes text into an attribute: & and " escaped, ' left; newer Chrome
    escapes < and > too."""
    escaped = text.replace("&", "&amp;").replace('"', "&quot;").replace("\xa0", "&nbsp;")
    return escaped.replace("<", "&lt;").replace(">", "&gt;") if angles else escaped


def redact_secrets(evidence: dict[str, bytes]) -> dict[str, bytes]:
    """Every text entry with each secret read so far replaced, raw or escaped."""
    forms = sorted(
        {
            form.encode()
            for secret in loaded_values()
            for form in (
                secret,
                json.dumps(secret)[1:-1],
                html.escape(secret, quote=True),
                html.escape(secret, quote=False),
                _attribute_escaped(secret, angles=False),
                _attribute_escaped(secret, angles=True),
            )
            if form
        },
        key=len,
        reverse=True,
    )
    if not forms:
        return evidence
    redacted: dict[str, bytes] = {}
    for name, data in evidence.items():
        if not name.endswith(".png"):
            for form in forms:
                data = data.replace(form, REDACTED)
        redacted[name] = data
    return redacted


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


CLEAR_OUTLINES_SCRIPT = """
() => {
  for (const node of document.querySelectorAll('[data-personal-os-outline]')) {
    node.style.outline = '';
    node.style.outlineOffset = '';
    node.removeAttribute('data-personal-os-outline');
  }
}
"""
# A control nobody can see (a hidden input styled over, a file in a closed
# panel) is outlined by the nearest part of the field that shows.
OUTLINE_SCRIPT = """
element => {
  let node = element;
  while (node && !node.getClientRects().length) node = node.parentElement;
  if (!node || node === document.body || node === document.documentElement) return;
  node.style.outline = '2px solid #f59e0b';
  node.style.outlineOffset = '2px';
  node.setAttribute('data-personal-os-outline', '');
}
"""


def highlight_unfilled(page: Page, fields: list[dict[str, Any]], questions: set[str]) -> None:
    """Outline exactly the questions left for you, and nothing the audit did not list.

    A choice is outlined option by option. Emptiness is the audit's call, never
    the page's: JazzHR names each Yes/No box apart, so a box can be unchecked
    on a question that is answered.
    """
    for frame in page.frames:
        with contextlib.suppress(Error):
            frame.evaluate(CLEAR_OUTLINES_SCRIPT)
    for field in fields:
        if question_of(field) not in questions:
            continue
        with contextlib.suppress(Error, RuntimeError):
            fresh_control(page, field).evaluate(OUTLINE_SCRIPT, timeout=CONTROL_TIMEOUT_MS)
