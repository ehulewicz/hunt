from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright

from personal_os_worker.adapters import failure_signature, option_match
from personal_os_worker.autofill import (
    AutofillPlan,
    build_autofill_plan,
    choose_option,
    is_sensitive_field,
    normalize_question,
)

YESNO_CONTAINER_XPATH = (
    "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), "
    "' ashby-application-form-input-yesno ')][1]"
)


class BrowserClosedError(RuntimeError):
    """The connected browser or its last usable application tab was closed."""


def is_browser_closed_error(page: Page, error: Error) -> bool:
    """Whether a Playwright operation failed because its page or browser closed."""
    try:
        if page.is_closed():
            return True
    except Error:
        return True
    message = str(error).casefold()
    return (
        type(error).__name__ == "TargetClosedError"
        or "target page, context or browser has been closed" in message
    )


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

    def active_page(self, current_page: Page | None = None) -> Page:
        """The tab currently in the foreground of the automation browser.

        Playwright exposes no notion of an active tab, but only the foreground
        tab of a focused window reports a visible document, so ask the pages
        themselves. Falls back to the most recently opened tab when the window is
        unfocused and every tab reports hidden. A closed current page ends its
        owning session even when an unrelated browser tab remains open.
        """
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        try:
            if current_page is not None and current_page.is_closed():
                raise BrowserClosedError("The application tab was closed")
            contexts = self._browser.contexts
            if not contexts:
                raise BrowserClosedError("The automation browser was closed")
            pages = [
                page
                for page in contexts[0].pages
                if not page.url.startswith(("chrome://", "devtools://", "about:"))
            ]
        except Error as error:
            raise BrowserClosedError("The automation browser was closed") from error
        if not pages:
            raise BrowserClosedError("The application tab was closed")
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


def click_safe_transition(page: Page, *, kind: str) -> str | None:
    """Click one unambiguous non-submission navigation control."""
    patterns = {
        "apply": re.compile(
            r"^\s*(?:apply(?: for this job| now)?|start application)\s*$", re.I
        ),
        "next": re.compile(r"^\s*(?:next|continue|save and continue)\s*$", re.I),
    }
    pattern = patterns.get(kind)
    if pattern is None:
        raise ValueError(f"Unknown transition kind: {kind}")
    if kind == "next" and page.locator(
        'iframe[src*="captcha" i], [data-sitekey], [class*="captcha" i]'
    ).count():
        return None

    for role in ("link", "button"):
        candidates: list[Any] = []
        locator = page.get_by_role(role, name=pattern)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    candidates.append(candidate)
            except Error:
                continue
        if len(candidates) > 1:
            return None
        if len(candidates) == 1:
            candidate = candidates[0]
            label = str(candidate.get_attribute("aria-label") or candidate.inner_text()).strip()
            candidate.click(timeout=5_000)
            return label or kind.title()
    return None


def reveal_application_form(page: Page, *, timeout_ms: int = 15_000) -> bool:
    """Click through to the form when a posting hides it behind an Apply step.

    Ashby and several other boards render the posting and the application as
    separate views, so the job page has no controls at all no matter how long it
    is given. Waiting longer never helps; the form has to be asked for.
    Returns whether controls are present once this has run.
    """
    if looks_like_application_form(inspect_fields(page)):
        return True
    try:
        clicked = click_safe_transition(page, kind="apply")
        if clicked is not None:
            page.wait_for_selector("input, textarea, select", timeout=timeout_ms)
    except Error:
        return False
    return looks_like_application_form(inspect_fields(page))


def plan_autofill(
    page: Page,
    *,
    profile: dict[str, Any],
    approved_answers: list[dict[str, Any]],
    fallback: Callable[[str], str | None] | None = None,
) -> tuple[list[dict[str, str | bool | None]], AutofillPlan]:
    """Work out what would be filled, without touching the page."""
    reveal_application_form(page)
    fields = inspect_fields(page)
    plan = build_autofill_plan(
        fields, profile=profile, approved_answers=approved_answers, fallback=fallback
    )
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
        expected = fill.value
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
                expected = select_combobox_option(page, control, fill.value)
            elif field.get("type") == "yesno" and fill.value is not None:
                yesno_option = "yes" if fill.value.strip().casefold() == "yes" else "no"
                control.locator(YESNO_CONTAINER_XPATH).locator(
                    f'button[data-option="{yesno_option}"]'
                ).click()
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
        if verify_control(page, field, expected, resume=fill.kind == "resume"):
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


