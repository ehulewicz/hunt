from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright

from personal_os_worker.autofill import AutofillPlan, build_autofill_plan


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

    def open_page(self, url: str) -> Page:
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        page = self._browser.contexts[0].new_page()
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
        fields, plan = plan_autofill(
            page, profile=profile, approved_answers=approved_answers
        )
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
    fields: list[dict[str, str | bool | None]],
    plan: AutofillPlan,
    *,
    resume_path: Path | None,
    assert_active: Callable[[], None],
) -> dict[str, Any]:
    controls = page.locator("input, textarea, select")
    filled_labels: list[str] = []
    skipped: list[str] = []
    unanswered = list(plan.unanswered_required_questions)
    for fill in plan.fills:
        assert_active()
        field = fields[fill.index]
        label = str(field.get("label") or field.get("name") or fill.index)
        control = controls.nth(fill.index)
        if fill.kind == "resume" and resume_path is None:
            skipped.append(f"{label} (no resume provided)")
            continue
        try:
            if fill.kind == "resume" and resume_path is not None:
                control.set_input_files(str(resume_path))
            elif field.get("tag") == "select" and fill.value is not None:
                control.select_option(label=fill.value)
            elif fill.value is not None:
                control.fill(fill.value)
        except Error:
            if field.get("tag") == "select" and fill.value is not None:
                try:
                    control.select_option(value=fill.value)
                except Error:
                    if field.get("required") is True:
                        unanswered.append(label)
                    continue
            else:
                if field.get("required") is True:
                    unanswered.append(label)
                continue
        filled_labels.append(label)
    # The page remains open in the dedicated visible profile for manual review.
    return {
        "captured_url": page.url,
        "filled_fields": filled_labels,
        "skipped_fields": skipped,
        "unanswered_required_questions": list(dict.fromkeys(unanswered)),
        "submitted": False,
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


def inspect_fields(page: Page) -> list[dict[str, str | bool | None]]:
    result = page.locator("input, textarea, select").evaluate_all(
        """
        elements => elements.map((element, index) => {
          const id = element.id;
          const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          const wrapping = element.closest('label');
          const fieldset = element.closest('fieldset');
          const legend = fieldset?.querySelector(':scope > legend');
          const group = element.closest('[role="group"], [role="radiogroup"]');
          return {
            index,
            tag: element.tagName.toLowerCase(),
            type: element.getAttribute('type'),
            name: element.getAttribute('name'),
            label: (
              explicit?.textContent
              || wrapping?.textContent
              || element.getAttribute('aria-label')
              || ''
            ).trim(),
            group: (
              legend?.textContent
              || group?.getAttribute('aria-label')
              || ''
            ).trim(),
            required: element.required === true,
          };
        })
        """
    )
    return cast(list[dict[str, str | bool | None]], result)
