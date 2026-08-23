from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright

from personal_os_worker.adapters import failure_signature, option_match
from personal_os_worker.autofill import AutofillPlan, build_autofill_plan, is_sensitive_field


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

    def active_page(self) -> Page:
        """The tab currently in the foreground of the automation browser.

        Playwright exposes no notion of an active tab, but only the foreground
        tab of a focused window reports a visible document, so ask the pages
        themselves. Falls back to the most recently opened tab when the window is
        unfocused and every tab reports hidden.
        """
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        pages = [
            page
            for page in self._browser.contexts[0].pages
            if not page.url.startswith(("chrome://", "devtools://", "about:"))
        ]
        if not pages:
            raise RuntimeError("No usable tab is open in the automation browser")
        for page in reversed(pages):
            try:
                if page.evaluate("document.visibilityState") == "visible":
                    return page
            except Error:
                continue
        return pages[-1]

    def open_page(self, url: str, *, console_errors: list[str] | None = None) -> Page:
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        page = self._browser.contexts[0].new_page()
        if console_errors is not None:
            page.on(
                "console",
                lambda message: console_errors.append(message.text[:2000])
                if message.type == "error"
                else None,
            )
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        return page

    def autofill_application(
        self,
        url: str,
        *,
        profile: dict[str, Any],
        approved_answers: list[dict[str, Any]],
        resume_path: Path,
        assert_active: Callable[[], None],
    ) -> dict[str, Any]:
        page = self.open_page(url)
        fields, plan = plan_autofill(page, profile=profile, approved_answers=approved_answers)
        return apply_autofill(
            page,
            fields,
            plan,
            resume_path=resume_path,
            assert_active=assert_active,
        )


def reveal_application_form(page: Page, *, timeout_ms: int = 15_000) -> bool:
    """Click through to the form when a posting hides it behind an Apply step.

    Ashby and several other boards render the posting and the application as
    separate views, so the job page has no controls at all no matter how long it
    is given. Waiting longer never helps; the form has to be asked for.
    Returns whether controls are present once this has run.
    """
    if page.locator("input, textarea, select").count() > 0:
        return True
    for role in ("link", "button"):
        target = page.get_by_role(role, name=re.compile(r"^\s*apply", re.I))
        if target.count() == 0:
            continue
        try:
            target.first.click(timeout=5_000)
            page.wait_for_selector("input, textarea, select", timeout=timeout_ms)
        except Error:
            continue
        if page.locator("input, textarea, select").count() > 0:
            return True
    return page.locator("input, textarea, select").count() > 0


def plan_autofill(
    page: Page,
    *,
    profile: dict[str, Any],
    approved_answers: list[dict[str, Any]],
) -> tuple[list[dict[str, str | bool | None]], AutofillPlan]:
    """Work out what would be filled, without touching the page."""
    reveal_application_form(page)
    fields = inspect_fields(page)
    plan = build_autofill_plan(fields, profile=profile, approved_answers=approved_answers)
    return fields, plan


