"""The errors a form shows reach the audit, in the page's own words."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.audit import Finding
from personal_os_worker.jobs.browser import field_errors, inspect_fields
from personal_os_worker.jobs.fix.lane import Problem
from personal_os_worker.jobs.session import AutofillSession
from personal_os_worker.jobs.session.context import SessionState

FORM = (Path(__file__).parent / "fixtures" / "field_errors" / "form.html").read_text()


@pytest.fixture(scope="module")
def browser_page():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        page = browser.new_page()
        yield page
        browser.close()


class Browser:
    def __init__(self, page: Any) -> None:
        self.page = page

    def active_page(self, _current: Any = None) -> Any:
        return self.page


def session_on(page: Any) -> AutofillSession:
    context = SimpleNamespace(page=page, browser=Browser(page), link=None)
    session = AutofillSession(cast(Any, context))
    session.state = SessionState()
    return session


def by_question(errors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(error["question"]): error for error in errors}


def test_the_reader_finds_each_kind_of_error_in_the_pages_words(browser_page: Any) -> None:
    browser_page.set_content(FORM)
    errors = by_question(field_errors(browser_page))
    assert errors["Email"]["message"] == "Enter a valid email address"
    assert errors["Phone"]["message"] == "Phone number is too short"
    assert errors["City"]["message"] == "We do not recognize that city"
    assert errors["Zip"]["message"] == "the page marks this field invalid"
    assert errors["Age"]["native"] is True and errors["Age"]["message"]
    assert errors["Email"]["native"] is False


def test_the_reader_keeps_help_text_hidden_fields_and_empty_alerts_out(browser_page: Any) -> None:
    browser_page.set_content(FORM)
    errors = field_errors(browser_page)
    assert "Fine" not in by_question(errors) and "Ghost" not in by_question(errors)
    assert not any("Help text" in str(error["message"]) for error in errors)
    assert not any("Hidden problem" in str(error["message"]) for error in errors)
    assert not any(not str(error["message"]).strip() for error in errors)


def test_an_alert_about_no_one_field_is_form_level(browser_page: Any) -> None:
    browser_page.set_content(FORM)
    level = [error for error in field_errors(browser_page) if not error["question"]]
    assert [error["message"] for error in level] == ["Please correct the errors below."]


def test_a_toast_is_not_an_error(browser_page: Any) -> None:
    browser_page.set_content(
        '<form><div role="alert">Submitting your application</div>'
        '<label for="a">A</label><input id="a"><label for="b">B</label><input id="b"></form>'
    )
    assert field_errors(browser_page) == []


def test_a_filled_field_with_an_error_becomes_a_finding_with_the_pages_words(
    browser_page: Any,
) -> None:
    browser_page.set_content(FORM)
    session = session_on(browser_page)
    session.state.tier_fact["Email"] = "contact.email"
    session.state.tier_answers["Email"] = "not-an-email"
    current = inspect_fields(browser_page)
    findings = {item.question: item for item in session.page_error_findings(current, [])}
    email = findings["Email"]
    assert email.cause == "format_error" and "Enter a valid email address" in email.note
    assert email.fact_key == "contact.email" and email.intended_value == "not-an-email"
    assert (email.evidence or {}).get("page_error") == "Enter a valid email address"
    assert findings["City"].cause == "control_error"
    assert "We do not recognize that city" in findings["City"].note


def test_a_long_form_field_with_an_error_goes_back_to_the_composer(browser_page: Any) -> None:
    browser_page.set_content(
        '<form><label for="why">Why do you want to work here?</label>'
        '<textarea id="why" name="why" aria-invalid="true" aria-errormessage="e">x</textarea>'
        '<span id="e">Answer is too short</span>'
        '<button type="button">Submit application</button></form>'
    )
    session = session_on(browser_page)
    current = inspect_fields(browser_page)
    [finding] = session.page_error_findings(current, [])
    assert finding.cause == "draft_error" and "Answer is too short" in finding.note


def test_an_error_on_a_field_already_found_only_adds_the_pages_words(browser_page: Any) -> None:
    browser_page.set_content(FORM)
    session = session_on(browser_page)
    current = inspect_fields(browser_page)
    existing = Finding("City", "fact_gap", True, "needs a fact", fact_key="contact.city")
    findings = {item.question: item for item in session.page_error_findings(current, [existing])}
    city = findings["City"]
    assert city.cause == "fact_gap" and city.fact_key == "contact.city"
    assert "We do not recognize that city" in city.note and "needs a fact" in city.note
    assert not (city.evidence or {}).get("page_error")


def test_the_fixer_rechecks_a_page_error_against_the_page(browser_page: Any) -> None:
    browser_page.set_content(FORM)
    session = session_on(browser_page)
    problem = Problem(
        key="format_error|example.com|x|Email", cause="format_error", question="Email",
        note="the page says x", target={"kind": "field", "question": "Email"},
        site="example.com", evidence={"page_error": "Enter a valid email address"},
    )
    assert session.problem_state(problem) == "persists"
    browser_page.evaluate("document.getElementById('email').removeAttribute('aria-invalid')")
    assert session.problem_state(problem) == "gone"
