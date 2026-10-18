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

from personal_os_worker.jobs.autofill import is_consent_field
from personal_os_worker.jobs.browser import (
    CONTROL_TIMEOUT_MS,
    captcha_challenge_present,
    inspect_fields,
    looks_like_application_form,
)

AccountView = Literal["sign_in", "create", "forgot", "new_password", "code", "check_email"]
AccountError = Literal["no_account", "wrong_password", "exists", "password_rule", "other"]
AccountControl = Literal["sign_in", "create", "forgot", "google", "submit"]

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


# ----- operating the account page --------------------------------------------

# The application's own final button. Nothing here clicks it, account page or not.
APPLICATION_SUBMIT = re.compile(
    r"\b(?:submit|send|complete|finish)\b.*\b(?:application|form)\b", re.IGNORECASE
)
# Workday's ids for each control, tried before any wording.
WORKDAY_CONTROLS: dict[AccountControl, tuple[str, ...]] = {
    "sign_in": ("signInLink", "utilityButtonSignIn"),
    "create": ("createAccountLink",),
    "forgot": ("forgotPasswordLink",),
    "google": (),
    "submit": (),
}
WORKDAY_SUBMIT: dict[AccountView, tuple[str, ...]] = {
    "sign_in": ("signInSubmitButton",),
    "create": ("createAccountSubmitButton",),
}
OTHER_SIGN_IN = re.compile(r"\b(?:sign[ -]?in|log[ -]?in|sign[ -]?up)\s+(?:with|using|via)\b", re.I)
CONTROL_WORDS: dict[AccountControl, tuple[re.Pattern[str], re.Pattern[str] | None]] = {
    "sign_in": (SIGN_IN_WORDS, OTHER_SIGN_IN),
    "create": (CREATE_WORDS, OTHER_SIGN_IN),
    "forgot": (FORGOT_WORDS, None),
    "google": (GOOGLE_WORDS, None),
}
# What the account form's own button says on each view; a generic pass follows.
SUBMIT_WORDS: dict[AccountView, re.Pattern[str]] = {
    "sign_in": SIGN_IN_WORDS,
    "create": CREATE_WORDS,
    "forgot": re.compile(r"\b(?:send|submit|reset|continue|next|request)\b", re.I),
    "new_password": re.compile(
        r"\b(?:reset|save|submit|change|set|update|continue|confirm)\b", re.I
    ),
    "code": re.compile(r"\b(?:verify|submit|continue|confirm|next)\b", re.I),
    "check_email": re.compile(r"\b(?:continue|next|done|ok)\b", re.I),
}
GENERIC_SUBMIT = re.compile(
    r"\b(?:continue|next|submit|send|verify|confirm|save|done|sign[ -]?in|log[ -]?in)\b", re.I
)
# Not the form's own button: the forgot link ("Reset Password" is a reset page's button).
FORGOT_LINK = re.compile(r"\bforgot(?:ten)?\b|\btrouble signing in\b", re.IGNORECASE)
NOT_SUBMIT = re.compile(
    "|".join(
        (OTHER_SIGN_IN.pattern, FORGOT_LINK.pattern, r"\bresend\b", APPLICATION_SUBMIT.pattern)
    ),
    re.IGNORECASE,
)
# A checkbox that agrees to mail is not the account's terms, whatever it says.
MARKETING_WORDS = re.compile(
    r"\bmarketing\b|\bnewsletters?\b|\bpromotional\b|\bjob alerts?\b|\btext messages?\b"
    r"|\bsms\b|\breceive (?:e-?mails?|updates|news|communications)\b",
    re.IGNORECASE,
)
ACCOUNT_INPUTS = (
    'input[type="password"], input[type="email"], input[autocomplete="email"],'
    ' input[autocomplete="username"], input[data-automation-id="email"],'
    ' input[name*="email" i], input[id*="email" i], input[autocomplete="one-time-code"],'
    ' input[name*="code" i], input[id*="code" i], input[inputmode="numeric"]'
)
EMAIL_INPUTS = (
    'input[data-automation-id="email"], input[type="email"], input[autocomplete="email"],'
    ' input[autocomplete="username"], input[name*="email" i], input[id*="email" i]'
)
CODE_INPUTS = (
    'input[autocomplete="one-time-code"], input[data-automation-id*="code" i],'
    ' input[name*="code" i], input[id*="code" i]'
)
MARK = "data-personal-os-account"

