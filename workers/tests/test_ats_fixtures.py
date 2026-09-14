from pathlib import Path

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.autofill import build_autofill_plan
from personal_os_worker.browser import (
    apply_autofill,
    click_safe_transition,
    inspect_fields,
    reveal_application_form,
    snapshot_values,
)
from personal_os_worker.patterns import resolve_pattern_answer

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


def test_snapshot_values_reads_text_choices_and_selects_by_question(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="email">Email</label><input id="email" type="email" value="a@b.c">
          <label for="why">Why this team?</label><textarea id="why">Because.</textarea>
          <label for="school">School</label>
          <select id="school"><option value="">Pick</option><option value="7">UIC</option></select>
          <fieldset><legend>How did you hear about us?</legend>
            <label><input type="radio" name="src" value="li">LinkedIn</label>
            <label><input type="radio" name="src" value="ref" checked>Referral</label>
          </fieldset>
          <label for="empty">Portfolio</label><input id="empty" type="url">
          <label for="cv">Resume</label><input id="cv" type="file">
        </form>
        """
    )
    browser_page.select_option("#school", label="UIC")
    snapshot = snapshot_values(browser_page, inspect_fields(browser_page))
    assert {key: item["answer_text"] for key, item in snapshot.items()} == {
        "Email": "a@b.c",
        "Why this team?": "Because.",
        "School": "UIC",
        "How did you hear about us?": "Referral",
    }


def test_live_greenhouse_shapes_strip_asterisks_skip_mirrors_and_find_resume_by_id(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="first">First Name*</label><input id="first" type="text">
          <div class="select-shell">
            <span class="a11y"></span>
            <label for="q1">Will you require Visa Sponsorship?*</label>
            <div class="select__input-container">
              <input id="q1" type="text" role="combobox" aria-expanded="false">
            </div>
            <input required="" tabindex="-1" aria-hidden="true" value="">
          </div>
          <div>
            <button type="button">Attach</button>
            <label class="visually-hidden" for="resume">Attach</label>
            <input id="resume" class="visually-hidden" type="file">
          </div>
          <div>
            <button type="button">Attach</button>
            <label class="visually-hidden" for="cover_letter">Attach</label>
            <input id="cover_letter" class="visually-hidden" type="file">
          </div>
        </form>
        """
    )
    fields = inspect_fields(browser_page)
    by_label = {field["label"]: field for field in fields}
    assert set(by_label) == {"First Name", "Will you require Visa Sponsorship?", "Attach"}
    assert by_label["First Name"]["required"] is True
    assert by_label["Will you require Visa Sponsorship?"]["required"] is True
    assert len(fields) == 4  # the aria-hidden mirror input is not a field
    plan = build_autofill_plan(
        fields, profile={"legal_name": "Alex Example"}, approved_answers=[]
    )
    kinds = {fields[fill.index]["selector"]: fill.kind for fill in plan.fills}
    assert kinds == {"#first": "text", "#resume": "resume"}


def test_ashby_combobox_label_comes_from_the_field_entry_not_the_placeholder(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <div class="ashby-application-form-field-entry">
          <label>Location<span>*</span></label>
          <div class="_inputContainer">
            <input placeholder="Start typing..." role="combobox" aria-expanded="false">
          </div>
        </div>
        <div class="ashby-application-form-field-entry">
          <label>Preferred first name</label>
          <input placeholder="Type here...">
        </div>
        """
    )
    labels = [(field["label"], field["required"]) for field in inspect_fields(browser_page)]
    assert labels == [("Location", True), ("Preferred first name", False)]


def test_ashby_radio_groups_take_their_question_from_the_heading_label(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <fieldset class="ashby-application-form-input-radio-group">
          <label class="_heading_f7cvd_52 _required_f7cvd_91">Are you authorized to work?</label>
          <div><label><input type="radio" name="a" value="y">Yes</label></div>
          <div><label><input type="radio" name="a" value="n">No</label></div>
        </fieldset>
        """
    )
    fields = inspect_fields(browser_page)
    assert [(f["label"], f["group"], f["required"]) for f in fields] == [
        ("Yes", "Are you authorized to work?", True),
        ("No", "Are you authorized to work?", True),
    ]


def test_ashby_yesno_control_is_fillable_by_the_sanctioned_country_rule(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    question = "Are you a citizen or resident of a sanctioned country?"
    browser_page.set_content(
        f"""
        <div class="ashby-application-form-field-entry">
          <label class="ashby-application-form-question-title _required_f7cvd_91">
            {question}
          </label>
          <div class="ashby-application-form-input-yesno">
            <button type="button" aria-pressed="false" data-option="yes">Yes</button>
            <button type="button" aria-pressed="false" data-option="no">No</button>
            <input type="checkbox" tabindex="-1">
          </div>
        </div>
        <script>
        document.querySelectorAll('.ashby-application-form-input-yesno button[data-option]')
          .forEach(button => button.addEventListener('click', () => {{
            const container = button.closest('.ashby-application-form-input-yesno');
            container.querySelectorAll('button[data-option]')
              .forEach(other => other.setAttribute('aria-pressed', 'false'));
            button.setAttribute('aria-pressed', 'true');
            container.querySelector('input[type="checkbox"]').checked =
              button.dataset.option === 'yes';
          }}));
        </script>
        """
    )
    fields = inspect_fields(browser_page)
    assert [
        (f["type"], f["label"], f["options"], f["filled"], f["value"], f["required"])
        for f in fields
    ] == [("yesno", question, ["Yes", "No"], False, None, True)]

    profile = {"visa_status": "Citizen"}
    plan = build_autofill_plan(
        fields,
        profile={},
        approved_answers=[],
        fallback=lambda asked: resolve_pattern_answer(
            asked, rules=[], profile=profile, resume={}, company="Acme"
        ),
    )
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    assert result["filled_fields"] == [question]
    refreshed = inspect_fields(browser_page)
    assert refreshed[0]["value"] == "No"
    assert refreshed[0]["filled"] is True
    assert snapshot_values(browser_page, refreshed) == {
        question: {"question": question, "answer_text": "No", "control_kind": "yesno"}
    }
