"""The two independent guards that keep a repair or validation page from submitting.

R23: a stray submit is the one irreversible mistake in the repair design, so it
is refused twice, by mechanisms that do not depend on each other. The page
script cancels every form submission and every click on a control labelled as
submitting; the network guard aborts any non-GET request that names a
submission. The repair worker also never clicks Next or Submit by construction.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Page, Route

SUBMITTING = re.compile(
    r"^\s*submit\b|\bsubmit\s+(?:my\s+|your\s+)?application\b|\bsend\s+application\b"
    r"|\bapply\s+now\b|\bfinish\s+application\b",
    re.IGNORECASE,
)
# Kept beside the Python pattern above; the same words, as JavaScript.
GUARD_SCRIPT = r"""
(() => {
  const submitting = new RegExp([
    '^\\s*submit\\b',
    '\\bsubmit\\s+(?:my\\s+|your\\s+)?application\\b',
    '\\bsend\\s+application\\b',
    '\\bapply\\s+now\\b',
    '\\bfinish\\s+application\\b',
  ].join('|'), 'i');
  const stop = event => { event.preventDefault(); event.stopImmediatePropagation(); };
  window.__personalOsSubmitGuard = true;
  document.addEventListener('submit', stop, true);
  document.addEventListener('click', event => {
    const target = event.target instanceof Element
      ? event.target.closest('button, input[type=submit], input[type=button], [role=button], a')
      : null;
    if (!target) return;
    const text = (target.innerText || target.value || target.getAttribute('aria-label') || '');
    if ((target.matches('input[type=submit]')) || submitting.test(text.trim())) stop(event);
  }, true);
  HTMLFormElement.prototype.submit = function () {};
  HTMLFormElement.prototype.requestSubmit = function () {};
})();
"""


def names_a_submission(method: str, url: str, body: str | None) -> bool:
    """Whether a request would send an application, judged by method, path, and body."""
    if method.upper() == "GET":
        return False
    text = f"{url} {body or ''}".casefold()
    return "submit" in text or ("/apply" in text and method.upper() == "POST")


def install_guards(page: Page) -> None:
    """Both guards, before the page loads anything."""
    page.add_init_script(GUARD_SCRIPT)

    def refuse(route: Route) -> None:
        request = route.request
        try:
            body = request.post_data
        except (UnicodeDecodeError, ValueError):
            body = None
        if names_a_submission(request.method, request.url, body):
            route.abort("blockedbyclient")
        else:
            route.continue_()

    page.route("**/*", refuse)


def guarded(page: Any) -> bool:
    """Whether the page script is in place (both run before any page script)."""
    try:
        return bool(page.evaluate("() => window.__personalOsSubmitGuard === true"))
    except Exception:
        return False
