"""Getting past an ATS account wall: Google, then sign in, create or reset.

Deterministic and stateless: no model call, nothing stored. Each step runs at
most once per wall, so a site that keeps refusing parks the run with what it
said instead of looping. The password comes from Secret Manager when a step
first needs it and is never put in a reason, a log line or a fact.

Workday answers a wrong password and a missing account with the same "wrong
email or password", so a refused sign-in tries to create the account first;
"already exists" is what sends the flow to the password reset.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from enum import StrEnum
from urllib.parse import urlsplit

from playwright.sync_api import Error, Page

from personal_os_worker.core.secrets import (
    ATS_PASSWORD,
    GMAIL_APP_PASSWORD,
    SecretUnavailable,
    loaded_values,
    read_secret,
)
from personal_os_worker.jobs.account_page import (
    AccountPage,
    click_account_control,
    fill_code,
    fill_credentials,
    inspect_account,
    tick_account_terms,
)
from personal_os_worker.jobs.browser import CONTROL_TIMEOUT_MS
from personal_os_worker.jobs.mail import (
    Inbox,
    MailKind,
    MailUnavailable,
    action_link,
    sender_hints,
    verification_code,
)

GOOGLE_HOST = "accounts.google.com"
# What Google shows when it wants more than a tile click: those are yours.
GOOGLE_CHALLENGE = re.compile(
    r"verify it[\u2019']s you|2-step verification|enter your password|confirm it[\u2019']s you"
    r"|try another way|couldn[\u2019']t sign you in",
    re.IGNORECASE,
)
GOOGLE_ACCEPT = re.compile(r"^\s*(?:continue|allow|confirm|next)\s*$", re.IGNORECASE)
NAVIGATION_TIMEOUT_MS = 30_000
POLL_MS = 250
# Every step runs once; this bounds the loop that dispatches them.
MAX_STEPS = 16
REASON_LIMIT = 300


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".").casefold()


def _plain_https(link: str) -> bool:
    """A mailed link Python and the browser read the same way.

    A backslash, whitespace or a control character, or an "@" before the path,
    lets the two parsers disagree about the host, so such a link is refused.
    """
    if "\\" in link or any(character.isspace() or ord(character) < 32 for character in link):
        return False
    parts = urlsplit(link)
    return parts.scheme == "https" and "@" not in parts.netloc and bool(parts.hostname)


class AccountOutcome(StrEnum):
    SIGNED_IN = "signed_in"
    PARKED = "parked"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AccountResult:
    outcome: AccountOutcome
    reason: str
    created: bool = False


class _Finished(Exception):
    """Ends the chain early with a result; control flow, not an error."""

    def __init__(self, result: AccountResult) -> None:
        self.result = result


@dataclass(slots=True)
class _Run:
    page: Page
    start_url: str
    done: set[str] = field(default_factory=set)
    created: bool = False
    exists: bool = False
    mail_since: datetime | None = None


class AccountFlow:
    """Signs in to the ATS as the account email, by whatever way the wall allows."""

    def __init__(
        self,
        *,
        email: str,
        company: str,
        password: Callable[[], str] = lambda: read_secret(ATS_PASSWORD),
        inbox: Callable[[], Inbox] | None = None,
        settle_ms: int = 8_000,
        mail_timeout: float = 180.0,
    ) -> None:
        self.email = email
        self.company = company
        self._password_source = password
        self._password: str | None = None
        self._inbox_source = inbox or (lambda: Inbox(email, read_secret(GMAIL_APP_PASSWORD)))
        self._inbox: Inbox | None = None
        self.settle_ms = settle_ms
        self.mail_timeout = mail_timeout

    # ----- the chain ------------------------------------------------------------

    def run(self, page: Page) -> AccountResult:
        run = _Run(page=page, start_url=page.url)
        try:
            return self._chain(run)
        except _Finished as finished:
            return finished.result
        except SecretUnavailable as error:
            return self._result(run, AccountOutcome.UNAVAILABLE, str(error))
        except MailUnavailable as error:
            return self._result(run, AccountOutcome.PARKED, str(error))
        except Error as error:
            return self._result(
                run, AccountOutcome.PARKED, f"the account page broke off: {error.message}"
            )

    def _chain(self, run: _Run) -> AccountResult:
        for _ in range(MAX_STEPS):
            seen = inspect_account(run.page)
            if seen is None:
                if self._left_the_wall(run.page):
                    reason = "created the account and signed in" if run.created else "signed in"
                    return self._result(run, AccountOutcome.SIGNED_IN, reason)
                continue
            if not self._on_the_wall(run, run.page.url):
                # A late Google page, or a link that led elsewhere: not where the password goes.
                return self._result(
                    run,
                    AccountOutcome.PARKED,
                    f"the account page moved to {_host(run.page.url)}, off "
                    f"{_host(run.start_url)}; nothing typed there",
                )
            if seen.captcha:
                return self._result(run, AccountOutcome.PARKED, "captcha on the account page")
            if seen.error == "password_rule":
                return self._result(
                    run, AccountOutcome.PARKED, f"the site refused the password: {seen.error_text}"
                )
            step = self._next_step(run, seen)
            if step is None or step in run.done:
                # A slow site may still be answering the last step: look once more first.
                if self._answered(seen, self._settle(run.page, seen)):
                    continue
                return self._result(run, AccountOutcome.PARKED, self._stuck(seen, step))
            run.done.add(step)
            getattr(self, f"_{step}")(run, seen)
        return self._result(run, AccountOutcome.PARKED, "the account wall took too many steps")

    def _next_step(self, run: _Run, seen: AccountPage) -> str | None:
        """The one step this page calls for, given what has been tried."""
        if seen.google and seen.view in {"sign_in", "create"} and "google" not in run.done:
            return "google"
        if seen.view == "sign_in":
            if "new_password" in run.done:
                return "sign_in_after_reset"
            if run.exists:
                return "open_forgot"
            if "create" in run.done:
                return "sign_in_again"
            if "sign_in" not in run.done:
                return "sign_in"
            if seen.error in {"no_account", "wrong_password"}:
                return "open_create"
            return None
        if seen.view == "create":
            if seen.error == "exists" or run.exists:
                run.exists = True
                return "open_forgot"
            return "create"
        if seen.view in {"code", "check_email"}:
            return "verify"
        if seen.view == "forgot":
            return "request_reset"
        if seen.view == "new_password":
            return "new_password"
        return None

    def _stuck(self, seen: AccountPage, step: str | None) -> str:
        said = f": {seen.error_text}" if seen.error_text else ""
        if step is None:
            return f"nothing to do on the {seen.view.replace('_', ' ')} page{said}"
        return f"{step.replace('_', ' ')} did not get past the account page{said}"

    # ----- steps ----------------------------------------------------------------

    def _sign_in(self, run: _Run, seen: AccountPage) -> None:
        password = self.password()
        fill_credentials(run.page, email=self.email, password=password)
        self._submit(run, seen, "sign-in")

    _sign_in_again = _sign_in
    _sign_in_after_reset = _sign_in

    def _open_create(self, run: _Run, seen: AccountPage) -> None:
        if not click_account_control(run.page, "create"):
            raise self._park(run, f"no way to create an account after: {seen.error_text}")
        self._settle(run.page, seen)

    def _create(self, run: _Run, seen: AccountPage) -> None:
        password = self.password()
        fill_credentials(run.page, email=self.email, password=password)
        tick_account_terms(run.page)
        run.mail_since = datetime.now(UTC)
        after = self._submit(run, seen, "create-account")
        if after is None or (after.view != "create" and after.error is None):
            run.created = True

    def _verify(self, run: _Run, seen: AccountPage) -> None:
        message = self._await(run, "verify")
        code = verification_code(message)
        if seen.view == "code" and code is not None:
            if not fill_code(run.page, code):
                raise self._park(run, "could not type the mailed verification code")
            self._submit(run, seen, "verify")
            run.created = True
            return
        link = self._own_link(run, message, "verify")
        tab = run.page.context.new_page()
        try:
            tab.goto(link, timeout=NAVIGATION_TIMEOUT_MS)
            tab.wait_for_load_state(timeout=NAVIGATION_TIMEOUT_MS)
        finally:
            tab.close()
        run.created = True
        self._return_to_application(run)

    def _open_forgot(self, run: _Run, seen: AccountPage) -> None:
        if not click_account_control(run.page, "forgot"):
            if click_account_control(run.page, "sign_in"):
                self._settle(run.page, seen)
            if not click_account_control(run.page, "forgot"):
                raise self._park(run, "the account exists, and no forgot-password control")
        self._settle(run.page, seen)

    def _request_reset(self, run: _Run, seen: AccountPage) -> None:
        fill_credentials(run.page, email=self.email, password="")
        run.mail_since = datetime.now(UTC)
        self._submit(run, seen, "password-reset")
        message = self._await(run, "reset")
        link = self._own_link(run, message, "reset")
        run.page.goto(link, timeout=NAVIGATION_TIMEOUT_MS)
        self._settle(run.page, None)

    def _new_password(self, run: _Run, seen: AccountPage) -> None:
        fill_credentials(run.page, email=self.email, password=self.password())
        self._submit(run, seen, "new-password")
        self._return_to_application(run)

    def _google(self, run: _Run, seen: AccountPage) -> None:
        """Pick the account email's tile; anything more Google asks for falls through."""
        page = run.page
        before = list(page.context.pages)
        if not click_account_control(page, "google"):
            return
        google = self._google_page(page, before)
        if google is None:
            self._settle(page, seen)
            return
        if not self._challenged(google):
            self._pick_google_account(google)
        if self._on_google(google):
            self._back_from_google(run, google)
        self._settle(page, None)

    # ----- Google ---------------------------------------------------------------

    def _on_google(self, page: Page) -> bool:
        return not page.is_closed() and urlsplit(page.url).hostname == GOOGLE_HOST

    def _google_page(self, page: Page, before: list[Page]) -> Page | None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for candidate in page.context.pages:
                if candidate not in before and self._on_google(candidate):
                    return candidate
            if self._on_google(page):
                return page
            page.wait_for_timeout(POLL_MS)
        return None

    def _challenged(self, google: Page) -> bool:
        try:
            if google.locator('input[type="password"]').first.is_visible():
                return True
            return google.get_by_text(GOOGLE_CHALLENGE).first.is_visible()
        except Error:
            return False

    def _pick_google_account(self, google: Page) -> None:
        tile = google.locator(
            f'[data-identifier="{self.email}" i], [data-email="{self.email}" i]'
        ).first
        try:
            if not tile.is_visible():
                tile = google.get_by_text(self.email, exact=False).first
            tile.click(timeout=CONTROL_TIMEOUT_MS)
        except Error:
            return
        accepted = False
        deadline = time.monotonic() + self.settle_ms / 1000
        while time.monotonic() < deadline and self._on_google(google):
            if self._challenged(google):
                return
            if not accepted:
                accept = google.get_by_role("button", name=GOOGLE_ACCEPT).first
                try:
                    if accept.is_visible():
                        accept.click(timeout=CONTROL_TIMEOUT_MS)
                        accepted = True
                except Error:
                    pass
            google.wait_for_timeout(POLL_MS)

    def _back_from_google(self, run: _Run, google: Page) -> None:
        """Close Google's popup, or step the tab back to the account page."""
        if google is not run.page:
            if not google.is_closed():
                google.close()
            return
        for _ in range(3):
            if not self._on_google(run.page):
                return
            try:
                run.page.go_back(timeout=NAVIGATION_TIMEOUT_MS)
            except Error:
                break
        if self._on_google(run.page):
            run.page.goto(run.start_url, timeout=NAVIGATION_TIMEOUT_MS)

    # ----- shared pieces ----------------------------------------------------------

    def password(self) -> str:
        if self._password is None:
            self._password = self._password_source()
        return self._password

    def _mail(self) -> Inbox:
        if self._inbox is None:
            self._inbox = self._inbox_source()
        return self._inbox

    def _await(self, run: _Run, kind: MailKind) -> EmailMessage:
        since = run.mail_since or datetime.now(UTC) - timedelta(minutes=10)
        host = urlsplit(run.start_url).hostname or ""
        message = self._mail().await_message(
            since=since, hints=sender_hints(host, self.company), timeout=self.mail_timeout
        )
        if message is None:
            what = "verification" if kind == "verify" else "password reset"
            minutes = max(1, round(self.mail_timeout / 60))
            raise self._park(run, f"no {what} mail arrived within {minutes} min")
        return message

    def _on_the_wall(self, run: _Run, url: str) -> bool:
        """Whether a URL the browser reports is on the host the wall started on."""
        return bool(_host(url)) and _host(url) == _host(run.start_url)

    def _own_link(self, run: _Run, message: EmailMessage, kind: MailKind) -> str:
        """The mailed link, only when it leads back to the wall's own site.

        Sender matching is by name, which a forged mail can copy; the link's
        host is what decides where the password would be typed.
        """
        link = action_link(message, kind)
        what = "verification" if kind == "verify" else "reset"
        if link is None:
            raise self._park(run, f"the {what} mail had no link to open")
        if not _plain_https(link) or not self._on_the_wall(run, link):
            raise self._park(
                run,
                f"the {what} mail's link points to {_host(link) or 'an unreadable host'}, off "
                f"{_host(run.start_url)}; not followed",
            )
        return link

    def _submit(self, run: _Run, seen: AccountPage, what: str) -> AccountPage | None:
        if not click_account_control(run.page, "submit"):
            raise self._park(run, f"could not find the {what} button")
        return self._settle(run.page, seen)

    def _return_to_application(self, run: _Run) -> None:
        run.page.goto(run.start_url, timeout=NAVIGATION_TIMEOUT_MS)
        self._settle(run.page, None)

    def _settle(self, page: Page, before: AccountPage | None) -> AccountPage | None:
        """Wait for the page to answer the last action, up to the settle time.

        With a page to compare against, the answer is a change from it; after a
        navigation there is none, and a loaded page is the answer.
        """
        deadline = time.monotonic() + self.settle_ms / 1000
        while True:
            page.wait_for_timeout(POLL_MS)
            if self._loaded(page):
                seen = inspect_account(page)
                if before is None or self._answered(before, seen) or time.monotonic() >= deadline:
                    return seen
            elif time.monotonic() >= deadline:
                return inspect_account(page)

    @staticmethod
    def _answered(before: AccountPage, seen: AccountPage | None) -> bool:
        """Whether the page has answered: it left the wall, moved on, or says something new.

        An earlier error clearing at the click is not an answer. Workday clears
        it at once and moves on a moment later, and the flow must not park in
        between.
        """
        if seen is None or seen.view != before.view:
            return True
        return seen.error is not None and (seen.error, seen.error_text) != (
            before.error,
            before.error_text,
        )

    def _loaded(self, page: Page) -> bool:
        try:
            return bool(page.evaluate("document.readyState") == "complete")
        except Error:
            return False

    def _left_the_wall(self, page: Page) -> bool:
        """No account page, twice, on a loaded page: a navigation in between is not success."""
        page.wait_for_timeout(POLL_MS * 2)
        return self._loaded(page) and inspect_account(page) is None

    def _park(self, run: _Run, reason: str) -> _Finished:
        return _Finished(self._result(run, AccountOutcome.PARKED, reason))

    def _result(self, run: _Run, outcome: AccountOutcome, reason: str) -> AccountResult:
        return AccountResult(outcome, self._one_line(reason), created=run.created)

    def _one_line(self, reason: str) -> str:
        text = " ".join(reason.split())
        for secret in {self._password, *loaded_values()}:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text[:REASON_LIMIT]

