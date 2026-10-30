from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.autofill import AnswerResolution, AutofillPlan, FormFill
from personal_os_worker.jobs.browser import apply_autofill, inspect_fields
from personal_os_worker.jobs.registry.events import (
    planned_events,
    unresolved_events,
    value_hash,
)
from personal_os_worker.jobs.registry.kinds import classify, control_attributes
from personal_os_worker.jobs.registry.strategies import (
    CANDIDATES,
    candidates,
    captcha_challenge_present,
)
from personal_os_worker.jobs.registry.strategies.buttons import button_choice

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("field", "kind"),
    [
        ({"tag": "input", "type": "text"}, "text_entry"),
        ({"tag": "textarea"}, "text_entry"),
        ({"tag": "select"}, "native_select"),
        ({"tag": "select", "multiple": True}, "multi_choice_group"),
        ({"tag": "input", "type": "radio", "group_size": 3}, "choice_group"),
        ({"tag": "input", "type": "yesno"}, "choice_group"),
        ({"tag": "div", "role": "radiogroup"}, "choice_group"),
        ({"tag": "input", "type": "checkbox", "group_size": 1}, "toggle"),
        ({"tag": "button", "role": "switch"}, "toggle"),
        ({"tag": "input", "type": "checkbox", "group_size": 4}, "multi_choice_group"),
        ({"tag": "input", "role": "combobox"}, "dynamic_choice"),
        ({"tag": "input", "type": "file"}, "file"),
    ],
)
def test_classification(field: dict[str, Any], kind: str) -> None:
    assert classify(field) == kind


def test_control_identity_ignores_the_question_the_name_and_the_ats() -> None:
    greenhouse = {
        "tag": "input", "type": "radio", "group_size": 2, "label": "Yes",
        "name": "question_123", "group": "Will you need sponsorship?", "ats": "greenhouse",
        "placeholder": None, "max_length": None,
    }
    lever = {
        "tag": "input", "type": "radio", "group_size": 5, "label": "Chicago",
        "name": "cards[abc][field0]", "group": "Preferred office", "ats": "lever",
    }
    assert control_attributes(greenhouse) == control_attributes(lever)
    assert control_attributes(greenhouse) != control_attributes(
        greenhouse | {"type": "checkbox", "group_size": 1}
    )


def test_candidate_order_mirrors_the_seeded_precedence() -> None:
    select = {"tag": "select"}
    fill = FormFill(index=0, kind="text", value="Bachelor of Science")
    assert candidates("native_select", select, fill, has_document=False) == [
        "select_by_label", "select_by_value",
    ]
    checkbox = {"tag": "input", "type": "checkbox"}
    assert candidates("toggle", checkbox, fill, has_document=False) == ["check_control"]
    clear = FormFill(index=0, kind="text", value=None, clear=True)
    assert candidates("toggle", checkbox, clear, has_document=False) == ["uncheck_control"]
    resume = FormFill(index=0, kind="resume", value=None)
    assert candidates("file", {"type": "file"}, resume, has_document=False) == []
    assert candidates("file", {"type": "file"}, resume, has_document=True) == ["upload_file"]
    assert set(CANDIDATES) == {
        "text_entry", "native_select", "choice_group", "multi_choice_group",
        "toggle", "dynamic_choice", "file",
    }


def test_button_choice_keeps_the_yes_no_reading() -> None:
    assert button_choice(["Yes", "No"], "Yes") == "Yes"
    assert button_choice(["Yes", "No"], "No, I do not") == "No"
    assert button_choice(["Remote", "Hybrid", "Onsite"], "Hybrid") == "Hybrid"
    assert button_choice(["Remote", "Hybrid"], "Onsite") is None