COMBOBOX_SETTLE_MS = 300
COMBOBOX_OPTIONS_TIMEOUT_MS = 4_000


def select_combobox_option(page: Page, control: Any, value: str) -> str:
    """Type into a typeahead, pick the one option the answer denotes, return its text.

    Keystrokes rather than a synthetic value: react-select and Google Places
    both key their menus off real input events and close on blur. Options are
    fetched asynchronously, so wait for the first one before matching.
    """
    control.click()
    control.fill("")
    control.press_sequentially(value, delay=20)
    # Scope to the control's own document so a form inside an iframe works too.
    options = control.locator("xpath=ancestor::body").get_by_role("option")
    try:
        options.first.wait_for(state="visible", timeout=COMBOBOX_OPTIONS_TIMEOUT_MS)
    except Error as error:
        raise RuntimeError("no unambiguous combobox option (none appeared)") from error
    page.wait_for_timeout(COMBOBOX_SETTLE_MS)
    # Pages keep other listboxes in the DOM (Greenhouse's phone-code list has
    # every country as a hidden option); only what the menu shows is a choice.
    visible = [index for index in range(options.count()) if options.nth(index).is_visible()]
    texts = [str(options.nth(index).inner_text()).strip() for index in visible]
    distinct = list(dict.fromkeys(text for text in texts if text))
    chosen = choose_option(distinct, value)
    if chosen is None:
        target = normalize_question(value)
        containing = [text for text in distinct if target and target in normalize_question(text)]
        chosen = containing[0] if len(containing) == 1 else None
    if chosen is None:
        raise RuntimeError("no unambiguous combobox option")
    options.nth(visible[texts.index(chosen)]).click()
    return chosen


def verify_control(
    page: Page, field: dict[str, Any], value: str | None, *, resume: bool = False
) -> bool:
    control = fresh_control(page, field)
    try:
        if resume:
            return int(control.evaluate("element => element.files?.length || 0")) > 0
        if field.get("type") == "yesno":
            yesno_option = "yes" if str(value or "").strip().casefold() == "yes" else "no"
            button = control.locator(YESNO_CONTAINER_XPATH).locator(
                f'button[data-option="{yesno_option}"]'
            )
            return bool(button.get_attribute("aria-pressed") == "true")
        if field.get("type") in {"radio", "checkbox"}:
            return bool(control.is_checked())
        observed = str(control.input_value()).strip()
        if field.get("tag") == "select" and value is not None:
            return value.casefold() in observed.casefold() or bool(observed)
        if field.get("role") == "combobox":
            return combobox_shows(control, str(value or ""))
        return observed == str(value or "").strip()
    except Error:
        return False


COMBOBOX_VALUE_SCRIPT = """
    element => {
      const own = String(element.value || '').trim();
      if (own) return own;
      let shell = element.parentElement;
      for (let depth = 0; shell && depth < 4; depth += 1) {
        const chosen = shell.querySelector('[class*="single-value"], [class*="multi-value"]');
        if (chosen && chosen.textContent.trim()) return chosen.textContent.trim();
        const mirror = shell.querySelector('input[aria-hidden="true"]');
        if (mirror && String(mirror.value || '').trim()) return String(mirror.value).trim();
        shell = shell.parentElement;
      }
      return '';
    }
"""


def combobox_value(control: Any) -> str:
    """What a typeahead currently holds, wherever the widget keeps it.

    A plain typeahead leaves the chosen text in the input. react-select
    empties the search input and renders the choice in a sibling
    `single-value`/`multi-value` element, or mirrors it into an aria-hidden
    input. Whichever is present is the value.
    """
    return str(control.evaluate(COMBOBOX_VALUE_SCRIPT)).strip()


