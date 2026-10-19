"""The gate between a posting and its form (design §10.2).

Boards put choices between the Apply control and the application: Workday's
portal asks whether to autofill from a resume, apply manually, or reuse the
last application, and nothing on that page is an input. Field capture sees an
empty page, so the run hits a wall it can never learn its way past.

A gate is an ordinary choice control. Its question is the dialog's heading, its
options are the ways in, and its answer comes from a fact like every other
value — so the resolver, the Inbox, patterns and `--explain` need no special
case. Buttons are captured here rather than in `inspect_fields` because a
button is a control only when there is no form: on a form it is Next or Submit,
and those are never operated.
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.account_page import inspect_account
from personal_os_worker.jobs.browser import (
    CONTROL_TIMEOUT_MS,
    captcha_challenge_present,
    has_submit_control,
    inspect_fields,
    looks_like_application_form,
)
from personal_os_worker.jobs.registry.kinds import GATEWAY
from personal_os_worker.jobs.sites import option_match

# What a gate never offers: the page's own submission, your account, and the
# ways out of a dialog. Anything else is a candidate the fact has to name.
GATE_SCRIPT = """
() => {
  const EXCLUDE = new RegExp(
    '^\\\\s*(?:x|\\u00d7|close|cancel|dismiss|back|skip|next|previous)\\\\s*$' +
    '|\\\\b(?:sign ?in|log ?in|sign ?up|signin|login|register|create (?:an )?account' +
    '|forgot|search jobs?|read more|privacy|cookies?|terms)\\\\b',
    'i'
  );
  const SUBMIT = new RegExp(
    '^\\\\s*submit\\\\s*$|\\\\b(?:submit|send|complete|finish)\\\\b.*\\\\b(?:application|form)\\\\b',
    'i'
  );
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const dialogs = [...document.querySelectorAll('[role="dialog"], dialog')].filter(visible);
  // A dialog is modal by construction: while one is open it is the only way on.
  const scope = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
  const heading = (() => {
    if (scope === document.body) return '';
    const labelled = scope.getAttribute('aria-labelledby');
    const referenced = labelled ? document.getElementById(labelled) : null;
    return String(
      referenced?.textContent
      || scope.getAttribute('aria-label')
      || scope.querySelector('h1, h2, h3, h4')?.textContent
      || ''
    ).trim().replace(/\\s+/g, ' ');
  })();
  const options = [];
  const seen = new Set();
  for (const element of scope.querySelectorAll('button, [role="button"], a[href]')) {
    if (!visible(element) || element.disabled) continue;
    const type = String(element.getAttribute('type') || '').toLowerCase();
    if (type === 'submit' || type === 'reset') continue;
    const text = String(
      element.innerText || element.textContent || element.getAttribute('aria-label') || ''
    ).trim().replace(/\\s+/g, ' ');
    if (!text || text.length > 60) continue;
    // A way in is always worded. A glyph (\u00d7, an arrow, an icon font) is a
    // close or a decoration, whatever the page's encoding turns it into.
    if (!/[a-z0-9]/i.test(text)) continue;
    const named = [
      text,
      element.getAttribute('aria-label') || '',
      element.getAttribute('title') || '',
    ];
    if (named.some(name => name && (EXCLUDE.test(name) || SUBMIT.test(name)))) continue;
    if (seen.has(text.toLowerCase())) continue;
    seen.add(text.toLowerCase());
    const token = String(options.length);
    element.setAttribute('data-personal-os-gate', token);
    options.push({ text, token });
  }
  if (!options.length) return null;
  // Record what the user clicks, so a gate the worker could not answer is
  // still learned from watching them pass it (R58).
  if (!window.__personalOsGateWatch) {
    window.__personalOsGateWatch = true;
    document.addEventListener('click', event => {
      const control = event.target instanceof Element
        ? event.target.closest('[data-personal-os-gate]') : null;
      if (control) window.__personalOsGateClicked = control.innerText.trim().replace(/\\s+/g, ' ');
    }, true);
  }
  return { heading, options: options.slice(0, 8) };
}
"""

# The question a gate asks when its dialog gives no heading of its own.
DEFAULT_GATE_QUESTION = "Start your application"


def inspect_gateways(page: Page) -> list[dict[str, Any]]:
    """The ways into the application on a page that offers no form, as one choice.

    At most one: an open dialog's choices, else the page's own controls. A page
    that already has a form to fill has no gate — its buttons are Next and
    Submit, which this never touches.
    """
    try:
        if looks_like_application_form(inspect_fields(page)) or has_submit_control(page):
            return []
        captured = page.evaluate(GATE_SCRIPT)
    except Error:
        return []
    if not captured:
        return []
    options = [str(option["text"]) for option in captured["options"]]
    tokens = {str(option["text"]): str(option["token"]) for option in captured["options"]}
    return [
        {
            "tag": "button",
            "type": GATEWAY,
            "label": str(captured["heading"] or DEFAULT_GATE_QUESTION),
            "options": options,
            "tokens": tokens,
            "group_size": len(options),
            "required": False,
            "filled": False,
        }
    ]


def click_gateway(page: Page, gate: dict[str, Any], value: str | None) -> str | None:
    """Click the one way in that the value names; None when it names none of them."""
    if value is None or captcha_challenge_present(page):
        return None
    chosen = option_match([str(option) for option in gate.get("options") or []], str(value))
    if chosen is None:
        return None
    token = dict(gate.get("tokens") or {}).get(chosen)
    if token is None:
        return None
    try:
        page.locator(f'[data-personal-os-gate="{token}"]').first.click(
            timeout=CONTROL_TIMEOUT_MS
        )
    except Error:
        return None
    return chosen


def form_appeared(page: Page, *, timeout_ms: int = 15_000) -> bool:
    """Whether the application is on the page now: its form, or the account wall in
    front of it (Workday's Apply Manually opens Create Account first). The gate's readback."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            if looks_like_application_form(inspect_fields(page)) or has_submit_control(page):
                return True
            if inspect_account(page) is not None:
                return True
        except Error:
            pass
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(250)


def observed_choice(page: Page) -> str | None:
    """The way in you clicked yourself, so a gate is learned by watching too."""
    try:
        clicked = page.evaluate("() => window.__personalOsGateClicked || null")
    except Error:
        return None
    return str(clicked) if clicked else None