def test_unresolved_events_count_a_choice_group_once() -> None:
    fields = [
        {"index": 0, "tag": "input", "type": "text", "label": "Email"},
        {"index": 1, "tag": "input", "type": "radio", "label": "Day", "group": "Shift",
         "group_size": 2},
        {"index": 2, "tag": "input", "type": "radio", "label": "Night", "group": "Shift",
         "group_size": 2},
        {"index": 3, "tag": "input", "type": "text", "label": "Phone", "filled": True},
        {"index": 4, "tag": "input", "type": "submit", "label": "Submit"},
        {"index": 5, "tag": "textarea", "label": "Why us?"},
    ]
    plan = AutofillPlan((FormFill(index=0, kind="text", value="a@b.co"),), ())
    events = unresolved_events(fields, plan, skip=lambda field: field.get("tag") == "textarea")
    assert [(event["question"], event["kind"], event["stopped_at"]) for event in events] == [
        ("Shift", "choice_group", "pattern"),
    ]
    # The group's options are kept with the sighting, for replaying what was learned.
    assert events[0]["options"] == ["Day", "Night"]


def test_planned_events_describe_a_dry_run_without_attempting() -> None:
    fields = [
        {"index": 0, "tag": "input", "type": "text", "label": "Email"},
        {"index": 1, "tag": "input", "type": "text", "label": "Middle name"},
    ]
    resolution = AnswerResolution(
        "a@b.co", "archive", "archive:label:email", fact_key="contact.email",
        formatter="text_as_is",
    )
    plan = AutofillPlan((FormFill(index=0, kind="text", value="a@b.co", resolution=resolution),),
                        ())
    misses = {"middle name": SimpleNamespace(
        stage="format", fact_key="identity.middle_name", resolver_id="binding:x",
        formatter="text_as_is",
    )}
    events = planned_events(fields, plan, misses=misses)
    assert events[0] | {"attributes": None} == {
        "question": "Email", "kind": "text_entry", "attributes": None, "strategy": None,
        "resolver_id": "archive:label:email", "fact_key": "contact.email",
        "formatter": "text_as_is", "pattern_id": None, "source": "archive",
        "value_hash": value_hash("a@b.co"), "stopped_at": None, "operated_ok": None,
        "form": "short", "options": None,
    }
    # A miss the resolver explained keeps its stage and the fact it named.
    assert (events[1]["stopped_at"], events[1]["fact_key"]) == ("format", "identity.middle_name")
    assert value_hash(" a@b.co ") == value_hash("a@b.co")


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


def by_label(fields: list[dict[str, Any]], label: str) -> int:
    return next(int(field["index"]) for field in fields if field.get("label") == label)


def test_every_control_kind_is_operated_and_measured(browser_page, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "controls.html").read_text())
    fields = inspect_fields(browser_page)
    assert {classify(field) for field in fields} == set(CANDIDATES)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 synthetic")
    fills = (
        FormFill(index=by_label(fields, "Full name"), kind="text", value="Ada Lovelace"),
        FormFill(index=by_label(fields, "Anything else?"), kind="text", value="Thanks."),
        FormFill(index=by_label(fields, "Degree"), kind="text", value="Bachelor of Science"),
        FormFill(index=by_label(fields, "Languages"), kind="text", value="Python, Rust"),
        FormFill(index=by_label(fields, "Night"), kind="text", value="Night"),
        FormFill(index=by_label(fields, "Do you enjoy puzzles?"), kind="text", value="Yes"),
        FormFill(index=by_label(fields, "Send me the newsletter"), kind="text", value=None,
                 clear=True),
        FormFill(index=by_label(fields, "Systems"), kind="text", value="Systems"),
        FormFill(index=by_label(fields, "Resume"), kind="resume", value=None),
        FormFill(index=by_label(fields, "City"), kind="text", value="Chicago"),
    )
    result = apply_autofill(
        browser_page, fields, AutofillPlan(fills, ()), resume_path=resume,
        assert_active=lambda: None,
    )
    assert result["failures"] == []
    assert browser_page.input_value("#name") == "Ada Lovelace"
    assert browser_page.input_value("#why") == "Thanks."
    assert browser_page.input_value("#degree") == "bs"
    assert browser_page.eval_on_selector(
        "#langs", "e => [...e.selectedOptions].map(o => o.value)"
    ) == ["py", "rs"]
    assert browser_page.locator("input[name=shift]:checked").get_attribute("value") == "night"
    assert browser_page.locator(".choices button", has_text="Yes").get_attribute(
        "aria-pressed"
    ) == "true"
    assert browser_page.is_checked("#newsletter") is False
    assert browser_page.locator("input[name=interests]:checked").get_attribute("value") == "sys"
    assert browser_page.input_value("#city") == "Chicago, IL"

    outcomes = {
        (event["kind"], event["strategy"]): event["operated_ok"] for event in result["events"]
    }
    assert outcomes == {
        ("text_entry", "set_value"): True,
        ("native_select", "select_by_label"): True,
        ("multi_choice_group", "select_multiple"): True,
        ("choice_group", "check_control"): True,
        ("choice_group", "click_option_button"): True,
        ("toggle", "uncheck_control"): True,
        ("multi_choice_group", "check_control"): True,
        ("file", "upload_file"): True,
        ("dynamic_choice", "combobox_type_and_select"): True,
    }
    assert all(event["stopped_at"] is None for event in result["events"])
    # Each attempt keeps what its control offered; a text box offered nothing.
    offered = {event["kind"]: event["options"] for event in result["events"]}
    assert offered["text_entry"] is None
    assert offered["native_select"] and "Bachelor of Science" in offered["native_select"]
    assert offered["choice_group"] is not None


