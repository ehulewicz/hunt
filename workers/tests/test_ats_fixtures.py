from pathlib import Path

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.autofill import build_autofill_plan
from personal_os_worker.browser import (
    apply_autofill,
    audit_filled_form,
    click_safe_transition,
    combobox_options,
    has_submit_control,
    inspect_fields,
    reveal_application_form,
    set_registered_value,
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
    plan = build_autofill_plan(fields, profile={}, approved_answers=[])
    assert plan.fills == ()
    assert plan.unanswered_required_questions == ()
    audit = audit_filled_form(fields)
    assert audit["required_unfilled"] == []
    assert audit["sensitive_filled"] == [question]
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
        profile={
            "school_name": "Example Institute of Technology",
            "graduation_date": "2027-12-15",
        },
        approved_answers=[],
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

    plan = build_autofill_plan(fields, profile={"country": "United Stat"}, approved_answers=[])
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


def test_places_city_typeahead_is_filled_by_the_location_rule(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(PLACES_CITY)
    fields = inspect_fields(browser_page)
    profile = {"city": "Atlanta", "region": "Georgia", "country": "United Stat"}
    plan = build_autofill_plan(
        fields,
        profile={},
        approved_answers=[],
        fallback=lambda asked: resolve_pattern_answer(
            asked, rules=[], profile=profile, resume={}, company="Acme"
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
    plan = build_autofill_plan(
        fields,
        profile={},
        approved_answers=[],
        fallback=lambda asked: resolve_pattern_answer(
            asked, rules=[], profile={"gpa": "3.78"}, resume={}, company="Acme"
        ),
    )
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
    replan = build_autofill_plan(
        refreshed, profile={}, approved_answers=[], fallback=lambda _: "4.0"
    )
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

    plan = build_autofill_plan(
        fields,
        profile={},
        approved_answers=[],
        fallback=lambda asked: resolve_pattern_answer(
            asked, rules=[], profile={"gpa": "3.78"}, resume={}, company="Acme"
        ),
    )
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
    plan = build_autofill_plan(fields, profile={}, approved_answers=[])
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