def combobox_shows(control: Any, expected: str) -> bool:
    """Whether a typeahead holds the picked option.

    The option was clicked by its text, so a rendered choice is the evidence;
    its display text may differ from the menu text (Greenhouse's phone-code
    picker lists "United States +1" but shows a flag and "+1").
    """
    held = combobox_value(control)
    return bool(held) and (
        held.casefold() == expected.strip().casefold()
        or control.evaluate("element => !String(element.value || '').trim()")
    )


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
          // React-select mirrors the chosen value into an aria-hidden input that
          // carries the required flag; the visible combobox is the real control.
          if (element.getAttribute('aria-hidden') === 'true') return false;
          return !['hidden', 'password', 'submit', 'button', 'reset'].includes(type);
        }).map((element, index) => {
          const id = element.id;
          const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          const wrapping = element.closest('label');
          const fieldset = element.closest('fieldset');
          // Ashby writes the group question as a heading label, not a legend.
          const legend = fieldset?.querySelector(':scope > legend, :scope > label');
          const groupRequired = Boolean(
            legend && (/required/i.test(legend.className) || /\\*\\s*$/.test(legend.textContent))
          );
          const group = element.closest('[role="group"], [role="radiogroup"]');
          const nearby = element.closest('[class*="field"], [class*="question"], [data-testid]');
          const labelledBy = element.getAttribute('aria-labelledby');
          const referenced = labelledBy
            ? labelledBy.split(/\\s+/).map(id => document.getElementById(id)?.textContent || '')
                .join(' ')
            : '';
          // Ashby renders a boolean question as two buttons plus a hidden checkbox
          // that mirrors their state; the question lives on a sibling label, not
          // on the checkbox itself.
          const yesnoContainer = element.closest('.ashby-application-form-input-yesno');
          const yesnoLabel = yesnoContainer?.parentElement?.querySelector(
            'label.ashby-application-form-question-title'
          ) || null;
          const yesnoButtons = yesnoContainer
            ? [...yesnoContainer.querySelectorAll('button[data-option]')] : [];
          const yesnoPressed = yesnoButtons.find(
            button => button.getAttribute('aria-pressed') === 'true'
          ) || null;
          const finalType = yesnoContainer ? 'yesno' : element.getAttribute('type');
          // A placeholder such as "Start typing..." is the last resort; Ashby
          // comboboxes have no id, so their question lives on the field's label.
          const rawLabel = (
              yesnoLabel?.textContent
              || explicit?.textContent
              || wrapping?.textContent
              || element.getAttribute('aria-label')
              || referenced
              || nearby?.querySelector('label')?.textContent
              || element.getAttribute('placeholder')
              || ''
            ).trim();
          // A trailing asterisk marks a required field on Greenhouse boards.
          const label = rawLabel.replace(/\\s*\\*\\s*$/, '');
          let shell = element.parentElement;
          let mirroredRequired = false;
          let mirroredValue = '';
          for (let depth = 0; shell && depth < 4; depth += 1) {
            const mirror = shell.querySelector('input[aria-hidden="true"]');
            if (mirror) {
              mirroredRequired = mirroredRequired || mirror.required === true;
              mirroredValue = mirroredValue || String(mirror.value || '').trim();
              if (mirroredRequired) break;
            }
            shell = shell.parentElement;
          }
          const isCombobox = element.getAttribute('role') === 'combobox';
          // react-select empties its search input after a pick and renders the
          // choice in a sibling; that sibling is the value of the control.
          let chosenText = '';
          if (isCombobox) {
            let holder = element.parentElement;
            for (let depth = 0; holder && depth < 4; depth += 1) {
              const chosen = holder.querySelector(
                '[class*="single-value"], [class*="multi-value"]'
              );
              if (chosen) { chosenText = chosen.textContent.trim(); break; }
              holder = holder.parentElement;
            }
          }
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
            type: finalType,
            name: element.getAttribute('name'),
            label,
            group: (
              legend?.textContent
              || group?.getAttribute('aria-label')
              || ''
            ).trim().replace(/\\s*\\*\\s*$/, ''),
            required: element.required === true
              || element.getAttribute('aria-required') === 'true'
              || /\\*\\s*$/.test(rawLabel)
              || (['radio', 'checkbox'].includes(element.type) && groupRequired)
              || (element.getAttribute('role') === 'combobox' && mirroredRequired)
              || Boolean(yesnoContainer && /_required_/.test(yesnoLabel?.className || '')),
            selector,
            role: element.getAttribute('role'),
            placeholder: element.getAttribute('placeholder'),
            autocomplete: element.getAttribute('autocomplete'),
            options: yesnoContainer
              ? ['Yes', 'No']
              : element.tagName === 'SELECT'
                ? [...element.options].map(option => option.textContent.trim()).filter(Boolean)
                : [],
            value: yesnoContainer
              ? (yesnoPressed ? yesnoPressed.textContent.trim() : null)
              : ['radio', 'checkbox'].includes(element.type)
                ? (element.getAttribute('value') || '') : null,
            fingerprint: [element.tagName, finalType,
              element.getAttribute('name'), label, element.getAttribute('role')
            ].join('|').toLowerCase(),
            filled: yesnoContainer
              ? Boolean(yesnoPressed)
              : element.type === 'file'
                ? (element.files?.length || 0) > 0
                : ['checkbox', 'radio'].includes(element.type)
                  ? element.checked === true
                  : String(element.value || '').trim().length > 0
                    || (isCombobox && (mirroredValue.length > 0 || chosenText.length > 0)),
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


def snapshot_values(page: Page, fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Read every non-empty answer on the page keyed by its question.

    Choice groups report the checked option's label under the group question,
    selects report the selected option's text, and files are skipped. This is
    what gets saved to the answer library, so later forms can reuse it.
    """
    snapshot: dict[str, dict[str, Any]] = {}
    for field in fields:
        field_type = str(field.get("type") or "").casefold()
        tag = str(field.get("tag") or "").casefold()
        if field_type in {"file", "submit", "button", "reset", "hidden"} or tag == "button":
            continue
        label = str(field.get("label") or "").strip()
        group = str(field.get("group") or "").strip()
        try:
            control = fresh_control(page, field)
            if field_type == "yesno":
                pressed = control.locator(YESNO_CONTAINER_XPATH).locator(
                    'button[aria-pressed="true"]'
                )
                if pressed.count() != 1:
                    continue
                question = label
                value = str(pressed.first.inner_text())
            elif field_type in {"radio", "checkbox"}:
                if not control.is_checked():
                    continue
                question = group or label
                value = label or str(field.get("value") or "")
            elif tag == "select":
                question = label
                value = str(
                    control.evaluate(
                        "element => element.selectedOptions[0]?.textContent?.trim() || ''"
                    )
                )
            elif field.get("role") == "combobox":
                question = label
                value = combobox_value(control)
            else:
                question = label
                value = str(control.input_value())
        except (Error, RuntimeError):
            continue
        question = question.strip()
        value = value.strip()
        if not question or not value:
            continue
        snapshot[question] = {
            "question": question,
            "answer_text": value,
            "control_kind": field_type or tag,
        }
    return snapshot


def audit_filled_form(
    fields: list[dict[str, str | bool | None]],
) -> dict[str, list[str]]:
    """Summarize a filled form without copying values into logs."""
    filled: list[str] = []
    required_unfilled: list[str] = []
    sensitive_filled: list[str] = []
    # A choice group is one question: it is filled once any option is checked,
    # and it is reported by the group question rather than each option's label.
    answered_groups: set[str] = set()
    pending_groups: list[str] = []
    for field in fields:
        field_type = str(field.get("type") or "").casefold()
        if field_type in {"submit", "button", "reset", "hidden"}:
            continue
        group = str(field.get("group") or "").strip()
        is_choice = field_type in {"radio", "checkbox"} and bool(group)
        label = group if is_choice else str(
            field.get("label") or field.get("name") or field.get("index") or "field"
        )
        if field.get("filled") is True:
            if is_choice:
                if label in answered_groups:
                    continue
                answered_groups.add(label)
            filled.append(label)
            if is_sensitive_field(field):
                sensitive_filled.append(label)
        elif field.get("required") is True:
            if is_choice:
                pending_groups.append(label)
            else:
                required_unfilled.append(label)
    required_unfilled.extend(
        dict.fromkeys(group for group in pending_groups if group not in answered_groups)
    )
    return {
        "filled_fields": filled,
        "required_unfilled": required_unfilled,
        "sensitive_filled": sensitive_filled,
    }
