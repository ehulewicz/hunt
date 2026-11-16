"""The only code that presses Next or Submit, on an `os hunt apply --submit` run alone.

Without the flag the run never comes here and you own navigation. With it, a
step that has been filled and audited goes through `press_forward`: wait for the
fixer, audit again, stop if anything that matters is still wrong, save what the
page holds, press the one button there is, and read what the page did.

| the page                                | the run                                  |
|-----------------------------------------|------------------------------------------|
| shows a confirmation                    | submitted, through the normal path       |
| shows the next step of the form         | fills it, audits it, comes back here     |
| shows validation errors                 | audits again, presses again, three times |
| asks for a mailed code                  | reads and enters it, three times         |
| opens an image puzzle                   | parks: it never touches one              |
| (before the press) has a captcha box    | parks, filled: the tick is yours         |
| does not change, after Next             | holds                                    |
| does not change, after Submit           | unconfirmed: it may have gone in         |
| moves on with no confirmation           | unconfirmed: it may have gone in         |
| says its captcha refused the Submit     | captcha rejected: never pressed again    |

An unconfirmed application is never pressed again, by this run or by a loop that
reads its exit code, so nothing goes in twice. Neither is a captcha-rejected
one (`CAPTCHA_REJECTED`): the ATS may have cleared the form, and nothing
refills it. The press is outside `armed` (`jobs/safety.py`) because the guards
exist to cancel exactly this click. The fixer's files cannot import this
module, and its probes stay guarded.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from playwright.sync_api import Error, Locator

from personal_os_worker.core.config import console
from personal_os_worker.core.secrets import GMAIL_APP_PASSWORD, read_secret
from personal_os_worker.core.wording import CAPTCHA_REJECTED, SUBMIT_CONTROL
from personal_os_worker.jobs.account_inspect import CODE_WORDS
from personal_os_worker.jobs.account_page import CODE_INPUTS, fill_code
from personal_os_worker.jobs.audit import Finding
from personal_os_worker.jobs.autofill import is_cover_letter_field, is_long_form_field
from personal_os_worker.jobs.browser import field_errors, inspect_fields
from personal_os_worker.jobs.mail import (
    Inbox,
    MailUnavailable,
    sender_hints,
    verification_code,
)
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    classify_application_page,
)
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.registry.strategies import captcha_challenge_present
from personal_os_worker.jobs.safety import SUBMITTING

if TYPE_CHECKING:
    from personal_os_worker.jobs.session.base import SessionBase as _Base
else:
    _Base = object  # `jobs/session/driver.py` mixes this in; session imports it, not the reverse

MAX_PRESSES = 3
MAX_CODE_TRIES = 3
MAX_STEPS = 25
# How long a page has to answer a press, and to hold still once it has moved.
PRESS_WAIT_SECONDS = 30.0
MOVED_SETTLE_SECONDS = 3.0
# An application waits this long for the fixer on one problem, then waits for the second pass.
FIXER_WAIT_SECONDS = 15 * 60.0
# A wall nothing is working on: time for the page to answer whatever was last tried.
WALL_WAIT_SECONDS = 60.0
MAIL_WAIT_SECONDS = 180.0

_NEXT = re.compile(
    r"^\s*(?:next(?:\s+(?:step|page))?|continue(?:\s+to\s+.{0,40})?|proceed"
    r"|save\s*(?:and|&)\s*(?:continue|next)"
    r"|review(?:\s+(?:and|&)\s+submit|\s+application)?)\s*[>\u203a\u2192]?\s*$",
    re.IGNORECASE,
)
# On a page already asking for a code, this is the button that sends it.
_VERIFY = re.compile(r"^\s*(?:verify|confirm)\b", re.IGNORECASE)
_NEVER = re.compile(r"\banother\b", re.IGNORECASE)
_PRESSABLE = re.compile(
    "|".join(pattern.pattern for pattern in (_NEXT, SUBMIT_CONTROL, SUBMITTING)), re.IGNORECASE
)
_PRESSABLE_FOR_CODE = re.compile(f"{_PRESSABLE.pattern}|{_VERIFY.pattern}", re.IGNORECASE)
_PAGE_TEXT_LIMIT = 200
_CAPTCHA_BOX_FRAME = re.compile(r"recaptcha/(?:api2|enterprise)/anchor|hcaptcha.*frame=checkbox")
_CAPTCHA_BOX = "#recaptcha-anchor, #checkbox"


class RunEnded(Exception):
    """This `--submit` run ends here without the application having gone in, as far as it knows."""

    outcome = "held"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ApplicationHeld(RunEnded):
    """Something is wrong that a retry may fix; the application refills from the top."""


class ApplicationParked(RunEnded):
    """A puzzle only you can clear; the tab waits, and a rerun picks it up."""

    outcome = "parked"


class ApplicationUnconfirmed(RunEnded):
    """It may have gone in: look before anything presses again."""

    outcome = "unconfirmed"


class ApplicationRejected(RunEnded):
    """The ATS's captcha refused the press: a failure, never pressed or refilled again."""

    outcome = "captcha_rejected"


