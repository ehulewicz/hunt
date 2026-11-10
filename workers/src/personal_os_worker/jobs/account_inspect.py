"""Recognizing an account page from every frame: its view, its error, Google, a captcha."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.sync_api import Error, Page

from personal_os_worker.core.page_scripts import load
from personal_os_worker.jobs.registry.strategies import captcha_challenge_present

AccountView = Literal[
    "email", "sign_in", "create", "forgot", "new_password", "code", "check_email"
]
AccountError = Literal["no_account", "wrong_password", "exists", "password_rule", "other"]

SIGN_IN_WORDS = re.compile(r"\b(?:sign[ -]?in|log[ -]?in|signin|login)\b", re.IGNORECASE)
CREATE_WORDS = re.compile(
    r"\bcreate (?:an |a |your )?(?:candidate )?account\b|\bsign[ -]?up\b|\bregister\b"
    r"|\bnew (?:user|candidate)\b",
    re.IGNORECASE,
)
FORGOT_WORDS = re.compile(
    r"\bforgot(?:ten)? (?:your )?password\b|\breset (?:your )?password\b"
    r"|\btrouble signing in\b",
    re.IGNORECASE,
)
GOOGLE_WORDS = re.compile(
    r"\b(?:sign[ -]?in|continue|log[ -]?in|sign[ -]?up)\s+(?:with|using|via)\s+google\b",
    re.IGNORECASE,
)
NO_ACCOUNT_WORDS = re.compile(
    r"(?:couldn't|could not|can't|cannot|unable to) find (?:an |your |that |the )?account"
    r"|\bno account\b|\baccount (?:does not|doesn't) exist\b"
    r"|\b(?:user|email|account) (?:was )?not found\b|\b(?:is not|isn't|not) registered\b",
    re.IGNORECASE,
)
WRONG_PASSWORD_WORDS = re.compile(
    r"\b(?:wrong|incorrect|invalid)\b.{0,30}\bpassword\b"
    r"|\bpassword\b.{0,20}\b(?:incorrect|wrong|invalid|did not match|didn't match)\b"
    r"|\b(?:wrong|incorrect|invalid) (?:credentials|login|sign[ -]?in)\b"
    r"|\b(?:sign[ -]?in|login) (?:failed|unsuccessful)\b",
    re.IGNORECASE,
)
EXISTS_WORDS = re.compile(
    r"\balready (?:exists|registered|in use|taken|been registered|associated"
    r"|have an account|has an account)\b"
    r"|\b(?:account|user) with (?:this|that) (?:email|e-mail)(?: address)?\b.{0,30}\bexists?\b"
    r"|\bexisting account\b",
    re.IGNORECASE,
)
PASSWORD_RULE_WORDS = re.compile(
    r"\bpassword\b.{0,40}\b(?:must|should|requires?|needs?|at least|does not meet"
    r"|doesn't meet|too (?:short|weak|common))\b"
    r"|\bmust (?:contain|include|have) (?:at least|an? |one )|\bat least \d+ characters\b"
    r"|\bpassword requirements\b|\bnot strong enough\b",
    re.IGNORECASE,
)
CHECK_EMAIL_WORDS = re.compile(
    r"\bcheck your (?:e-?mail|inbox)\b|\bverify your (?:e-?mail|account)\b"
    r"|\b(?:we(?:'ve| have)|was|been) sent\b.{0,60}\b(?:e-?mail|link|code)\b"
    r"|\bverification (?:e-?mail|link)\b|\bconfirm your (?:e-?mail|account)\b",
    re.IGNORECASE,
)
CODE_WORDS = re.compile(
    r"\b(?:verification|security|one[- ]time|confirmation|access) code\b"
    r"|\benter (?:the )?code\b",
    re.IGNORECASE,
)
# The account exists but must be verified before it can sign in (Workday's alert).
VERIFY_FIRST_WORDS = re.compile(
    r"\bverify your (?:account|e-?mail(?: address)?) before\b"
    r"|\b(?:account|e-?mail(?: address)?) (?:is not|isn't|has not been|hasn't been|not yet)"
    r" (?:been |yet )?verified\b",
    re.IGNORECASE,
)
RESEND_WORDS = re.compile(
    r"\bresend\b.{0,30}\bverification\b|\bresend (?:the |a )?(?:e-?mail|link|code)\b",
    re.IGNORECASE,
)
NEW_PASSWORD_WORDS = re.compile(
    r"\b(?:new|reset|set|choose|create) (?:a |your )?(?:new )?password\b", re.IGNORECASE
)
# The way on from an email box that stands alone: iCIMS asks for the email first
# and answers with a password page or a new profile.
EMAIL_STEP_WORDS = re.compile(r"\b(?:next|continue)\b", re.IGNORECASE)
# An email box that signs you up for alerts or news is not an account.
NOT_AN_ACCOUNT = re.compile(
    r"\b(?:job )?alerts?\b|\bnewsletter\b|\bsubscribe\b|\btalent (?:community|network)\b"
    r"|\bnotif(?:y|ications?)\b",
    re.IGNORECASE,
)
# Most specific first: a rule message often also says "invalid password".
ERROR_WORDS: tuple[tuple[AccountError, re.Pattern[str]], ...] = (
    ("password_rule", PASSWORD_RULE_WORDS),
    ("exists", EXISTS_WORDS),
    ("no_account", NO_ACCOUNT_WORDS),
    ("wrong_password", WRONG_PASSWORD_WORDS),
)
ERROR_LIKE = re.compile(
    r"\b(?:error|invalid|incorrect|failed|try again|problem|unable|not valid|not allowed)\b",
    re.IGNORECASE,
)

ACCOUNT_SCRIPT = load("account_signals")


@dataclass(frozen=True, slots=True)
class AccountPage:
    view: AccountView
    google: bool
    error: AccountError | None
    error_text: str
    captcha: bool
    # The site's own alert says to verify the account first (a standing hint does not).
    verify_first: bool = False


@dataclass(slots=True)
class _Seen:
    passwords: int = 0
    emails: int = 0
    codes: int = 0
    google: int = 0
    captcha: int = 0
    controls: list[str] = field(default_factory=list)
    scopes: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    automation: set[str] = field(default_factory=set)
    text: str = ""


def _gather(page: Page) -> _Seen:
    """The account signals of every frame, summed: iCIMS signs in inside an iframe."""
    seen = _Seen()
    for frame in page.frames:
        try:
            found: dict[str, Any] = frame.evaluate(ACCOUNT_SCRIPT)
        except Error:
            continue
        seen.passwords += int(found["passwords"])
        seen.emails += int(found["emails"])
        seen.codes += int(found["codes"])
        seen.google += int(found["google"])
        seen.captcha += int(found["captcha"])
        seen.controls += [str(text) for text in found["controls"]]
        seen.scopes += list(found["scopes"])
        seen.alerts += [str(text) for text in found["alerts"]]
        seen.headings += [str(text) for text in found["headings"]]
        seen.automation |= {str(name) for name in found["automation"]}
        seen.text = f"{seen.text}\n{found['text']}".strip()
    return seen


def _error(alerts: list[str]) -> tuple[AccountError | None, str]:
    """What the page's own error says, if it says anything."""
    for text in alerts:
        for kind, words in ERROR_WORDS:
            if words.search(text):
                return kind, text
    for text in alerts:
        if ERROR_LIKE.search(text):
            return "other", text
    return None, ""


