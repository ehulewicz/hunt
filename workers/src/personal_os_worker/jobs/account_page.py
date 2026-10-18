"""The account wall: an ATS sign-in, create-account or verify page, perceived.

A page is an account page only when it is not already an application form
(`page_state.classify_application_page` checks the form first), so a real form
with an "Apply with Google" or "save your progress" password extra stays a
form. Workday's `data-automation-id`s are read where they exist; elsewhere the
wording below decides, grown from captured `account_wall` failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.browser import captcha_challenge_present

AccountView = Literal["sign_in", "create", "forgot", "new_password", "code", "check_email"]
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
NEW_PASSWORD_WORDS = re.compile(
    r"\b(?:new|reset|set|choose|create) (?:a |your )?(?:new )?password\b", re.IGNORECASE
)
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

ACCOUNT_SCRIPT = """
() => {
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const words = element => String(
    element.innerText || element.textContent || element.getAttribute('aria-label')
    || element.getAttribute('value') || element.getAttribute('title') || ''
  ).trim().replace(/\\s+/g, ' ');
  const all = selector => [...document.querySelectorAll(selector)].filter(visible);
  const CONTROLS = 'button, [role="button"], a[href], input[type="submit"], input[type="button"]';
  const passwords = all('input[type="password"]');
  const emails = all(
    'input[type="email"], input[autocomplete="email"], input[autocomplete="username"],'
    + ' input[data-automation-id="email"], input[name*="email" i], input[id*="email" i]'
  ).filter(input => !['hidden', 'password', 'checkbox', 'radio'].includes(
    String(input.type || '').toLowerCase()
  ));
  const codes = all(
    'input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i],'
    + ' input[data-automation-id*="code" i]'
  );
  // What an email box's own form offers, with the words around it.
  const scopes = emails.map(input => {
    let scope = input.closest('form');
    if (!scope) {
      scope = input.parentElement;
      for (let depth = 0; scope && depth < 6 && !scope.querySelector(CONTROLS); depth += 1) {
        scope = scope.parentElement;
      }
    }
    if (!scope) return { controls: '', text: '' };
    return {
      controls: [...scope.querySelectorAll(CONTROLS)].filter(visible).map(words).join(' | '),
      text: words(scope).slice(0, 400),
    };
  });
  const alerts = all(
    '[role="alert"], [aria-live="assertive"], [data-automation-id="errorMessage"],'
    + ' [data-automation-id*="error" i], [class*="error" i]'
  ).filter(element => !element.querySelector('input, button, select, textarea'))
    .map(words).filter(text => text && text.length <= 300);
  return {
    passwords: passwords.length,
    emails: emails.length,
    codes: codes.length,
    controls: all(CONTROLS).map(words).filter(Boolean).map(text => text.slice(0, 80)),
    scopes,
    alerts: [...new Set(alerts)].slice(0, 10),
    headings: all('h1, h2, h3, [role="heading"], legend').map(words).filter(Boolean).slice(0, 10),
    automation: [...new Set(all('[data-automation-id]').map(
      element => element.getAttribute('data-automation-id')
    ))],
    google: all(
      'iframe[src*="accounts.google.com/gsi" i], iframe[title*="sign in with google" i]'
    ).length,
    captcha: all(
      'iframe[src*="recaptcha" i][src*="anchor" i]:not([src*="size=invisible" i]),'
      + ' iframe[src*="hcaptcha" i][src*="frame=checkbox" i]'
    ).length,
    text: String(document.body?.innerText || '').slice(0, 3000),
  };
}
"""


@dataclass(frozen=True, slots=True)
class AccountPage:
    view: AccountView
    google: bool
    error: AccountError | None
    error_text: str
    captcha: bool


@dataclass(slots=True)
class _Seen:
    passwords: int = 0
    emails: int = 0
    codes: int = 0
    google: int = 0
    captcha: int = 0
    controls: list[str] = field(default_factory=list)
    scopes: list[dict[str, str]] = field(default_factory=list)
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


def _view(seen: _Seen) -> AccountView | None:
    ids = seen.automation
    heading = " | ".join(seen.headings)
    if "verifyPassword" in ids or "createAccountSubmitButton" in ids:
        return "create"
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
    )
