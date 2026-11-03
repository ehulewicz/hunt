"""The account wall: an ATS sign-in, create-account or verify page, perceived.

A page is an account page only when it is not already an application form
(`page_state.classify_application_page` checks the form first), so a real form
with an "Apply with Google" or "save your progress" password extra stays a
form. Workday's `data-automation-id`s are read where they exist; elsewhere the
wording in `account_inspect.py` decides, grown from captured `account_wall`
failures. This module operates the page that recognition finds.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.account_inspect import (
    ACCOUNT_SCRIPT,
    CHECK_EMAIL_WORDS,
    CODE_WORDS,
    CREATE_WORDS,
    ERROR_LIKE,
    ERROR_WORDS,
    EXISTS_WORDS,
    FORGOT_WORDS,
    GOOGLE_WORDS,
    NEW_PASSWORD_WORDS,
    NO_ACCOUNT_WORDS,
    NOT_AN_ACCOUNT,
    PASSWORD_RULE_WORDS,
    RESEND_WORDS,
    SIGN_IN_WORDS,
    VERIFY_FIRST_WORDS,
    WRONG_PASSWORD_WORDS,
    AccountError,
    AccountPage,
    AccountView,
    _account_scope,
    _error,
    _gather,
    _Seen,
    _view,
    inspect_account,
)
from personal_os_worker.jobs.autofill import is_consent_field
from personal_os_worker.jobs.browser import (
    inspect_fields,
    looks_like_application_form,
)
from personal_os_worker.jobs.registry.strategies import CONTROL_TIMEOUT_MS

__all__ = [
    "ACCEPT_WORDS",
    "ACCOUNT_INPUTS",
    "ACCOUNT_SCRIPT",
    "APPLICATION_SUBMIT",
    "CHECK_EMAIL_WORDS",
    "CODE_INPUTS",
    "CODE_WORDS",
    "CONTROL_WORDS",
    "CREATE_WORDS",
    "EMAIL_INPUTS",
    "ERROR_LIKE",
    "ERROR_WORDS",
    "EXISTS_WORDS",
    "FORGOT_LINK",
    "FORGOT_WORDS",
    "GENERIC_SUBMIT",
    "GOOGLE_WORDS",
    "MARK",
    "MARKETING_WORDS",
    "NEW_PASSWORD_WORDS",
    "NOT_AN_ACCOUNT",
    "NOT_SUBMIT",
    "NO_ACCOUNT_WORDS",
    "OTHER_SIGN_IN",
    "PASSWORD_RULE_WORDS",
    "RESEND_WORDS",
    "SIGN_IN_WORDS",
    "SUBMIT_WORDS",
    "TARGET_SCRIPT",
    "TERMS_SCRIPT",
    "VERIFY_FIRST_WORDS",
    "WORKDAY_CONTROLS",
    "WORKDAY_SUBMIT",
    "WORKDAY_TERMS",
    "WRONG_PASSWORD_WORDS",
    "AccountControl",
    "AccountError",
    "AccountPage",
    "AccountView",
    "_Seen",
    "_account_scope",
    "_error",
    "_gather",
    "_view",
    "click_account_control",
    "fill_code",
    "fill_credentials",
    "inspect_account",
    "tick_account_terms",
]

AccountControl = Literal["sign_in", "create", "forgot", "google", "resend", "submit"]

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
    "resend": (),
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
    "resend": (RESEND_WORDS, None),
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
# Workday's account terms box; on a live tenant it says only "I Accept".
WORKDAY_TERMS = "createAccountCheckbox"
ACCEPT_WORDS = re.compile(
    r"\bI accept\b|\baccept(?:ed)? (?:the )?(?:terms|conditions|policy)\b", re.IGNORECASE
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
    // No account inputs, no account form: nothing on the page is its button.
    pool = pool.filter(element => scopes.some(scope => scope.contains(element)));
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
      return {
        token, label: text, name: box.name || box.id || '',
        automation: box.getAttribute('data-automation-id') || '',
      };
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
    seen = inspect_account(page)
    # Account controls are operated only on an account page (the rule's one exception).
    if seen is None or _application_present(page):
        return False
    if control == "submit":
        view: AccountView | None = seen.view
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
            terms = (
                box.get("automation") == WORKDAY_TERMS
                or is_consent_field(field)
                or ACCEPT_WORDS.search(label)
            )
            if not terms or MARKETING_WORDS.search(label):
                continue
            try:
                frame.locator(f'[data-personal-os-terms="{box["token"]}"]').check(
                    force=True, timeout=CONTROL_TIMEOUT_MS
                )
            except Error:
                continue
            ticked += 1
    return ticked
