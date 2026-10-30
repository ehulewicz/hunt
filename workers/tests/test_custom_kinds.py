"""A custom kind: one module teaches the worker a widget no built-in kind captures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_autofill_session import browser_page as browser_page

from personal_os_worker.jobs.autofill import build_autofill_plan
from personal_os_worker.jobs.browser import (
    OPERATIONS,
    Attempt,
    apply_autofill,
    inspect_fields,
    inspect_unknown_controls,
    verify_control,
)
from personal_os_worker.jobs.registry.kinds import classify, custom
from personal_os_worker.jobs.registry.kinds.custom import CustomKind, register
from personal_os_worker.jobs.registry.strategies import APPLIES, CANDIDATES
from personal_os_worker.jobs.resolve import Knowledge, Resolver

SLIDER_PAGE = """
<div class="field">
  <label id="years-label">Years of programming *</label>
  <div id="years" role="slider" tabindex="0" aria-labelledby="years-label"
       aria-valuemin="0" aria-valuemax="10" aria-valuenow="0" aria-required="true"
       style="width:200px;height:12px;background:#ccc"></div>
</div>
<script>
const slider = document.querySelector('#years');
slider.addEventListener('keydown', event => {
  const now = Number(slider.getAttribute('aria-valuenow'));
  const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
  slider.setAttribute('aria-valuenow', String(Math.min(10, Math.max(0, now + step))));
});
</script>
"""


def press_until(attempt: Attempt) -> str | None:
    """Arrow keys until the slider holds the value."""
    wanted = int(str(attempt.fill.value))
    for _ in range(25):
        now = int(attempt.control.get_attribute("aria-valuenow") or 0)
        if now == wanted:
            return str(wanted)
        attempt.control.press("ArrowRight" if now < wanted else "ArrowLeft")
    raise RuntimeError("the slider would not reach the value")


@pytest.fixture
def slider_kind(monkeypatch: pytest.MonkeyPatch) -> Iterator[CustomKind]:
    monkeypatch.setattr(custom, "KINDS", {})
    monkeypatch.setattr(custom, "_loaded", True)
    # Registering adds to the shared tables; they are put back as they were.
    saved = (dict(OPERATIONS), dict(CANDIDATES), dict(APPLIES))
    kind = register(CustomKind(
        name="slider",
        selector='[role="slider"]',
        read="element => ({value: element.getAttribute('aria-valuenow') || '', "
             "filled: element.getAttribute('aria-valuenow') !== "
             "element.getAttribute('aria-valuemin')})",
        strategies={"slider_keys": press_until},
    ))
    yield kind
    for table, before in zip((OPERATIONS, CANDIDATES, APPLIES), saved, strict=True):
        table.clear()
        table.update(before)  # type: ignore[attr-defined]


def test_a_registered_kind_is_captured_planned_operated_and_read_back(
    browser_page: Any, slider_kind: CustomKind
) -> None:
    browser_page.set_content(SLIDER_PAGE)
    fields = inspect_fields(browser_page)
    [slider] = [field for field in fields if field.get("custom") == "slider"]
    assert (slider["label"], slider["required"], slider["filled"]) == (
        "Years of programming", True, False)
    assert classify(slider) == "slider"
    assert inspect_unknown_controls(browser_page) == []

    knowledge = Knowledge(
        facts={"experience.programming_years": 4},
        types={"experience.programming_years": "number"},
        patterns=({"id": "p", "kind": "binding", "status": "active",
                   "question_key": "years of programming",
                   "fact_key": "experience.programming_years",
                   "resolver_id": "binding:p"},),
    )
    plan = build_autofill_plan(fields, resolver=Resolver(knowledge))
    result = apply_autofill(browser_page, fields, plan, resume_path=Path("missing.pdf"),
                            assert_active=lambda: None)
    assert result["failures"] == []
    assert browser_page.locator("#years").get_attribute("aria-valuenow") == "4"
    assert verify_control(browser_page, slider, "4")
    assert not verify_control(browser_page, slider, "5")


def test_without_its_kind_the_same_widget_is_an_unknown_control(browser_page: Any) -> None:
    browser_page.set_content(SLIDER_PAGE)
    assert not [field for field in inspect_fields(browser_page) if field.get("custom")]
    [unknown] = inspect_unknown_controls(browser_page)
    assert unknown["role"] == "slider"