def _account_scope(seen: _Seen) -> AccountView | None:
    """Sign in or create, when an email box's own form says which."""
    for scope in seen.scopes:
        if NOT_AN_ACCOUNT.search(scope.get("text", "")):
            continue
        controls = scope.get("controls", "")
        if SIGN_IN_WORDS.search(controls):
            return "sign_in"
        if CREATE_WORDS.search(controls):
            return "create"
    return None


def _email_step(seen: _Seen) -> bool:
    """An email box alone in its form with only a way on: a sign-in's first step."""
    return any(
        not scope.get("others")
        and not NOT_AN_ACCOUNT.search(scope.get("text", ""))
        and EMAIL_STEP_WORDS.search(scope.get("controls", ""))
        for scope in seen.scopes
    )


def _view(seen: _Seen) -> AccountView | None:
    ids = seen.automation
    heading = " | ".join(seen.headings)
    if "createAccountSubmitButton" in ids:
        return "create"
    if "verifyPassword" in ids:
        # Workday's password reset page confirms the new password the same way.
        return "create" if seen.emails else "new_password"
    if "signInSubmitButton" in ids:
        return "sign_in"
    if seen.passwords >= 2:
        if not seen.emails and NEW_PASSWORD_WORDS.search(heading):
            return "new_password"
        return "create"
    if seen.passwords == 1:
        if not seen.emails and NEW_PASSWORD_WORDS.search(heading):
            return "new_password"
        if CREATE_WORDS.search(heading) and not SIGN_IN_WORDS.search(heading):
            return "create"
        return "sign_in"
    if seen.codes and CODE_WORDS.search(seen.text):
        return "code"
    if seen.emails and FORGOT_WORDS.search(heading):
        return "forgot"
    if CHECK_EMAIL_WORDS.search(heading) or (
        not seen.emails and len(seen.text) < 1500 and CHECK_EMAIL_WORDS.search(seen.text)
    ):
        return "check_email"
    scoped = _account_scope(seen) if seen.emails else None
    if scoped is not None:
        return scoped
    if seen.google:
        return "sign_in"
    if seen.emails and not seen.passwords and _email_step(seen):
        return "email"
    return None


def inspect_account(page: Page) -> AccountPage | None:
    """The account page this is, or None when nothing on it is an account wall."""
    seen = _gather(page)
    google = bool(seen.google) or any(GOOGLE_WORDS.search(text) for text in seen.controls)
    seen.google = int(google)
    view = _view(seen)
    if view is None:
        return None
    error, error_text = _error(seen.alerts)
    return AccountPage(
        view=view,
        google=google,
        error=error,
        error_text=error_text,
        captcha=bool(seen.captcha) or captcha_challenge_present(page),
        verify_first=any(VERIFY_FIRST_WORDS.search(text) for text in seen.alerts),
    )