TARGET_SCRIPT = """
({ ids, include, exclude, scoped, inputs }) => {
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const words = element => String(
    element.innerText || element.textContent || element.getAttribute('aria-label')
    || element.getAttribute('value') || ''
  ).trim().replace(/\\s+/g, ' ');
  const CONTROLS = 'button, [role="button"], a[href], input[type="submit"], input[type="button"]';
  for (const old of document.querySelectorAll('[MARK]')) old.removeAttribute('MARK');
  const mark = element => {
    // Workday lays a click_filter over its buttons, and the click has to land on it.
    const filter = element.parentElement
      ? element.parentElement.querySelector(':scope > [data-automation-id="click_filter"]')
      : null;
    (filter && visible(filter) ? filter : element).setAttribute('MARK', '1');
    return words(element) || element.getAttribute('data-automation-id') || 'control';
  };
  for (const id of ids) {
    const element = [...document.querySelectorAll(`[data-automation-id="${id}"]`)].find(visible);
    if (element) return mark(element);
  }
  if (!include) return null;
  const want = new RegExp(include, 'i');
  const avoid = exclude ? new RegExp(exclude, 'i') : null;
  let pool = [...document.querySelectorAll(CONTROLS)].filter(visible);
  if (scoped) {
    // The account form's own button sits with its inputs, and is a button.
    const scopes = [...document.querySelectorAll(inputs)].filter(visible).map(input => {
      let scope = input.closest('form') || input.parentElement;
      for (let depth = 0; scope && depth < 6 && !scope.querySelector(CONTROLS); depth += 1) {
        scope = scope.parentElement;
      }
      return scope;
    }).filter(Boolean);
    if (scopes.length) pool = pool.filter(element => scopes.some(scope => scope.contains(element)));
    pool = pool.filter(element => element.tagName !== 'A');
  }
  const found = pool.find(element => {
    const text = `${words(element)} ${element.getAttribute('aria-label') || ''}`;
    return want.test(text) && !(avoid && avoid.test(text));
  });
  return found ? mark(found) : null;
}
""".replace("MARK", MARK)

TERMS_SCRIPT = """
() => {
  const visible = element => Boolean(
    element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length)
  );
  return [...document.querySelectorAll('input[type="checkbox"]')]
    .filter(box => !box.checked && !box.disabled)
    .map((box, index) => {
      const home = box.getRootNode();
      const label = (box.id ? home.querySelector(`label[for="${CSS.escape(box.id)}"]`) : null)
        || box.closest('label');
      if (!visible(box) && !visible(label)) return null;
      const token = String(index);
      box.setAttribute('data-personal-os-terms', token);
      const text = String(
        (label && label.innerText) || box.getAttribute('aria-label')
        || (box.parentElement && box.parentElement.innerText) || ''
      ).trim().replace(/\\s+/g, ' ').slice(0, 300);
      return { token, label: text, name: box.name || box.id || '' };
    })
    .filter(Boolean);
}
"""


def _application_present(page: Page) -> bool:
    """Whether the application itself is on the page: its form, or its final button."""
    try:
        if looks_like_application_form(inspect_fields(page)):
            return True
    except Error:
        return True
    for frame in page.frames:
        try:
            buttons = frame.get_by_role("button", name=APPLICATION_SUBMIT)
            for index in range(buttons.count()):
                if buttons.nth(index).is_visible():
                    return True
        except Error:
            continue
    return False


def _click_marked(page: Page, args: dict[str, Any]) -> bool | None:
    """Mark the control the args describe, in whichever frame has it, and click it."""
    for frame in page.frames:
        try:
            marked = frame.evaluate(TARGET_SCRIPT, args)
        except Error:
            continue
        if not marked:
            continue
        try:
            frame.locator(f'[{MARK}="1"]').first.click(timeout=CONTROL_TIMEOUT_MS)
        except Error:
            return False
        return True
    return None


