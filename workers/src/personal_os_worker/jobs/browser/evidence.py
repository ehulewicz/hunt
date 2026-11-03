"""Page evidence: captured HTML/JSON with secrets stripped."""

from __future__ import annotations

import contextlib
import html
import json
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.core.secrets import loaded_values
from personal_os_worker.jobs.page_scripts import load


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
    html_script = load("page_evidence")
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
