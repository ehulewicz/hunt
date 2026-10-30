from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright
from standing_rules import standing

from personal_os_worker.jobs.autofill import AnswerResolution, build_autofill_plan
from personal_os_worker.jobs.browser import (
    apply_autofill,
    audit_filled_form,
    click_safe_transition,
    has_submit_control,
    inspect_fields,
    reveal_application_form,
    snapshot_entries,
    snapshot_values,
)
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    capture_tab_navigation,
    classify_application_page,
)
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.registry.strategies.combobox import combobox_options
from personal_os_worker.jobs.registry.strategies.native import set_registered_value
from personal_os_worker.jobs.resolve import Knowledge, Resolver

FIXTURES = Path(__file__).parent / "fixtures"


# The bindings these fixtures' questions would get in the Inbox: question key ->
# (fact, formatter the binding prefers).
BINDINGS: dict[str, tuple[str, str | None]] = {
    "are you a citizen or resident of a sanctioned country": (
        "work_auth.sanctioned_country_citizen", None
    ),
    "country": ("address.country", None),
    "email": ("contact.email", None),
    "end date month": ("education.graduation_date", "date_as_month_name"),
    "end date year": ("education.graduation_date", "date_as_year"),
    "first name": ("identity.first_name", None),
    "how many full time semester long co op rotations have you completed prior to this "
    "application select one": ("coop.completed_rotations", None),
    "location city": ("address.location", None),
    "school": ("education.school_name", None),
    "this role requires working onsite at our facility in rochester ny are you able to reliably "
    "commute or relocate to rochester ny to perform the duties of this position onsite by your "
    "start date": ("availability.onsite", None),
    "what are your annual base salary expectations please include currency": (
        "compensation.minimum", None
    ),
    "what are your salary requirements": ("compensation.minimum", None),
    "what is your current cumulative gpa on a 4 0 scale": ("education.gpa", None),
    "what is your expected graduation date term year": ("education.graduation_date", None),
    "what is your notice period to begin working with exegy": (
        "availability.notice_period_weeks", None
    ),
    "will you now or in the future require sponsorship": ("work_auth.requires_sponsorship", None),
    "will you require visa sponsorship": ("work_auth.requires_sponsorship", None),
    "your authorization to work in the country where you live": (
        "work_auth.us_citizen", "citizenship_option"
    ),
    "your email address": ("contact.email", None),
    "your name": ("identity.legal_name", None),
    "your phone number": ("contact.phone", None),
}


def knowing(facts: dict[str, Any], *, company: str = "Acme") -> Resolver:
    """Facts decide the value; accepted bindings only say which fact a question asks for."""
    rows = [{"key": key, "value": value} for key, value in facts.items()]
    patterns = [
        {"id": f"b{index}", "kind": "binding", "question_key": question, "fact_key": key,
         "formatter": formatter, "resolver_id": f"binding:{key}", "status": "active"}
        for index, (question, (key, formatter)) in enumerate(BINDINGS.items())
    ]
    return Resolver(
        Knowledge.build(facts=rows, career=None, patterns=patterns, company=company)
    )


class Stub:
    """Answers by question label, as an accepted binding would."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    def resolve(self, field: dict[str, Any]) -> AnswerResolution | None:
        value = self.answers.get(str(field.get("label") or ""))
        return AnswerResolution(value, "pattern", "binding:test") if value else None


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
        resolver=knowing(
            {
                "identity.legal_name": "Alex Example",
                "contact.email": "ethan@example.com",
                "address.country": "United States",
            }
        ),
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


def test_ashby_megazone_fixture_fills_standing_facts(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "ashby_megazone.html").read_text())
    fields = inspect_fields(browser_page)
    resolver = knowing(
        {
            "compensation.minimum": {"amount": 40, "currency": "USD", "period": "hour"},
            "education.graduation_date": "2027-12-15",
            "coop.completed_rotations": 4,
            "availability.onsite": True,
        },
        company="Megazone",
    )
    plan = build_autofill_plan(fields, resolver=resolver)
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert result["failures"] == []
    assert browser_page.input_value("#salary") == "40"
    assert browser_page.input_value("#graduation") == "Fall 2027"
    assert browser_page.locator("input[name=coops]:checked").get_attribute("value") == "3+"
    assert browser_page.locator('[data-option="yes"]').get_attribute("data-state") == "selected"


def test_ashby_submitted_fixture_wins_over_recaptcha_textarea(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "ashby_submitted.html").read_text())
    assert (
        classify_application_page(browser_page, inspect_fields(browser_page))
        == ApplicationPageState.SUBMITTED
    )


def test_ashby_navigation_pair_and_plain_submit_form_are_not_submission(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "ashby_megazone.html").read_text())
    navigation = capture_tab_navigation(browser_page)
    assert navigation is not None
    assert navigation.overview_url == "https://jobs.ashbyhq.com/megazone/role-1"
    assert navigation.application_url.endswith("/megazone/role-1/application")
    assert (
        classify_application_page(browser_page, inspect_fields(browser_page), navigation)
        == ApplicationPageState.FORM
    )


def test_ashby_exegy_fixture_fills_contact_authorization_notice_and_salary(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "ashby_exegy.html").read_text())
    fields = inspect_fields(browser_page)
    resolver = knowing(
        {
            "identity.legal_name": "Ethan Example",
            "contact.email": "e@example.com",
            "contact.phone": "555",
            "work_auth.us_citizen": True,
            "availability.notice_period_weeks": 2,
            "compensation.minimum": {"amount": 40, "currency": "USD", "period": "hour"},
        },
        company="Exegy",
    )
    plan = build_autofill_plan(fields, resolver=resolver)
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert result["failures"] == []
    assert browser_page.input_value("#name") == "Ethan Example"
    assert browser_page.input_value("#email") == "e@example.com"
    assert browser_page.input_value("#phone") == "555"
    assert (
        browser_page.locator('input[name="authorization"]:checked').get_attribute("value")
        == "nationality"
    )
    assert browser_page.locator('input[name="notice"]:checked').get_attribute("value") == "2-4"
    assert browser_page.input_value("#salary") == "$83,200 USD"
    assert browser_page.input_value("#expiry") == ""


def test_lever_captcha_keeps_radio_and_sensitive_checkbox_manual(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "lever.html").read_text())
    fields = inspect_fields(browser_page)
    for field in fields:
        field["ats"] = "lever"
    plan = build_autofill_plan(
        fields,
        resolver=knowing(
            {"contact.email": "ethan@example.com", "work_auth.requires_sponsorship": False}
        ),
    )
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert browser_page.locator("input[name=terms]").is_checked() is False
    assert browser_page.locator("input[name=sponsor]:checked").count() == 0
    assert any("captcha" in failure["error_message"].casefold() for failure in result["failures"])


LEVER_LOCATION = """
<li class="application-question" data-qa="structured-contact-location-question">
  <label>
    <div class="application-label">Current location <span class="required">&#10033;</span></div>
    <div class="application-field">
      <input class="location-input" id="location-input" type="text" name="location" required>
      <input id="selected-location" type="hidden" name="selectedLocation" value="">
      <div class="dropdown-container">
        <div class="dropdown-results"></div>
        <div class="dropdown-no-results">No location found. Try entering a different location</div>
      </div>
    </div>
  </label>