def click_account_control(page: Page, control: AccountControl) -> bool:
    """Click one account control. Never the application's own Next or Submit.

    "submit" is the account form's own button (sign in, create account, send,
    verify, reset), chosen from inside the form that holds the account inputs.
    """
    if _application_present(page):
        return False
    if control == "submit":
        seen = inspect_account(page)
        view = seen.view if seen is not None else None
        other = CREATE_WORDS if view == "sign_in" else SIGN_IN_WORDS if view == "create" else None
        passes: list[dict[str, Any]] = [
            {
                "ids": list(WORKDAY_SUBMIT.get(view, ())) if view else [],
                "include": SUBMIT_WORDS[view].pattern if view else None,
                "exclude": NOT_SUBMIT.pattern,
            },
            {
                "ids": [],
                "include": GENERIC_SUBMIT.pattern,
                "exclude": "|".join(
                    pattern for pattern in (NOT_SUBMIT.pattern, other and other.pattern) if pattern
                ),
            },
        ]
        scoped = True
    else:
        include, exclude = CONTROL_WORDS[control]
        passes = [
            {
                "ids": list(WORKDAY_CONTROLS[control]),
                "include": include.pattern,
                "exclude": exclude.pattern if exclude else None,
            }
        ]
        scoped = False
    for args in passes:
        clicked = _click_marked(page, {**args, "scoped": scoped, "inputs": ACCOUNT_INPUTS})
        if clicked is not None:
            return clicked
    return False


def _first_visible(frame: Any, selector: str) -> Any | None:
    boxes = frame.locator(selector)
    for index in range(boxes.count()):
        box = boxes.nth(index)
        kind = str(box.get_attribute("type") or "text").casefold()
        if kind not in {"hidden", "checkbox", "radio", "password"} and box.is_visible():
            return box
    return None


def fill_credentials(page: Page, *, email: str, password: str) -> None:
    """Fill the account email and every visible password box, confirm included."""
    for frame in page.frames:
        filled = False
        try:
            box = _first_visible(frame, EMAIL_INPUTS)
            if box is not None:
                box.fill(email, timeout=CONTROL_TIMEOUT_MS)
                filled = True
            passwords = frame.locator('input[type="password"]')
            for index in range(passwords.count()):
                if passwords.nth(index).is_visible():
                    passwords.nth(index).fill(password, timeout=CONTROL_TIMEOUT_MS)
                    filled = True
        except Error:
            continue
        if filled:
            return


def fill_code(page: Page, code: str) -> bool:
    """Type a mailed code: into its one box, or a digit per box."""
    for frame in page.frames:
        try:
            box = _first_visible(frame, CODE_INPUTS)
            if box is not None and box.get_attribute("maxlength") != "1":
                box.fill(code, timeout=CONTROL_TIMEOUT_MS)
                return True
            digits = frame.locator('input[maxlength="1"]')
            shown = [digits.nth(index) for index in range(digits.count())]
            shown = [digit for digit in shown if digit.is_visible()]
            if len(shown) >= len(code):
                for digit, character in zip(shown, code, strict=False):
                    digit.fill(character, timeout=CONTROL_TIMEOUT_MS)
                return True
        except Error:
            continue
    return False


def tick_account_terms(page: Page) -> int:
    """Tick the account's terms consent, and nothing that signs you up for mail."""
    ticked = 0
    for frame in page.frames:
        try:
            boxes: list[dict[str, str]] = frame.evaluate(TERMS_SCRIPT)
        except Error:
            continue
        for box in boxes:
            label = str(box.get("label") or "")
            field = {"label": label, "name": str(box.get("name") or "")}
            if not is_consent_field(field) or MARKETING_WORDS.search(label):
                continue
            try:
                frame.locator(f'[data-personal-os-terms="{box["token"]}"]').check(
                    force=True, timeout=CONTROL_TIMEOUT_MS
                )
            except Error:
                continue
            ticked += 1
    return ticked