def test_a_value_no_option_fits_stops_at_format_then_falls_back(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content((FIXTURES / "controls.html").read_text())
    fields = inspect_fields(browser_page)
    plan = AutofillPlan((FormFill(index=by_label(fields, "Degree"), kind="text", value="ms"),), ())
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert browser_page.input_value("#degree") == "ms"
    assert [(e["strategy"], e["stopped_at"], e["operated_ok"]) for e in result["events"]] == [
        ("select_by_label", "format", False),
        ("select_by_value", None, True),
    ]


def test_only_an_open_captcha_challenge_parks_choices(  # type: ignore[no-untyped-def]
    browser_page,
) -> None:
    form = """
      <fieldset><legend>Shift</legend>
        <label><input type="radio" name="shift" value="day">Day</label>
        <label><input type="radio" name="shift" value="night">Night</label>
      </fieldset>
    """
    browser_page.set_content(
        f"<form>{form}</form>"
        '<div class="g-recaptcha" data-sitekey="s" data-size="invisible"></div>'
        '<div class="grecaptcha-badge"><iframe src="https://www.google.com/recaptcha/api2/'
        'anchor?k=s&size=invisible"></iframe></div>'
    )
    assert captcha_challenge_present(browser_page) is False
    fields = inspect_fields(browser_page)
    plan = AutofillPlan((FormFill(index=by_label(fields, "Night"), kind="text", value="Night"),),
                        ())
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert result["failures"] == []

    # Lever's invisible hCaptcha: a zero-height box whose frames are hidden.
    browser_page.set_content(
        f'<form>{form}</form><div class="h-captcha" data-sitekey="s"></div>'
        '<div style="visibility: hidden"><iframe width="300" height="200"'
        ' src="https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha-enclave.html'
        '#frame=enclave"></iframe></div>'
    )
    assert captcha_challenge_present(browser_page) is False

    # A checkbox widget waiting for you at Submit does not park anything.
    browser_page.set_content(
        f'<form>{form}</form><div class="g-recaptcha" data-sitekey="s">'
        '<iframe width="304" height="78" title="reCAPTCHA"'
        ' src="https://www.google.com/recaptcha/api2/anchor?k=s"></iframe></div>'
    )
    assert captcha_challenge_present(browser_page) is False

    # An open challenge (the image puzzle) parks the choice.
    browser_page.set_content(
        f'<form>{form}</form><div class="h-captcha" data-sitekey="s">'
        '<iframe width="400" height="580" src="https://newassets.hcaptcha.com/captcha/v1/x/'
        'static/hcaptcha.html#frame=challenge"></iframe></div>'
    )
    assert captcha_challenge_present(browser_page) is True
    fields = inspect_fields(browser_page)
    plan = AutofillPlan((FormFill(index=by_label(fields, "Night"), kind="text", value="Night"),),
                        ())
    result = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert browser_page.locator("input[name=shift]:checked").count() == 0
    assert result["failures"][0]["category"] == "captcha_guard"
    assert result["events"][0]["stopped_at"] == "strategy"