def apply_autofill(
    page: Page,
    fields: list[dict[str, Any]],
    plan: AutofillPlan,
    *,
    resume_path: Path | None,
    assert_active: Callable[[], None],
) -> dict[str, Any]:
    filled_labels: list[str] = []
    skipped: list[str] = []
    unanswered = list(plan.unanswered_required_questions)
    failures: list[dict[str, Any]] = []
    ordered_fills = sorted(
        plan.fills,
        key=lambda fill: (
            0
            if fill.kind == "resume"
            and str(fields[fill.index].get("ats")) in {"greenhouse", "ashby"}
            else 1
        ),
    )
    for fill in ordered_fills:
        assert_active()
        field = fields[fill.index]
        label = str(field.get("label") or field.get("name") or fill.index)
        control = fresh_control(page, field)
        if fill.kind == "resume" and resume_path is None:
            skipped.append(f"{label} (no resume provided)")
            continue
        try:
            if fill.kind == "resume" and resume_path is not None:
                control.set_input_files(str(resume_path))
                page.wait_for_timeout(750)
            elif field.get("tag") == "select" and fill.value is not None:
                options = [str(value) for value in field.get("options", [])]
                matched = option_match(options, fill.value)
                if matched is None:
                    raise RuntimeError("no unambiguous select option")
                control.select_option(label=matched)
            elif field.get("role") == "combobox" and fill.value is not None:
                control.click()
                set_registered_value(control, fill.value)
                option = page.get_by_role(
                    "option", name=re.compile(rf"^\s*{re.escape(fill.value)}\s*$", re.I)
                )
                if option.count() != 1:
                    raise RuntimeError("no unambiguous combobox option")
                option.click()
            elif field.get("type") in {"radio", "checkbox"} and fill.value is not None:
                if (
                    field.get("ats") == "lever"
                    and page.locator(
                        'iframe[src*="captcha" i], [data-sitekey], [class*="captcha" i]'
                    ).count()
                ):
                    raise RuntimeError("Lever choice left manual because captcha is present")
                control.check()
            elif fill.value is not None:
                set_registered_value(control, fill.value)
        except (Error, RuntimeError) as error:
            message = str(error)
            category = (
                "ambiguous_mapping"
                if "unambiguous" in message
                else "captcha_guard"
                if "captcha" in message.casefold()
                else "stale_locator"
                if "no longer present" in message or "strict mode" in message
                else "fill_exception"
            )
            if field.get("tag") == "select" and fill.value is not None:
                try:
                    control.select_option(value=fill.value)
                except Error:
                    if field.get("required") is True:
                        unanswered.append(label)
                    failures.append(
                        _failure(
                            field, category, message, intended=fill.value
                        )
                    )
                    continue
            else:
                if field.get("required") is True:
                    unanswered.append(label)
                failures.append(
                    _failure(field, category, message, intended=fill.value)
                )
                continue
        if verify_control(page, field, fill.value, resume=fill.kind == "resume"):
            filled_labels.append(label)
        else:
            try:
                observed = fresh_control(page, field).input_value()
            except Error:
                observed = None
            failures.append(
                _failure(
                    field,
                    "readback_mismatch",
                    "value did not register",
                    intended=fill.value,
                    observed=observed,
                )
            )
            if field.get("required") is True:
                unanswered.append(label)
    # The page remains open in the dedicated visible profile for manual review.
    return {
        "captured_url": page.url,
        "filled_fields": filled_labels,
        "skipped_fields": skipped,
        "unanswered_required_questions": list(dict.fromkeys(unanswered)),
        "failures": failures,
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
    return frame.locator("input, textarea, select, [role=combobox]").nth(
        int(field.get("control_index", field["index"]))
    )


def set_registered_value(control: Any, value: str) -> None:
    control.evaluate(
        """
        (element, value) => {
          const proto = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
          if (setter) setter.call(element, value); else element.value = value;
          element.dispatchEvent(new Event('input', {bubbles: true}));
          element.dispatchEvent(new Event('change', {bubbles: true}));
          element.dispatchEvent(new Event('blur', {bubbles: true}));
        }
        """,
        value,
    )


def verify_control(
    page: Page, field: dict[str, Any], value: str | None, *, resume: bool = False
) -> bool:
    control = fresh_control(page, field)
    try:
        if resume:
            return int(control.evaluate("element => element.files?.length || 0")) > 0
        if field.get("type") in {"radio", "checkbox"}:
            return bool(control.is_checked())
        observed = str(control.input_value()).strip()
        if field.get("tag") == "select" and value is not None:
            return value.casefold() in observed.casefold() or bool(observed)
        return observed == str(value or "").strip()
    except Error:
        return False


def _failure(
    field: dict[str, Any],
    category: str,
    message: str,
    *,
    intended: str | None = None,
    observed: str | None = None,
) -> dict[str, Any]:
    fingerprint = str(field.get("fingerprint") or field.get("selector") or field.get("index"))
    return {
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
        elements => elements.filter(element => {
          const type = (element.getAttribute('type') || '').toLowerCase();
          return !['hidden', 'password', 'submit', 'button', 'reset'].includes(type);
        }).map((element, index) => {
          const id = element.id;
          const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          const wrapping = element.closest('label');
          const fieldset = element.closest('fieldset');
          const legend = fieldset?.querySelector(':scope > legend');
          const group = element.closest('[role="group"], [role="radiogroup"]');
          const nearby = element.closest('[class*="field"], [class*="question"], [data-testid]');
          const label = (
              explicit?.textContent
              || wrapping?.textContent
              || element.getAttribute('aria-label')
              || element.getAttribute('placeholder')
              || nearby?.querySelector('label')?.textContent
              || ''
            ).trim();
          const selector = id
            ? `#${CSS.escape(id)}`
            : element.getAttribute('name')
              ? `${element.tagName.toLowerCase()}[name="${
                  CSS.escape(element.getAttribute('name'))
                }"]`
              : '';
          return {
            index,
            tag: element.tagName.toLowerCase(),
            type: element.getAttribute('type'),
            name: element.getAttribute('name'),
            label,
            group: (
              legend?.textContent
              || group?.getAttribute('aria-label')
              || ''
            ).trim(),
            required: element.required === true,
            selector,
            role: element.getAttribute('role'),
            placeholder: element.getAttribute('placeholder'),
            autocomplete: element.getAttribute('autocomplete'),
            options: element.tagName === 'SELECT'
              ? [...element.options].map(option => option.textContent.trim()).filter(Boolean)
              : [],
            value: ['radio', 'checkbox'].includes(element.type)
              ? (element.getAttribute('value') || '') : null,
            fingerprint: [element.tagName, element.getAttribute('type'),
              element.getAttribute('name'), label, element.getAttribute('role')
            ].join('|').toLowerCase(),
            filled: element.type === 'file'
              ? (element.files?.length || 0) > 0
              : ['checkbox', 'radio'].includes(element.type)
                ? element.checked === true
                : String(element.value || '').trim().length > 0,
          };
        })
        """
    for frame_index, frame in enumerate(page.frames):
        try:
            raw = frame.locator("input, textarea, select, [role=combobox]").evaluate_all(script)
        except Error:
            continue
        for item in cast(list[dict[str, Any]], raw):
            item["control_index"] = item["index"]
            item["index"] = len(results)
            item["frame_index"] = frame_index
            item["frame_url"] = frame.url
            results.append(item)
    return results


def sanitized_evidence(
    page: Page,
    fields: list[dict[str, Any]],
    *,
    console_errors: list[str] | None = None,
) -> dict[str, bytes]:
    """Capture visible form evidence without field values or browser/session data."""
    safe_fields = [
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
                "placeholder",
                "autocomplete",
                "options",
                "fingerprint",
            )
        }
        for field in fields
        if str(field.get("type") or "").casefold() not in {"password", "hidden"}
    ]
    html_script = """
        body => {
          const clone = body.cloneNode(true);
          clone.querySelectorAll('input, textarea').forEach(element => {
            element.removeAttribute('value'); element.textContent = '';
          });
          clone.querySelectorAll('input[type="hidden"], input[type="password"]').forEach(
            element => element.remove()
          );
          clone.querySelectorAll(
            '[data-sitekey], iframe[src*="captcha" i]'
          ).forEach(e => e.remove());
          return clone.outerHTML;
        }
        """
    evidence = {
        "controls.json": json.dumps(safe_fields, indent=2, default=str).encode(),
        "page.json": json.dumps({"url": page.url, "title": page.title()}, indent=2).encode(),
        "console-errors.json": json.dumps(console_errors or [], indent=2).encode(),
        "screen.png": page.screenshot(full_page=True),
    }
    for frame_index, frame in enumerate(page.frames):
        try:
            body = frame.locator("body")
            if body.count():
                evidence[f"frame-{frame_index}.html"] = str(body.evaluate(html_script)).encode()
        except Error:
            continue
    return evidence


def audit_filled_form(
    fields: list[dict[str, str | bool | None]],
) -> dict[str, list[str]]:
    """Summarize a filled form without copying values into logs."""
    filled: list[str] = []
    required_unfilled: list[str] = []
    sensitive_filled: list[str] = []
    for field in fields:
        field_type = str(field.get("type") or "").casefold()
        if field_type in {"submit", "button", "reset", "hidden"}:
            continue
        label = str(field.get("label") or field.get("name") or field.get("index") or "field")
        if field.get("filled") is True:
            filled.append(label)
            if is_sensitive_field(field):
                sensitive_filled.append(label)
        elif field.get("required") is True:
            required_unfilled.append(label)
    return {
        "filled_fields": filled,
        "required_unfilled": required_unfilled,
        "sensitive_filled": sensitive_filled,
    }