class StallClock:
    """How long a run nobody is watching stands at a wall or a missing posting.

    It waits while the fixer works, up to the limit per problem, then briefly.
    """

    def __init__(self, busy: Callable[[], str | None]) -> None:
        self.busy = busy
        self.on: str | None = None
        self.since: float | None = None

    def check(self, why: str) -> None:
        """Raises `ApplicationHeld` with `why` once waiting no longer helps."""
        busy, now = self.busy(), time.monotonic()
        if self.since is None or busy != self.on:
            self.on, self.since = busy, now
            return
        limit = FIXER_WAIT_SECONDS if busy is not None else WALL_WAIT_SECONDS
        if now - self.since >= limit:
            raise ApplicationHeld(why)


def open_inbox(email: str) -> Inbox:
    return Inbox(email, read_secret(GMAIL_APP_PASSWORD))


def blocking(findings: list[Finding], fields: list[dict[str, Any]]) -> list[Finding]:
    """What stops a press. Only two things do not.

    An optional short question with no fact is listed and left, as a widget with
    no question nothing can answer. An error the page shows on a field that holds
    an answer waits for the press limit, since the press is what makes a page
    look again.
    """
    return [item for item in findings if not _passes(item, fields)]


def _passes(item: Finding, fields: list[dict[str, Any]]) -> bool:
    evidence = item.evidence or {}
    if evidence.get("page_error"):
        return True
    if item.required:
        return False
    if evidence.get("unlabelled"):
        return True
    return item.cause in {"fact_gap", "pattern_gap"} and _short_question(item.question, fields)


def _short_question(question: str, fields: list[dict[str, Any]]) -> bool:
    wanted = normalize_answer_question(question)
    for item in fields:
        if normalize_answer_question(question_of(item)) == wanted:
            return not (
                is_long_form_field(item)
                or is_cover_letter_field(item)
                or str(item.get("type") or "").casefold() == "file"
            )
    return True


def _name(button: Locator) -> str:
    text = button.get_attribute("aria-label") or button.inner_text() or (
        button.get_attribute("value") or ""
    )
    return " ".join(str(text).split())


@dataclass(frozen=True, slots=True)
class _Look:
    """The page, as far as telling whether a press did anything."""

    signature: tuple[str, tuple[str, ...]]
    errors: tuple[tuple[str, str], ...]
    code_boxes: int