</li>
"""


def test_lever_location_label_ignores_the_typeahead_dropdown(browser_page) -> None:  # type: ignore[no-untyped-def]
    """Lever nests the whole field, dropdown included, inside the question's label.

    Reading that label wholesale names the question after whatever the typeahead
    is showing, and the suggestions change on every keystroke, so the field's
    fingerprint churns and `wait_and_advance` calls it a new page step.
    """
    browser_page.set_content(LEVER_LOCATION)
    location = next(
        field for field in inspect_fields(browser_page) if field.get("name") == "location"
    )
    assert location["label"] == "Current location"

    before = location["fingerprint"]
    browser_page.eval_on_selector(
        ".dropdown-results",
        "el => el.innerHTML = '<div>Atlanta, GA, USA</div><div>Atlantic City, NJ, USA</div>'",
    )
    after = next(
        field for field in inspect_fields(browser_page) if field.get("name") == "location"
    )["fingerprint"]
    assert after == before


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


def test_lever_style_duplicate_apply_links_to_the_same_page_are_not_ambiguous(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    """Lever repeats the Apply link at the top and bottom of the posting."""
    browser_page.set_content(
        """
        <a data-qa="btn-apply-top" href="/apply">apply for this job</a>
        <p>Job description ...</p>
        <a data-qa="btn-apply-bottom" href="/apply">apply for this job</a>
        <script>
        document.querySelectorAll('a[href="/apply"]').forEach(link => {
          link.addEventListener('click', event => {
            event.preventDefault();
            window.__applyClicks = (window.__applyClicks || 0) + 1;
            document.body.innerHTML =
              '<label>First name<input name="first_name" required></label>' +
              '<label>Email<input name="email" required></label>' +
              '<label>Resume<input name="resume" type="file" required></label>';
          });
        });
        </script>
        """
    )

    assert reveal_application_form(browser_page, timeout_ms=2_000) is True
    assert any(field.get("name") == "email" for field in inspect_fields(browser_page))
    assert browser_page.evaluate("window.__applyClicks") == 1


def test_apply_links_to_different_destinations_stay_ambiguous(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <a href="/apply-a">apply now</a>
        <a href="/apply-b">apply now</a>
        """
    )

    assert click_safe_transition(browser_page, kind="apply") is None


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


def test_submit_control_is_detected_without_clicking_it(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        """
        <button>Continue</button>
        <button id="submit" onclick="this.dataset.clicked = 'yes'">Submit application</button>
        """
    )
    assert has_submit_control(browser_page) is True
    assert browser_page.locator("#submit").get_attribute("data-clicked") is None


def test_snapshot_joins_every_checked_box_of_a_group(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="email">Email</label><input id="email" type="email" value="a@b.c">
          <fieldset><legend>Technologies you have used</legend>
            <label><input type="checkbox" name="t" value="py" checked>Python</label>
            <label><input type="checkbox" name="t" value="go">Go</label>
            <label><input type="checkbox" name="t" value="ts" checked>Typescript</label>
          </fieldset>
        </form>
        """
    )
    snapshot = snapshot_values(browser_page, inspect_fields(browser_page))
    assert snapshot["Technologies you have used"]["answer_text"] == "Python, Typescript"


def test_snapshot_distinguishes_empty_controls_from_failed_reads(
    browser_page, monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    from personal_os_worker.jobs import browser as browser_module

    browser_page.set_content("""
        <label for="location">Location</label><input id="location">
        <label for="school">School</label>
        <select id="school"><option value="">Choose a school</option></select>
        <label><input type="checkbox">Remote</label>
        <label for="unreadable">Unreadable</label><input id="unreadable" value="Known">
    """)
    fields = inspect_fields(browser_page)
    original = browser_module.fresh_control

    def read_control(page, field):  # type: ignore[no-untyped-def]
        if field.get("label") == "Unreadable":
            raise Error("Control detached during read")
        return original(page, field)

    monkeypatch.setattr(browser_module, "fresh_control", read_control)
    entries = snapshot_entries(browser_page, fields)
    assert {entry["question"]: entry["answer_text"] for entry in entries} == {
        "Location": "", "School": "", "Remote": "",
    }
    assert snapshot_values(browser_page, fields) == {}


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


def test_custom_consent_radio_detects_a_manual_selection(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    question = "Check Yes or No to indicate your agreement to receive text message updates"
    browser_page.set_content(
        f"""
        <div class="application-field">
          <div>{question}</div>
          <a href="/privacy">Privacy Policy</a>
          <div>
            <label><input required type="radio" name="sms" value="yes" checked>
              Yes - I consent to receiving text messages</label>
            <label><input required type="radio" name="sms" value="no">
              No - I do not consent to receiving text messages</label>
          </div>
        </div>
        """
    )
    fields = inspect_fields(browser_page)
    assert {field["group"] for field in fields} == {question}
    assert [field["filled"] for field in fields] == [True, False]
    plan = build_autofill_plan(fields)
    assert plan.fills == ()
    assert plan.unanswered_required_questions == ()
    audit = audit_filled_form(fields)
    assert audit["required_unfilled"] == []
    assert audit["filled_fields"] == [question]
    snapshot = snapshot_values(browser_page, fields)
    assert snapshot[question]["answer_text"] == "Yes - I consent to receiving text messages"


def test_registered_text_value_receives_focus_and_blur_commit(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <input id="email" type="email">
        <input id="next">
        <script>
          window.commitEvents = [];
          const email = document.querySelector('#email');
          email.addEventListener('focus', () => window.commitEvents.push('focus'));
          email.addEventListener('input', () => window.commitEvents.push('input'));
          email.addEventListener('change', () => window.commitEvents.push('change'));
          email.addEventListener('click', () => window.commitEvents.push('click'));
          email.addEventListener('blur', () => window.commitEvents.push('blur'));
        </script>
        """
    )
    set_registered_value(browser_page.locator("#email"), "alex.example@example.com")
    assert browser_page.locator("#email").input_value() == "alex.example@example.com"
    assert browser_page.evaluate("window.commitEvents") == [
        "focus",
        "input",
        "change",
        "click",
        "blur",
    ]


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
        fields, resolver=knowing({"identity.legal_name": "Alex Example"})
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


