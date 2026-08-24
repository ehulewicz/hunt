from pathlib import Path

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.autofill import build_autofill_plan
from personal_os_worker.browser import (
    apply_autofill,
    click_safe_transition,
    inspect_fields,
    reveal_application_form,
)

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_greenhouse_resume_first_survives_rerender_and_submit_is_absent(
    browser_page,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    fields = inspect_fields(browser_page)
    for field in fields:
        field["ats"] = "greenhouse"
    assert not any(field.get("type") in {"submit", "password", "hidden"} for field in fields)
    plan = build_autofill_plan(
        fields,
        profile={
            "legal_name": "Alex Example",
            "email": "ethan@example.com",
            "country": "United States",
        },
        approved_answers=[
            {
                "normalized_key": "why this team",
                "answer_text": "The work matters.",
                "reuse_policy": "exact",
            }
        ],
    )
    resume = tmp_path / "main.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    result = apply_autofill(
        browser_page, fields, plan, resume_path=resume, assert_active=lambda: None
    )
    assert result["submitted"] is False
    assert "First name" in result["filled_fields"]
    assert browser_page.locator("#first").input_value() == "Ethan"


def test_ashby_combobox_and_same_origin_iframe_are_inspected(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "ashby.html").read_text())
    browser_page.wait_for_timeout(100)
    fields = inspect_fields(browser_page)
    assert any(field.get("role") == "combobox" for field in fields)
    assert any(int(field.get("frame_index", 0)) > 0 for field in fields)


def test_lever_captcha_keeps_radio_and_sensitive_checkbox_manual(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "lever.html").read_text())
    fields = inspect_fields(browser_page)
    for field in fields:
        field["ats"] = "lever"
    plan = build_autofill_plan(
        fields,
        profile={"email": "ethan@example.com", "sponsorship_required": False},
        approved_answers=[],
    )
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert browser_page.locator("input[name=terms]").is_checked() is False
    assert browser_page.locator("input[name=sponsor]:checked").count() == 0
    assert any("captcha" in failure["error_message"].casefold() for failure in result["failures"])


def test_ashby_landing_page_apply_control_reveals_the_form(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        """
        <main>
          <h1>Embedded Controls Intern - Spring</h1>
          <a href="#application" onclick="document.querySelector('main').innerHTML = `
            <label>First name<input name='first_name' required></label>
            <label>Last name<input name='last_name' required></label>
            <label>Email<input name='email' type='email' required></label>
            <label>Resume<input name='resume' type='file' required></label>
          `"><button>Apply for this job</button></a>
        </main>
        """
    )

    assert reveal_application_form(browser_page, timeout_ms=2_000) is True
    assert any(field.get("type") == "file" for field in inspect_fields(browser_page))


def test_safe_next_clicks_next_but_never_submit(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        """
        <button id="next" onclick="this.dataset.clicked = 'yes'">Continue</button>
        <button id="submit" onclick="this.dataset.clicked = 'yes'">Submit application</button>
        """
    )

    assert click_safe_transition(browser_page, kind="next") == "Continue"
    assert browser_page.locator("#next").get_attribute("data-clicked") == "yes"
    assert browser_page.locator("#submit").get_attribute("data-clicked") is None