class SubmitMixin(_Base):
    _stall: StallClock | None = None

    # ----- the press ---------------------------------------------------------

    def press_forward(
        self, fields: list[dict[str, Any]], result: dict[str, Any]
    ) -> ApplicationPageState:
        """Press this step forward; how the page answered, or the run ends here."""
        if self.state.step >= MAX_STEPS:
            raise ApplicationHeld(f"still filling at step {self.state.step}; it looks like a loop")
        presses = 0
        while True:
            self._settle(fields, result)
            self._save_before_press()
            before = self._look()
            since = datetime.now(UTC)
            kind = self._press()
            presses += 1
            self.state.pressed_step = self.state.step
            answer = self._read_press(before, kind)
            answer = self._through_mailed_codes(answer, since)
            if isinstance(answer, ApplicationPageState):
                return answer
            if presses >= MAX_PRESSES:
                raise ApplicationHeld(
                    f"the page still refuses it after {presses} presses: {self._errors_said()}"
                )

    def _settle(self, fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """Audit until the fixer has nothing left to work on, then stop on what remains."""
        self._await_fixer()
        findings = self.audit_and_repair(fields, result)
        while self._fixer_busy() is not None:
            self._await_fixer()
            findings = self.audit_and_repair(fields, result)
        current = inspect_fields(self.page)
        stops = blocking(findings, current)
        if stops:
            said = "; ".join(f"{item.question}: {item.cause} ({item.note})" for item in stops)
            raise ApplicationHeld(said)
        letter = self.state.cover_letter
        if letter is not None and not letter.strip():
            raise ApplicationHeld("the cover letter still has an open [slot]")
        if self._captcha_box_open():
            raise ApplicationParked(
                "the form is filled and an \"I'm not a robot\" box is yours to tick"
            )

    def _captcha_box_open(self) -> bool:
        """Whether a visible captcha box still waits for its tick; nothing here ever ticks it."""
        for frame in self.page.frames:
            if not _CAPTCHA_BOX_FRAME.search(frame.url) or "size=invisible" in frame.url:
                continue
            try:
                checked = frame.locator(_CAPTCHA_BOX).first.get_attribute(
                    "aria-checked", timeout=1_000
                )
                if checked != "true" and frame.frame_element().is_visible():
                    return True
            except Error:
                continue
        return False

    def _fixer_busy(self) -> str | None:
        return self.ctx.fixes.busy() if self.ctx.fixes is not None else None

    def _await_fixer(self) -> None:
        """Serve the fixer's safe points while it works, up to the limit per problem."""
        working: str | None = None
        since = time.monotonic()
        while (busy := self._fixer_busy()) is not None:
            if busy != working:
                working, since = busy, time.monotonic()
            if time.monotonic() - since >= FIXER_WAIT_SECONDS:
                raise ApplicationHeld(f"the fixer is still on {busy} after the wait for it ran out")
            self.sleep()

    def _save_before_press(self) -> None:
        """What the page holds, recorded now: after the press it may hold nothing."""
        self.record_snapshot(inspect_fields(self.page))
        self.save_now()
        if self.state.withheld:
            raise ApplicationHeld(
                "the page holds a choice a fact contradicts: "
                + ", ".join(sorted(self.state.withheld.values()))
            )

    def press_target(self, *, for_code: bool = False) -> tuple[Locator, str]:
        """The one button to press and its name; anything but exactly one holds the run."""
        words = _PRESSABLE_FOR_CODE if for_code else _PRESSABLE
        found: list[tuple[Locator, str]] = []
        for frame in self.page.frames:
            buttons = frame.get_by_role("button", name=words)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                try:
                    if not button.is_visible():
                        continue
                    name = _name(button)
                except Error:
                    continue
                if not _NEVER.search(name):
                    found.append((button, name))
        if len(found) != 1:
            names = ", ".join(repr(name) for _button, name in found) or "none"
            raise ApplicationHeld(f"expected one button to press, found {len(found)}: {names}")
        button, name = found[0]
        if not button.is_enabled():
            raise ApplicationHeld(f"the button to press is disabled: {name!r}")
        return button, name

    def _press(self, *, for_code: bool = False) -> str:
        """Press the one button; "submit" when it may send the application, else "next"."""
        button, name = self.press_target(for_code=for_code)
        kind = "next" if _NEXT.search(name) else "submit"
        console.print(f"  [bold]pressing[/bold] {name}")
        if kind == "submit":
            self.state.submit_pressed = True
        try:
            button.click(timeout=5_000)
        except Error as error:
            raise ApplicationHeld(f"could not press {name!r}: {str(error)[:200]}") from error
        return kind

    # ----- what the page did -------------------------------------------------

    def _look(self, current: list[dict[str, Any]] | None = None) -> _Look:
        fields = inspect_fields(self.page) if current is None else current
        return _Look(
            signature=(self.page.url, tuple(str(item.get("fingerprint")) for item in fields)),
            errors=tuple(sorted(
                (str(error["question"]), str(error["message"]))
                for error in field_errors(self.page)
            )),
            code_boxes=self._code_boxes(),
        )

    def _code_boxes(self) -> int:
        """Boxes a page shows for a mailed code; none unless its words say that is what they are."""
        boxes = 0
        for frame in self.page.frames:
            try:
                for selector in (CODE_INPUTS, 'input[maxlength="1"]'):
                    found = frame.locator(selector)
                    boxes += sum(found.nth(i).is_visible() for i in range(found.count()))
                if boxes and CODE_WORDS.search(frame.locator("body").inner_text(timeout=1_000)):
                    return boxes
            except Error:
                continue
            boxes = 0
        return 0

    def _read_press(
        self, before: _Look, kind: str, *, code_asked: bool = False
    ) -> ApplicationPageState | str:
        """How the page answered: a state to go on from, "errors", or "code"; else the run ends."""
        started = time.monotonic()
        moved_since: float | None = None
        while True:
            waited = time.monotonic() - started
            self.page = self.ctx.browser.active_page(self.page)
            current = inspect_fields(self.page)
            state = classify_application_page(self.page, current, self.state.navigation)
            if state == ApplicationPageState.SUBMITTED:
                return state
            if kind == "submit" and CAPTCHA_REJECTED.search(self._page_text()):
                raise ApplicationRejected(
                    f"the captcha refused the press; the form may be cleared: {self._page_says()}"
                )
            if captcha_challenge_present(self.page):
                raise ApplicationParked("an image puzzle is open; the tab is waiting for you")
            look = self._look(current)
            timed_out = waited >= PRESS_WAIT_SECONDS
            if look.code_boxes > before.code_boxes or (
                code_asked and look.code_boxes and timed_out
            ):
                return "code"
            if look.errors and (look.errors != before.errors or timed_out):
                return "errors"
            moved = look.signature != before.signature
            if moved and state in {ApplicationPageState.FORM, ApplicationPageState.ACCOUNT}:
                moved_since = moved_since or time.monotonic()
                if time.monotonic() - moved_since >= MOVED_SETTLE_SECONDS:
                    return self._moved_on(state, before, look, kind)
            else:
                moved_since = None
            if timed_out:
                self._gave_no_answer(moved, kind)
            self.sleep()

    def _moved_on(
        self, state: ApplicationPageState, before: _Look, look: _Look, kind: str
    ) -> ApplicationPageState:
        if state == ApplicationPageState.ACCOUNT or kind == "next":
            return state
        # A form after Submit is a next step only if it asks for something new.
        if not look.signature[1] or look.signature[1] == before.signature[1]:
            raise ApplicationUnconfirmed(
                f"the page moved after Submit with nothing new to fill: {self._page_says()}"
            )
        return state

    def _gave_no_answer(self, moved: bool, kind: str) -> None:
        if moved:
            raise ApplicationUnconfirmed(
                f"the page moved on with no confirmation I recognize: {self._page_says()}"
            )
        said = f"the form did not change {PRESS_WAIT_SECONDS:.0f}s after the press, with no error"
        if kind == "submit":
            raise ApplicationUnconfirmed(said)
        raise ApplicationHeld(said)

    def _page_text(self) -> str:
        try:
            return " ".join(self.page.locator("body").inner_text(timeout=2_000).split())
        except Error:
            return ""

    def _page_says(self) -> str:
        return self._page_text()[:_PAGE_TEXT_LIMIT] or "(the page could not be read)"

    def _errors_said(self) -> str:
        try:
            return "; ".join(
                sorted({str(error["message"]) for error in field_errors(self.page)})
            ) or "no error it could read"
        except Error:
            return "no error it could read"

    # ----- a mailed code -----------------------------------------------------

    def _through_mailed_codes(
        self, answer: ApplicationPageState | str, since: datetime
    ) -> ApplicationPageState | str:
        tries = 0
        while answer == "code":
            if tries >= MAX_CODE_TRIES:
                raise ApplicationHeld(f"the page still asks for its code after {tries} tries")
            tries += 1
            self._enter_code(since)
            since = datetime.now(UTC)
            before = self._look()
            kind = self._press(for_code=True)
            answer = self._read_press(before, kind, code_asked=True)
        return answer

    def _enter_code(self, since: datetime) -> None:
        address = str(self.ctx.knowledge.facts.get("contact.email") or "").strip()
        host = urlsplit(self.page.url).hostname or ""
        try:
            message = open_inbox(address).await_message(
                since=since, hints=sender_hints(host, self.ctx.company), timeout=MAIL_WAIT_SECONDS
            )
        except (MailUnavailable, RuntimeError, OSError) as error:
            raise ApplicationHeld(f"the page asks for a mailed code: {error}") from error
        if message is None:
            raise ApplicationHeld("the page asks for a mailed code that never came")
        code = verification_code(message)
        if code is None or not fill_code(self.page, code):
            raise ApplicationHeld("the page asks for a mailed code I could not read or enter")
        console.print("  [dim]entered the mailed code[/dim]")

    # ----- a wall in front of the form ---------------------------------------

    def stalled(self) -> None:
        """Called each poll a run stands at a wall: nobody is at this terminal to get it past.

        A puzzle parks it. Otherwise it waits while the fixer works, up to the
        limit per problem, and then briefly; without `--submit` a wall is yours
        and this does nothing.
        """
        if not self.ctx.submit:
            return
        if captcha_challenge_present(self.page):
            raise ApplicationParked("an image puzzle is open; the tab is waiting for you")
        self._stall = self._stall or StallClock(self._fixer_busy)
        walls = self.state.walls
        self._stall.check(walls[-1].note if walls else "no form came up")
