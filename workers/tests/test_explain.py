"""`apply --explain` and `--step` on a real page: the whole decision, one control at a time."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.autofill import build_autofill_plan
from personal_os_worker.jobs.browser import apply_autofill, inspect_fields
from personal_os_worker.jobs.explain import Explainer
from personal_os_worker.jobs.resolve import Knowledge, Resolver

FORM = """
<form>
  <label for="first">First name</label><input id="first" required>
  <label for="grad">Graduation</label>
  <select id="grad"><option value=""></option><option>Spring 2027</option>
    <option>Fall 2027</option></select>
  <label for="email">Email</label><input id="email" type="email">
  <label for="why">Anything else?</label><input id="why">
  <button type="submit">Submit application</button>
</form>
"""


@pytest.fixture(scope="module")
def page():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        page = browser.new_page()
        yield page
        browser.close()


def resolver() -> Resolver:
    def binding(question: str, key: str) -> dict[str, Any]:
        return {"id": f"b-{key}", "kind": "binding", "question_key": question,
                "fact_key": key, "resolver_id": f"binding:{key}", "status": "active"}

    return Resolver(Knowledge.build(
        facts=[{"key": "identity.legal_name", "value": "Alex Example"},
               {"key": "contact.email", "value": "ethan@example.com"},
               {"key": "education.graduation_date", "value": "2027-12-15",
                "value_type": "date"}],
        career=None,
        patterns=[binding("first name", "identity.first_name"),
                  binding("email", "contact.email"),
                  {"id": "e1", "kind": "expression", "expression": r"\bgraduat",
                   "fact_key": "education.graduation_date", "resolver_id": "expression:e1",
                   "status": "active"}],
    ))


def run(page: Any, answers: list[str]) -> tuple[str, list[str]]:  # type: ignore[no-untyped-def]
    page.set_content(FORM)
    fields = inspect_fields(page)
    answering = resolver()
    plan = build_autofill_plan(fields, resolver=answering)
    asked: list[str] = []
    replies = iter(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return next(replies)

    explainer = Explainer(page, step=bool(answers), ask=ask)
    explainer.begin("test", fields, plan, answering)
    apply_autofill(page, fields, plan, resume_path=None, assert_active=lambda: None,
                   observer=explainer)
    explainer.finish()
    return page, asked


def test_every_part_of_the_decision_is_printed(page, capsys) -> None:  # type: ignore[no-untyped-def]
    run(page, [])
    out = " ".join(capsys.readouterr().out.split())
    # Control, question, planner, resolver, operation, for a binding.
    assert "#0 First name" in out and "text_entry" in out and "required" in out
    assert "'first name'" in out and "resolver: binding:identity.first_name" in out
    assert "b-identity.first_name → identity.first_name" in out and "decided by the binding" in out
    assert "identity.first_name = 'Ethan'" in out
    assert "set_value ✓ operated" in out and "✓ the page shows 'Ethan'" in out
    # An expression, and the formatter that fit the select's options.
    assert "/\\bgraduat/ → education.graduation_date" in out
    assert "date_as_term_option → 'Fall 2027'" in out and "other renderings" in out
    assert "native_select" in out and "options 2: Spring 2027 | Fall 2027" in out
    # What was not filled says why.
    assert "Not filled in this pass (1)" in out and "Anything else?" in out
    assert "nothing: no pattern yet; the model pass, then you" in out
    assert "Submit application" not in out  # buttons are never controls


def test_stepping_skips_on_s_and_stops_pausing_on_c(page) -> None:  # type: ignore[no-untyped-def]
    page, asked = run(page, ["s", "c"])
    # Paused twice: skipped First name, then stopped pausing; the rest still filled.
    assert len(asked) == 2
    assert page.input_value("#first") == ""
    assert page.input_value("#email") == "ethan@example.com"
    assert page.eval_on_selector("#grad", "el => el.selectedOptions[0].text") == "Fall 2027"