def test_ashby_education_controls_use_nested_labels_and_search_for_school(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <div class="ashby-application-form-field-entry">
          <label class="_required_outer" for="education">Education History</label>
          <div class="ashby-application-form-input-education-entry">
            <div class="stack vertical">
              <label class="_required_nested" for="school-without-a-matching-id">School</label>
              <div><input placeholder="Search schools..." role="combobox"></div>
            </div>
            <div class="dates">
              <div class="date-field">
                <label>Start Date</label>
                <div>
                  <select>
                    <option value="">Month...</option><option value="8">August</option>
                  </select>
                  <select>
                    <option value="">Year...</option><option value="2024">2024</option>
                  </select>
                </div>
              </div>
              <div class="date-field">
                <label>End Date</label>
                <div>
                  <select>
                    <option value="">Month...</option><option value="12">December</option>
                  </select>
                  <select>
                    <option value="">Year...</option><option value="2027">2027</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div id="school-menu"></div>
        <script>
        (() => {
          const schools = ['Yeshiva University', 'Example Institute of Technology'];
          const box = document.querySelector('input[role="combobox"]');
          const menu = document.querySelector('#school-menu');
          const render = () => {
            const needle = box.value.toLowerCase();
            menu.innerHTML = '';
            schools.filter(school => !needle || school.toLowerCase().includes(needle))
              .forEach(school => {
                const option = document.createElement('div');
                option.setAttribute('role', 'option');
                option.textContent = school === 'Example Institute of Technology'
                  ? school + '\\nUnited States\\ngatech.edu' : school;
                option.addEventListener('click', () => {
                  box.value = school;
                  menu.innerHTML = '';
                });
                menu.appendChild(option);
              });
          };
          box.addEventListener('click', render);
          box.addEventListener('input', render);
          box.addEventListener('keydown', event => {
            if (event.key === 'Escape') menu.innerHTML = '';
          });
        })();
        </script>
        """
    )

    fields = inspect_fields(browser_page)
    assert [(field["label"], field["required"]) for field in fields] == [
        ("School", True),
        ("Start Date Month", False),
        ("Start Date Year", False),
        ("End Date Month", False),
        ("End Date Year", False),
    ]
    assert [field["options"] for field in fields[1:]] == [
        ["August"],
        ["2024"],
        ["December"],
        ["2027"],
    ]

    plan = build_autofill_plan(
        fields,
        resolver=knowing(
            {
                "education.school_name": "Example Institute of Technology",
                "education.graduation_date": "2027-12-15",
            }
        ),
    )
    assert [(fill.index, fill.value) for fill in plan.fills] == [
        (0, "Example Institute of Technology"),
        (3, "December"),
        (4, "2027"),
    ]
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert result["failures"] == []
    assert browser_page.locator('input[role="combobox"]').input_value() == (
        "Example Institute of Technology"
    )
    assert browser_page.locator("select").nth(2).input_value() == "12"
    assert browser_page.locator("select").nth(3).input_value() == "2027"


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

    plan = build_autofill_plan(
        fields, resolver=knowing({"work_auth.sanctioned_country_citizen": False})
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


REACT_SELECT_COUNTRY = """
<div class="select">
  <label for="country">Country</label>
  <div class="select__control">
    <div class="select__value-container">
      <input id="country" class="select__input" role="combobox" autocomplete="off">
    </div>
  </div>
  <div class="select__menu"></div>
</div>
<ul class="iti__country-list" style="display: none">
  <li role="option">United States+1</li><li role="option">United Kingdom+44</li>
</ul>
<script>
(() => {
const codes = ['United Kingdom (+44)', 'United States (+1)', 'Canada (+1)'];
const box = document.querySelector('#country');
const holder = document.querySelector('.select__value-container');
const menu = document.querySelector('.select__menu');
box.addEventListener('input', () => {
  const needle = box.value.toLowerCase();
  menu.innerHTML = '';
  codes.filter(code => code.toLowerCase().includes(needle)).forEach(code => {
    const option = document.createElement('div');
    option.setAttribute('role', 'option');
    option.textContent = code;
    option.addEventListener('mousedown', () => {
      holder.querySelector('.select__single-value')?.remove();
      const chosen = document.createElement('div');
      chosen.className = 'select__single-value';
      chosen.textContent = code.replace(/^.* \\(/, '').replace(')', '');
      holder.prepend(chosen);
      box.value = '';
      menu.innerHTML = '';
    });
    menu.appendChild(option);
  });
});
box.addEventListener('blur', () => setTimeout(() => { menu.innerHTML = ''; }, 0));
})();
</script>
"""

REACT_SELECT_POLICY = """
<div class="select">
  <label for="source">How did you hear about DV Trading?</label>
  <div class="select__control"><div class="select__value-container">
    <input id="source" class="select__input" role="combobox" aria-required="true">
  </div></div>
  <div class="select__menu"></div>
</div>
<div class="select">
  <label for="terms">Terms and Conditions</label>
  <div class="select__control"><div class="select__value-container">
    <input id="terms" class="select__input" role="combobox" aria-required="true">
  </div></div>
  <div class="select__menu"></div>
</div>
<script>
(() => {
const menus = {
  source: ['LinkedIn', 'Handshake', 'DV Website', 'Referral', 'Other'],
  terms: ["Yes, I have read and agree to DV Trading's privacy policy"],
};
for (const [id, choices] of Object.entries(menus)) {
  const box = document.getElementById(id);
  const wrapper = box.closest('.select');
  const holder = wrapper.querySelector('.select__value-container');
  const menu = wrapper.querySelector('.select__menu');
  const render = () => {
    const needle = box.value.toLowerCase();
    menu.innerHTML = '';
    choices.filter(c => c.toLowerCase().includes(needle)).forEach(choice => {
      const option = document.createElement('div');
      option.setAttribute('role', 'option');
      option.textContent = choice;
      option.addEventListener('mousedown', () => {
        holder.querySelector('.select__single-value')?.remove();
        const chosen = document.createElement('div');
        chosen.className = 'select__single-value';
        chosen.textContent = choice;
        holder.prepend(chosen);
        box.value = '';
        menu.innerHTML = '';
      });
      menu.appendChild(option);
    });
  };
  box.addEventListener('click', render);
  box.addEventListener('input', render);
  box.addEventListener('keydown', e => { if (e.key === 'Escape') menu.innerHTML = ''; });
  box.addEventListener('blur', () => setTimeout(() => { menu.innerHTML = ''; }, 0));
}
})();
</script>
"""

PLACES_CITY = """
<div class="field">
  <label for="city">Location (City)</label>
  <input id="city" role="combobox" required>
  <div class="menu"></div>
</div>
<script>
(() => {
const box = document.querySelector('#city');
const menu = document.querySelector('.menu');
let pending = null;
box.addEventListener('input', () => {
  menu.innerHTML = '';
  clearTimeout(pending);
  if (!box.value.toLowerCase().startsWith('atl')) return;
  pending = setTimeout(() => {
    const option = document.createElement('div');
    option.setAttribute('role', 'option');
    option.textContent = 'Atlanta, Georgia, United States';
    option.addEventListener('click', () => {
      box.value = option.textContent; menu.innerHTML = '';
    });
    menu.appendChild(option);
  }, 150);
});
})();
</script>
"""


def test_react_select_is_picked_by_prefix_and_read_back_from_its_rendered_choice(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(REACT_SELECT_COUNTRY)
    fields = inspect_fields(browser_page)
    assert [(f["label"], f["role"], f["required"], f["filled"]) for f in fields] == [
        ("Country", "combobox", False, False)
    ]

    plan = build_autofill_plan(fields, resolver=knowing({"address.country": "United States"}))
    assert [fill.value for fill in plan.fills] == ["United States"]
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    # The menu said "United States (+1)"; the control now shows only "+1".
    assert result["failures"] == []
    assert result["filled_fields"] == ["Country"]
    assert browser_page.locator(".select__single-value").inner_text() == "+1"
    assert browser_page.locator("#country").input_value() == ""
    refreshed = inspect_fields(browser_page)
    assert refreshed[0]["filled"] is True
    assert snapshot_values(browser_page, refreshed) == {
        "Country": {"question": "Country", "answer_text": "+1", "control_kind": "input"}
    }
    assert audit_filled_form(refreshed)["required_unfilled"] == []


def test_how_heard_and_consent_typeaheads_are_chosen_from_the_live_menu(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(REACT_SELECT_POLICY)
    fields = inspect_fields(browser_page)
    # The standing rules are patterns: each typeahead is chosen against its live menu.
    plan = build_autofill_plan(fields, resolver=standing())
    assert [(fill.value, fill.chooser) for fill in plan.fills] == [
        (None, "source"),
        (None, "consent"),
    ]
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert result["filled_fields"] == ["How did you hear about DV Trading?", "Terms and Conditions"]
    assert result["applied"] == {
        0: "DV Website",
        1: "Yes, I have read and agree to DV Trading's privacy policy",
    }
    shown = [el.inner_text() for el in browser_page.locator(".select__single-value").all()]
    assert shown == ["DV Website", "Yes, I have read and agree to DV Trading's privacy policy"]
    assert audit_filled_form(inspect_fields(browser_page))["required_unfilled"] == []


def test_places_city_typeahead_is_filled_by_the_location_rule(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(PLACES_CITY)
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(
        fields,
        resolver=knowing(
            {"address.city": "Atlanta", "address.state": "Georgia",
             "address.country": "United States"}
        ),
    )
    assert [fill.value for fill in plan.fills] == ["Atlanta, Georgia, United States"]
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert browser_page.locator("#city").input_value() == "Atlanta, Georgia, United States"
    assert snapshot_values(browser_page, inspect_fields(browser_page)) == {
        "Location (City)": {
            "question": "Location (City)",
            "answer_text": "Atlanta, Georgia, United States",
            "control_kind": "input",
        }
    }


FREE_ENTRY_GPA = """
<div class="select">
  <label for="gpa">What is your current cumulative GPA (on a 4.0 scale)?</label>
  <div class="select__control"><div class="select__value-container">
    <input id="gpa" class="select__input" role="combobox" aria-required="true" autocomplete="off">
  </div></div>
</div>
<script>
(() => {
const box = document.querySelector('#gpa');
const holder = document.querySelector('.select__value-container');
box.addEventListener('keydown', event => {
  if (event.key !== 'Enter' || !box.value.trim()) return;
  const chosen = document.createElement('div');
  chosen.className = 'select__single-value';
  chosen.textContent = box.value.trim();
  holder.prepend(chosen);
  box.value = '';
});
})();
</script>
"""


def test_free_entry_typeahead_commits_the_typed_value_with_enter(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(FREE_ENTRY_GPA)
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(fields, resolver=knowing({"education.gpa": 3.78}))
    assert [fill.value for fill in plan.fills] == ["3.78"]
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert browser_page.locator(".select__single-value").inner_text() == "3.78"
    refreshed = inspect_fields(browser_page)
    assert refreshed[0]["filled"] is True
    assert snapshot_values(browser_page, refreshed)[refreshed[0]["label"]]["answer_text"] == "3.78"
    # Already answered: no tier plans it again.
    replan = build_autofill_plan(refreshed, resolver=knowing({"education.gpa": 4.0}))
    assert replan.fills == ()


GPA_RANGES = """
<div class="select">
  <label for="gpa">What is your current cumulative GPA (on a 4.0 scale)?</label>
  <div class="select__control"><div class="select__value-container">
    <input id="gpa" class="select__input" role="combobox" aria-required="true" autocomplete="off">
  </div></div>
  <div class="select__menu"></div>
</div>
<script>
(() => {
const ranges = ['3.9 or above', '3.6-3.8', 'Below 3.6'];
const box = document.querySelector('#gpa');
const holder = document.querySelector('.select__value-container');
const menu = document.querySelector('.select__menu');
const render = () => {
  const needle = box.value.toLowerCase();
  menu.innerHTML = '';
  ranges.filter(range => range.toLowerCase().includes(needle)).forEach(range => {
    const option = document.createElement('div');
    option.setAttribute('role', 'option');
    option.textContent = range;
    option.addEventListener('mousedown', () => {
      const chosen = document.createElement('div');
      chosen.className = 'select__single-value';
      chosen.textContent = range;
      holder.prepend(chosen);
      box.value = '';
      menu.innerHTML = '';
    });
    menu.appendChild(option);
  });
};
box.addEventListener('focus', render);
box.addEventListener('click', render);
box.addEventListener('input', render);
box.addEventListener('keydown', event => { if (event.key === 'Escape') menu.innerHTML = ''; });
})();
</script>
"""


def test_gpa_number_picks_the_listed_range_without_typing_it_away(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(GPA_RANGES)
    fields = inspect_fields(browser_page)
    assert combobox_options(browser_page, browser_page.locator("#gpa")) == [
        "3.9 or above",
        "3.6-3.8",
        "Below 3.6",
    ]
    assert browser_page.locator("[role=option]").count() == 0  # menu closed again

    plan = build_autofill_plan(fields, resolver=knowing({"education.gpa": 3.78}))
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert browser_page.locator(".select__single-value").inner_text() == "3.6-3.8"


def test_a_transcript_upload_takes_the_tracked_transcript(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="resume">Resume/CV</label><input id="resume" type="file" required>
          <label for="transcript">Unofficial Transcript</label>
          <input id="transcript" type="file">
          <label for="first">First name</label><input id="first" name="first_name" required>
          <label for="email">Email</label><input id="email" name="email" required>
        </form>
        """
    )
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    transcript = tmp_path / "Transcript.pdf"
    transcript.write_bytes(b"%PDF-1.4 transcript")
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(fields)
    assert sorted(fill.kind for fill in plan.fills) == ["resume", "transcript"]

    result = apply_autofill(
        browser_page,
        fields,
        plan,
        resume_path=resume,
        transcript_path=transcript,
        assert_active=lambda: None,
    )

    assert result["failures"] == []
    assert set(result["filled_fields"]) == {"Resume/CV", "Unofficial Transcript"}
    assert browser_page.locator("#transcript").evaluate("el => el.files[0].name") == (
        "Transcript.pdf"
    )

    without = apply_autofill(
        browser_page, fields, plan, resume_path=resume, assert_active=lambda: None
    )
    assert without["skipped_fields"] == ["Unofficial Transcript (no transcript provided)"]


LEVER_CUSTOM_QUESTIONS = """
<ul>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width text">
        <div class="text">Please state your full legal name:<span class="required">✱</span></div>
      </div>
      <div class="application-field full-width required-field">
        <input required="required" class="card-field-input" type="text"
          placeholder="Type your response" name="cards[a][field0]">
      </div>
    </div>
  </li>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width multiple-choice">
        <div class="text">How did you hear about us?<span class="required">✱</span></div>
      </div>
      <div class="application-field full-width required-field">
        <ul data-qa="multiple-choice">
          <li><label><input type="radio" name="cards[b][field0]" required="required">
            <span class="application-answer-alternative">Campus visit</span></label></li>
          <li><label><input type="radio" name="cards[b][field0]" required="required">
            <span class="application-answer-alternative">Referral</span></label></li>
        </ul>
      </div>
    </div>
  </li>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width multiple-select">
        <div class="text">Which degrees have you already completed, if any?</div>
      </div>
      <div class="application-field full-width">
        <ul data-qa="checkboxes">
          <li><label><input type="checkbox" name="cards[c][field0]">
            <span class="application-answer-alternative">Associate degree</span></label></li>
          <li><label><input type="checkbox" name="cards[c][field0]">
            <span class="application-answer-alternative">Bachelor's degree</span></label></li>
        </ul>
      </div>
    </div>
  </li>
</ul>
"""

LEVER_EEO_RACE = """
<div class="application-question">
  <label>
    <div class="application-label multiple-choice">Race</div>
    <div class="application-field">
      <ul data-qa="multiple-choice">
        <li><label><input type="radio" name="eeo[race]">
          <span class="eeo-option-text application-answer-alternative">Hispanic or Latino</span>
          <div class="eeo-option-description">A person of Cuban, Mexican origin.</div>
        </label></li>
        <li><label><input type="radio" name="eeo[race]">
          <span class="eeo-option-text application-answer-alternative">White</span>
          <div class="eeo-option-description">A person having origins in Europe.</div>
        </label></li>
      </ul>
    </div>
  </label>
</div>
"""

LEVER_BUILTIN_FULL_NAME = """
<li class="application-question">
  <label>
    <div class="application-label">Full name<span class="required">✱</span></div>
    <div class="application-field"><input type="text" name="name" required></div>
  </label>
</li>
"""


def test_lever_custom_question_groups_and_single_field_label_are_recognized(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(LEVER_CUSTOM_QUESTIONS)
    fields = {f["label"] or f["group"]: f for f in inspect_fields(browser_page)}

    assert fields["Please state your full legal name:"]["group"] == ""
    assert fields["Please state your full legal name:"]["required"] is True

    assert fields["Campus visit"]["group"] == "How did you hear about us?"
    assert fields["Referral"]["group"] == "How did you hear about us?"
    assert fields["Campus visit"]["required"] is True

    assert fields["Associate degree"]["group"] == (
        "Which degrees have you already completed, if any?"
    )
    assert fields["Associate degree"]["type"] == "checkbox"


def test_lever_custom_radio_group_fills_through_the_fallback_tier(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(LEVER_CUSTOM_QUESTIONS)
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(
        fields,
        resolver=Stub(
            {
                "Please state your full legal name:": "Alex Example",
                "How did you hear about us?": "Referral",
            }
        ),
    )
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert browser_page.locator('input[name="cards[a][field0]"]').input_value() == (
        "Alex Example"
    )
    referral = browser_page.get_by_text("Referral", exact=True).locator(
        "xpath=preceding-sibling::input"
    )
    assert referral.is_checked()


def test_lever_eeo_race_group_reports_the_clean_option_text(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(LEVER_EEO_RACE)
    fields = inspect_fields(browser_page)
    assert [(f["label"], f["group"]) for f in fields] == [
        ("Hispanic or Latino", "Race"),
        ("White", "Race"),
    ]


def test_lever_builtin_field_label_is_unaffected(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(LEVER_BUILTIN_FULL_NAME)
    fields = inspect_fields(browser_page)
    # `.application-label` now names the field, so the required marker Lever
    # renders beside the question is no longer part of it.
    assert fields[0]["label"] == "Full name"
    assert fields[0]["group"] == ""
    assert fields[0]["required"] is True


def test_greenhouse_style_resume_rerender_still_verifies_as_uploaded(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    """Greenhouse removes the file input on a successful upload and replaces it
    with a filename summary, shifting every later control's index by one; a
    stale index must not read as an empty, unrelated control."""
    browser_page.set_content(
        """
        <form>
          <label for="resume">Resume/CV</label><input id="resume" type="file" required>
          <label for="cover_letter">Cover Letter</label><input id="cover_letter" type="file">
        </form>
        <script>
        document.querySelector('#resume').addEventListener('change', () => {
          const input = document.querySelector('#resume');
          const summary = document.createElement('div');
          summary.textContent = input.files[0].name;
          input.replaceWith(summary);
        });
        </script>
        """
    )
    resume = tmp_path / "Alex_Example.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    fields = inspect_fields(browser_page)
    for field in fields:
        field["ats"] = "greenhouse"
    plan = build_autofill_plan(fields)

    result = apply_autofill(
        browser_page, fields, plan, resume_path=resume, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert "Resume/CV" in result["filled_fields"]
    assert browser_page.locator("#resume").count() == 0  # confirms the rerender happened
    assert browser_page.get_by_text("Alex_Example.pdf").is_visible()


def test_a_captchas_response_box_is_never_a_question(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        '<label for="first">First Name</label><input id="first">'
        '<div class="g-recaptcha" data-sitekey="x">'
        '<textarea id="g-recaptcha-response" name="g-recaptcha-response"></textarea></div>'
        '<textarea name="h-captcha-response"></textarea>'
    )
    assert [field["label"] for field in inspect_fields(browser_page)] == ["First Name"]


def test_readback_accepts_a_masked_phone_and_rejects_a_wrong_select_option(  # type: ignore[no-untyped-def]
    browser_page,
) -> None:
    from personal_os_worker.jobs.browser import same_entry, verify_control

    assert same_entry("(555) 010-0199", "5550100199")
    assert same_entry("+1 555-010-0199", "5550100199")
    assert not same_entry("(555) 010-0198", "5550100199")
    assert not same_entry("1999", "2027")  # short numbers are not phones
    browser_page.set_content(
        '<label for="s">Veteran</label><select id="s"><option value="1">I am a veteran</option>'
        '<option value="2">I am not a veteran</option></select>'
    )
    [field] = inspect_fields(browser_page)
    browser_page.select_option("#s", value="1")
    assert not verify_control(browser_page, field, "I am not a veteran")
    browser_page.select_option("#s", value="2")
    assert verify_control(browser_page, field, "I am not a veteran")
    assert verify_control(browser_page, field, "2")  # select_by_value reports the value


def test_a_collapsed_popups_search_box_is_not_a_question(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        '<label for="phone">Phone</label><input id="phone" type="tel">'
        '<div role="dialog" style="display:none"><input type="search" aria-label="Search"'
        ' role="combobox"></div>'
        '<div role="dialog"><label for="open">Visible in an open dialog</label>'
        '<input id="open"></div>'
    )
    labels = [field["label"] for field in inspect_fields(browser_page)]
    assert labels == ["Phone", "Visible in an open dialog"]


LEVER_LOCATION_LIVE = LEVER_LOCATION + """
<script>
const box = document.querySelector('#location-input');
const results = document.querySelector('.dropdown-results');
let chosen = '';
box.addEventListener('input', () => {
  chosen = '';
  results.innerHTML = box.value.toLowerCase().startsWith('atlanta') && box.value.length < 12
    ? '<div class="result">Atlanta, TX, USA</div><div class="result">Atlanta, GA, USA</div>'
    : '';
  results.querySelectorAll('.result').forEach(item => item.addEventListener('mousedown', () => {
    chosen = item.textContent; box.value = chosen;
    document.querySelector('#selected-location').value = chosen; results.innerHTML = '';
  }));
});
// Lever forgets typed text that was never chosen from the list.
box.addEventListener('change', () => { if (!chosen) box.value = ''; });
</script>
"""


def test_a_suggestion_list_without_aria_is_typed_into_and_picked(browser_page) -> None:  # type: ignore[no-untyped-def]
    from personal_os_worker.jobs.registry.strategies import CANDIDATES
    from personal_os_worker.jobs.repair.repairer import repair_strategy

    browser_page.set_content(LEVER_LOCATION_LIVE)
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(
        fields, resolver=Stub({"Current location": "Atlanta, Georgia, United States"})
    )
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    # A plain set_value is cleared on blur: the live fill fails readback ...
    assert result["failures"] and result["failures"][0]["stopped_at"] == "strategy"
    assert CANDIDATES["text_entry"] == ("set_value", "type_and_pick_suggestion")
    # ... and repair finds the suggestion that names the same city and state.
    location = next(field for field in fields if field.get("name") == "location")
    candidate = repair_strategy(browser_page, location, "Atlanta, Georgia, United States")
    assert candidate is not None and candidate["strategy"] == "type_and_pick_suggestion"
    assert browser_page.input_value("#location-input") == "Atlanta, GA, USA"
    assert browser_page.input_value("#selected-location") == "Atlanta, GA, USA"


def test_a_choice_lands_on_its_own_question_whatever_was_filtered_before_it(  # type: ignore[no-untyped-def]
    browser_page,
) -> None:
    """Lever: two radio groups, hidden inputs and a captcha box earlier in the page.

    The positional fallback counted every input while the capture skipped the
    hidden ones, so "Yes" for export control checked another question's "yes".
    """
    browser_page.set_content(
        '<input type="hidden" name="h1"><input type="hidden" name="h2">'
        '<textarea name="h-captcha-response"></textarea>'
        '<fieldset><legend>Earlier question?</legend>'
        '<label><input type="radio" name="cards[a][field1]" value="yes">yes</label>'
        '<label><input type="radio" name="cards[a][field1]" value="no">no</label></fieldset>'
        '<fieldset><legend>Export control: are you a U.S. person?</legend>'
        '<label><input type="radio" name="cards[b][field0]" value="Yes">Yes</label>'
        '<label><input type="radio" name="cards[b][field0]" value="No">No</label></fieldset>'
        '<label>City <input></label>'
    )
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(
        fields, resolver=Stub({"Export control: are you a U.S. person?": "Yes"})
    )
    apply_autofill(browser_page, fields, plan, resume_path=None, assert_active=lambda: None)
    checked = browser_page.eval_on_selector_all(
        "input[type=radio]:checked", "els => els.map(e => e.name + '=' + e.value)"
    )
    assert checked == ["cards[b][field0]=Yes"]
    # A control with neither id nor name is found by its true position.
    from personal_os_worker.jobs.browser import fresh_control

    city = next(field for field in fields if field.get("label") == "City")
    assert fresh_control(browser_page, city).evaluate("e => e.closest('label').textContent") == (
        "City "
    )


JAZZHR = """
<div class="job-form-fields">
  <div class="form-group">
    <label for="first" class="control-label">First Name<i class="asterisk">*</i></label>
    <input type="text" id="first" name="resumator-firstname-value">
  </div>
  <div class="form-group">
    <label for="resumator-address-value" class="control-label">Address</label>
    <div class="resumator-input">
      <input type="text" id="resumator-address-value" placeholder="Address">
      <div class="row">
        <div class="col"><input type="text" id="resumator-city-value" placeholder="City"></div>
        <div class="col">
          <input type="text" id="resumator-state-value" placeholder="State/Province"></div>
        <div class="col"><input type="text" id="resumator-postal-value" placeholder="Postal"></div>
      </div>
    </div>
  </div>
  <textarea id="resumator-xml-value" style="display:none"></textarea>
  <div class="form-group">
    <label for="q1" class="control-label">Do you currently, or will you in the future require
      sponsorship to work in the US?<i class="asterisk">*</i></label>
    <input type="hidden" id="q1" name="resumator-questionnaire[1]">
    <input type="checkbox" id="q1-1" name="resumator-checkbox-1-1" value="Yes">
    <label for="q1-1">Yes</label>
    <input type="checkbox" id="q1-2" name="resumator-checkbox-1-2" value="No">
    <label for="q1-2">No</label>
  </div>
</div>
"""


def test_jazzhr_address_parts_yes_no_boxes_and_hidden_mirrors(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(JAZZHR)
    fields = inspect_fields(browser_page)
    labels = [field["label"] for field in fields]
    # Each address part keeps its own placeholder rather than the block's label,
    # and the hidden XML mirror is not a question.
    assert labels == ["First Name", "Address", "City", "State/Province", "Postal", "Yes", "No"]
    boxes = [field for field in fields if field["type"] == "checkbox"]
    assert all("require" in field["group"] and "sponsorship" in field["group"] for field in boxes)
    assert all(field["group_size"] == 2 and field["required"] for field in boxes)
    resolver = Resolver(Knowledge.build(
        facts=[{"key": "work_auth.requires_sponsorship", "value": False}],
        career=None,
        patterns=[{"id": "e1", "kind": "expression", "expression": r"\bsponsor",
                   "fact_key": "work_auth.requires_sponsorship",
                   "resolver_id": "expression:e1", "status": "active"}],
    ))
    plan = build_autofill_plan(fields, resolver=resolver)
    apply_autofill(browser_page, fields, plan, resume_path=None, assert_active=lambda: None)
    assert browser_page.is_checked("#q1-2") and not browser_page.is_checked("#q1-1")


# JazzHR keeps its resume input in a panel that opens only from "Attach resume";
# its own submit check reads the input, but nothing on screen shows the file.
JAZZHR_RESUME = """
<style>.none { display: none; }</style>
<form>
  <div id="resumator-resume" class="form-group">
    <label id="resumator-resume-label" for="resumator-resume-value"
      class="control-label">Resume<i class="asterisk">*</i></label>
    <div id="resumator-resume-options" class="resumator-resume-text">
      <a id="resumator-choose-upload" href="#">Attach resume</a> or
      <a id="resumator-choose-paste" href="#">Paste resume</a>
    </div>
    <div id="resumator-resume-upload-wrapper" class="none">
      <div class="resumator-input" id="resumator-resume-field">
        <input type="file" id="resumator-resume-value" name="resumator-resume-value">
      </div>
    </div>
    <div id="resumator-resume-paste-wrapper" class="none">
      <textarea id="resumator-resumetext-value"></textarea>
    </div>
  </div>
  <div class="form-group">
    <label for="first" class="control-label">First Name<i class="asterisk">*</i></label>
    <input type="text" id="first" name="resumator-firstname-value">
  </div>
</form>
<script>
for (const [link, panel] of [['#resumator-choose-upload', '#resumator-resume-upload-wrapper'],
                             ['#resumator-choose-paste', '#resumator-resume-paste-wrapper']]) {
  document.querySelector(link).addEventListener('click', event => {
    event.preventDefault();
    document.querySelector(panel).classList.remove('none');
    document.querySelector('#resumator-resume-options').style.display = 'none';
  });
}
</script>
"""


def test_a_file_in_a_panel_nobody_opened_is_not_uploaded(  # type: ignore[no-untyped-def]
    browser_page, tmp_path: Path
) -> None:
    browser_page.set_content(JAZZHR_RESUME)
    resume = tmp_path / "Alex_Example.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    browser_page.set_input_files("#resumator-resume-value", str(resume))

    fields = inspect_fields(browser_page)
    [upload] = [field for field in fields if field.get("type") == "file"]
    assert upload["filled"] is False
    assert "Resume" in audit_filled_form(fields)["required_unfilled"]


def test_a_resume_upload_opens_its_own_attach_panel_first(  # type: ignore[no-untyped-def]
    browser_page, tmp_path: Path
) -> None:
    browser_page.set_content(JAZZHR_RESUME)
    resume = tmp_path / "Alex_Example.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    fields = inspect_fields(browser_page)
    plan = build_autofill_plan(fields)

    result = apply_autofill(
        browser_page, fields, plan, resume_path=resume, assert_active=lambda: None
    )

    assert result["failures"] == []
    assert "Resume" in result["filled_fields"]
    assert browser_page.is_visible("#resumator-resume-value")
    assert browser_page.locator("#resumator-resume-value").evaluate(
        "element => element.files[0].name"
    ) == "Alex_Example.pdf"
    assert not browser_page.is_visible("#resumator-resumetext-value")  # paste stays shut
    [upload] = [field for field in inspect_fields(browser_page) if field.get("type") == "file"]
    assert upload["filled"] is True


def test_an_attach_button_that_opens_a_file_dialog_gets_the_file_without_one(  # type: ignore[no-untyped-def]
    browser_page, tmp_path: Path
) -> None:
    browser_page.set_content(
        """
        <div class="form-group">
          <label for="cv">Resume*</label>
          <button type="button" id="attach">Upload resume</button>
          <span id="shown"></span>
          <div style="display: none"><input type="file" id="cv"></div>
        </div>
        <script>
        document.querySelector('#attach').addEventListener('click',
          () => document.querySelector('#cv').click());
        document.querySelector('#cv').addEventListener('change',
          event => { document.querySelector('#shown').textContent = event.target.files[0].name; });
        </script>
        """
    )
    resume = tmp_path / "Alex_Example.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    fields = inspect_fields(browser_page)

    result = apply_autofill(
        browser_page, fields, build_autofill_plan(fields), resume_path=resume,
        assert_active=lambda: None,
    )

    assert result["failures"] == []
    assert browser_page.inner_text("#shown") == "Alex_Example.pdf"


def test_a_file_left_in_a_closed_panel_fails_its_readback(  # type: ignore[no-untyped-def]
    browser_page, tmp_path: Path
) -> None:
    browser_page.set_content(
        '<div class="form-group"><label for="cv">Resume*</label>'
        '<div style="display: none"><input type="file" id="cv"></div></div>'
        '<label for="first">First Name</label><input id="first">'
    )
    resume = tmp_path / "Alex_Example.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    fields = inspect_fields(browser_page)

    result = apply_autofill(
        browser_page, fields, build_autofill_plan(fields), resume_path=resume,
        assert_active=lambda: None,
    )

    assert [failure["category"] for failure in result["failures"]] == ["readback_mismatch"]
    assert "Resume" in result["unanswered_required_questions"]


def test_only_the_questions_left_open_are_outlined(browser_page) -> None:  # type: ignore[no-untyped-def]
    """JazzHR names every Yes/No box apart, so an answered question's Yes box is not empty."""
    from personal_os_worker.jobs.browser import highlight_unfilled

    follow_up = "If Yes, please provide the name of the team member who referred you."
    browser_page.set_content(
        JAZZHR_RESUME.replace(
            "</form>",
            """
            <div class="form-group">
              <label for="q1" class="control-label">Were you referred by a current
                employee?<i class="asterisk">*</i></label>
              <input type="checkbox" id="q1-1" name="resumator-checkbox-1-1" value="Yes">
              <label for="q1-1">Yes</label>
              <input type="checkbox" id="q1-2" name="resumator-checkbox-1-2" value="No" checked>
              <label for="q1-2">No</label>
            </div>
            <div class="form-group">
              <label for="q2" class="control-label">""" + follow_up + """</label>
              <input type="text" id="q2">
            </div>
            </form>""",
        )
    )
    fields = inspect_fields(browser_page)

    highlight_unfilled(browser_page, fields, {follow_up, "Resume", "First Name"})

    def outline(selector: str) -> str:
        return str(browser_page.locator(selector).evaluate("element => element.style.outline"))

    assert outline("#q1-1") == "" and outline("#q1-2") == ""
    assert "solid" in outline("#q2") and "solid" in outline("#first")
    # The resume input is in a closed panel: the field around it is what you can see.
    assert "solid" in outline("#resumator-resume")

    browser_page.fill("#q2", "N/A")
    highlight_unfilled(browser_page, inspect_fields(browser_page), {"Resume"})
    assert outline("#q2") == "" and outline("#first") == ""


def test_a_pair_of_identical_apply_buttons_is_one_action(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        """
        <h1>OLED Software Co-op</h1>
        <button class="top">Apply</button><p>About the role ...</p><button>Apply</button>
        <script>
        document.querySelectorAll('button').forEach(button =>
          button.addEventListener('click', () => {
          document.body.innerHTML = '<label>First Name<input name="first"></label>' +
            '<label>Email<input name="email" type="email"></label>' +
            '<label>Resume<input name="resume" type="file"></label>';
        }));
        </script>
        """
    )
    assert reveal_application_form(browser_page, timeout_ms=2_000) is True
    browser_page.set_content(
        "<button>Apply</button><button>Apply</button><button>Apply</button>"
    )
    assert reveal_application_form(browser_page, timeout_ms=500) is False


def test_a_phone_box_holding_only_its_dial_code_is_empty(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content(
        '<label for="m">Mobile Number</label><input id="m" type="tel" value="+1">'
        '<label for="h">Home phone</label><input id="h" type="tel" value="(555) 010-0199">'
    )
    filled = {field["label"]: field["filled"] for field in inspect_fields(browser_page)}
    assert filled == {"Mobile Number": False, "Home phone": True}


def test_an_apply_link_inside_an_iframe_reveals_the_form_there(browser_page) -> None:  # type: ignore[no-untyped-def]
    """iCIMS: the posting and its "Apply for this job online" link live in a frame."""
    inner = (
        "<a href='#' onclick=\"document.body.innerHTML = "
        "'<label>Email<input type=email name=email></label>"
        "<label>First Name<input name=first></label>"
        "<label>Resume<input type=file name=resume></label>'; return false\">"
        "Apply for this job online</a>"
    )
    from html import escape

    browser_page.set_content(
        f'<a href="#x">Skip Branding</a><iframe srcdoc="{escape(inner)}"></iframe>'
    )
    browser_page.wait_for_timeout(300)
    assert reveal_application_form(browser_page, timeout_ms=3_000) is True
    assert any(field.get("name") == "email" for field in inspect_fields(browser_page))


SHADOW_FORM = """
<oc-resume-upload></oc-resume-upload>
<oc-apply-with-resume></oc-apply-with-resume>
<city-picker></city-picker>
<script>
customElements.define('oc-resume-upload', class extends HTMLElement {
  connectedCallback() {
    this.attachShadow({mode: 'open'}).innerHTML =
      '<div class="dropzone"><input type="file" id="resume-drop">' +
      '<label for="resume-drop">Choose a file</label></div>';
  }
});
customElements.define('oc-apply-with-resume', class extends HTMLElement {
  connectedCallback() {
    this.attachShadow({mode: 'open'}).innerHTML =
      '<div class="dropzone"><input type="file" id="easy-drop">' +
      '<label for="easy-drop">Choose a file</label></div>';
  }
});
customElements.define('city-picker', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<label for="city">City</label>' +
      '<input id="city" role="combobox" aria-autocomplete="list" aria-expanded="false">' +
      '<ul role="listbox" id="cities"></ul>';
    const box = root.querySelector('#city');
    const list = root.querySelector('#cities');
    box.addEventListener('input', () => {
      list.innerHTML = box.value.toLowerCase().startsWith('atl')
        ? '<li role="option">Atlanta, TX, USA</li><li role="option">Atlanta, GA, USA</li>'
        : '';
      list.querySelectorAll('li').forEach(item => item.addEventListener('click', () => {
        box.value = item.textContent; list.innerHTML = '';
      }));
    });
  }
});
</script>
"""


def test_shadow_dom_resume_dropzone_and_typeahead(browser_page, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """SmartRecruiters renders its form in shadow roots."""
    from personal_os_worker.jobs.autofill import is_resume_field
    from personal_os_worker.jobs.registry.strategies.combobox import select_combobox_option

    browser_page.set_content(SHADOW_FORM)
    fields = inspect_fields(browser_page)
    files = [field for field in fields if field.get("type") == "file"]
    assert [is_resume_field(field) for field in files] == [True, False]
    city = next(field for field in fields if field.get("label") == "City")
    from personal_os_worker.jobs.browser import fresh_control

    chosen = select_combobox_option(
        browser_page, fresh_control(browser_page, city), "Atlanta, Georgia"
    )
    assert chosen == "Atlanta, GA, USA"


SECTION_BODY = """
  <div><label>Have you worked for Acme before?</label>
    <label><input type="radio" name="worked" value="yes">Yes</label>
    <label><input type="radio" name="worked" value="no">No</label></div>
  <div><label>Are you at least 18 years old?</label>
    <label><input type="radio" name="adult" value="yes">Yes</label>
    <label><input type="radio" name="adult" value="no">No</label></div>
"""


@pytest.mark.parametrize(
    "section",
    [
        "<fieldset><legend>Job specific questions</legend>{body}</fieldset>",
        '<div role="group" aria-label="Job specific questions">{body}</div>',
        '<div data-testid="questions"><h3>Job specific questions</h3>{body}</div>',
    ],
)
def test_a_section_heading_is_never_a_choice_groups_question(
    browser_page,  # type: ignore[no-untyped-def]
    section: str,
) -> None:
    browser_page.set_content(section.format(body=SECTION_BODY))
    fields = inspect_fields(browser_page)
    assert [(field["label"], field["group"]) for field in fields] == [
        ("Yes", "Have you worked for Acme before?"),
        ("No", "Have you worked for Acme before?"),
        ("Yes", "Are you at least 18 years old?"),
        ("No", "Are you at least 18 years old?"),
    ]


def test_a_fieldset_holding_one_question_keeps_its_legend(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        """
        <fieldset><legend><label id="q">Have you previously been employed by Axiom?</label>
        </legend>
          <div aria-labelledby="q">
            <div><input id="y" type="radio" name="prev" value="true">
              <label for="y">Yes</label></div>
            <div><input id="n" type="radio" name="prev" value="false">
              <label for="n">No</label></div>
          </div>
        </fieldset>
        """
    )
    assert {field["group"] for field in inspect_fields(browser_page)} == {
        "Have you previously been employed by Axiom?"
    }


@pytest.mark.parametrize(
    "markup",
    [
        # The question is plain text before its options.
        """<form><label for="e">Email</label><input id="e">
           <div><p>Do you require sponsorship?</p>
             <label><input type="radio" name="s" value="y">Yes</label>
             <label><input type="radio" name="s" value="n">No</label></div></form>""",
        # The group names its question by aria-labelledby.
        """<form><span id="sq">Do you require sponsorship?</span>
           <div role="radiogroup" aria-labelledby="sq">
             <label><input type="radio" name="s" value="y">Yes</label>
             <label><input type="radio" name="s" value="n">No</label></div></form>""",
    ],
)
def test_a_yes_no_radio_keeps_the_question_it_answers(
    browser_page,  # type: ignore[no-untyped-def]
    markup: str,
) -> None:
    browser_page.set_content(markup)
    radios = [field for field in inspect_fields(browser_page) if field["type"] == "radio"]
    assert [(field["label"], question_of(field)) for field in radios] == [
        ("Yes", "Do you require sponsorship?"),
        ("No", "Do you require sponsorship?"),
    ]
    plan = build_autofill_plan(
        inspect_fields(browser_page), resolver=Stub({"Do you require sponsorship?": "No"})
    )
    planned = {fill.index for fill in plan.fills}
    assert [field["label"] for field in radios if field["index"] in planned] == ["No"]
