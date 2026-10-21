"""Formatters: one fact rendered for each control kind it meets (design §8)."""

from __future__ import annotations

from typing import Any

import pytest

from personal_os_worker.jobs.formatters import Rendering, polarity, render, yes_no_option

GRADUATION = "2027-12-15"
MINIMUM = {"amount": 40, "currency": "USD", "period": "hour"}
TEXT = {"tag": "input", "type": "text"}


def rendered(
    key: str, value: Any, value_type: str, question: str, field: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[str, str] | None:
    result = render(key, value, value_type, field=field or TEXT, question=question, **kwargs)
    return (result.value, result.formatter) if result else None


@pytest.mark.parametrize(
    ("question", "field", "expected"),
    [
        ("What is your expected graduation date?", TEXT, ("December 2027", "date_as_month_year")),
        ("Expected graduation (Term & Year)", TEXT, ("Fall 2027", "date_as_term")),
        ("Graduation semester", TEXT, ("Fall 2027", "date_as_term")),
        ("Graduation month", TEXT, ("December", "date_as_month_name")),
        ("Graduation year", TEXT, ("2027", "date_as_year")),
        ("Graduation date", {"tag": "input", "type": "date"}, ("2027-12-15", "date_as_iso")),
        ("Graduation", {"tag": "input", "type": "month"}, ("2027-12", "date_as_month_input")),
        ("Graduation date", {"tag": "input", "type": "text", "placeholder": "MM/DD/YYYY"},
         ("12/15/2027", "date_as_us")),
    ],
)
def test_a_date_follows_the_question_and_the_input(
    question: str, field: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert rendered("education.graduation_date", GRADUATION, "date", question, field) == expected


def test_a_date_picks_whichever_rendering_an_option_offers() -> None:
    terms = {"tag": "select", "options": ["Spring 2027", "Fall 2027", "Spring 2028"]}
    assert rendered("education.graduation_date", GRADUATION, "date", "Graduation", terms) == (
        "Fall 2027", "date_as_term_option"
    )
    months = {"tag": "select", "options": ["May 2027", "December 2027"]}
    assert rendered("education.graduation_date", GRADUATION, "date", "Graduation", months) == (
        "December 2027", "date_as_month_year_option"
    )
    assert rendered(
        "education.graduation_date", GRADUATION, "date", "Graduation",
        {"tag": "select", "options": ["2025", "2026"]},
    ) is None


@pytest.mark.parametrize(
    ("question", "field", "expected"),
    [
        ("What are your salary requirements?", TEXT, ("$40/hour", "money_as_hourly")),
        ("Desired hourly pay", {"tag": "input", "type": "number"}, ("40", "money_as_hourly")),
        ("Annual base salary expectations", TEXT, ("$83,200 USD", "money_as_annual")),
        ("Expected salary per year", {"tag": "input", "type": "number"},
         ("83200", "money_as_annual")),
    ],
)
def test_minimum_pay_is_hourly_unless_the_question_asks_annual(
    question: str, field: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert rendered("compensation.minimum", MINIMUM, "object", question, field) == expected


def test_minimum_pay_picks_the_range_it_falls_in() -> None:
    hourly = {"tag": "select", "options": ["$20-$30", "$30-$45", "$45+"]}
    assert rendered("compensation.minimum", MINIMUM, "object", "Hourly pay?", hourly) == (
        "$30-$45", "money_as_hourly_option"
    )


@pytest.mark.parametrize(
    ("options", "value", "expected"),
    [
        (["Yes", "No"], True, "Yes"),
        (["Yes", "No"], False, "No"),
        (["I am not a protected veteran", "I identify as a protected veteran",
          "I don't wish to answer"], False, "I am not a protected veteran"),
        (["Yes, I have a disability", "No, I do not have a disability",
          "I do not want to answer"], "No", "No, I do not have a disability"),
        (["Yes - I am authorized", "Yes - with a visa", "No"], True, None),
    ],
)
def test_yes_and_no_find_their_option_in_any_wording(
    options: list[str], value: Any, expected: str | None
) -> None:
    field = {"tag": "select", "options": options}
    value_type = "boolean" if isinstance(value, bool) else "text"
    result = rendered("eeo.veteran_status", value, value_type, "Status?", field)
    assert (result[0] if result else None) == expected


def test_polarity_reads_yes_and_no_and_nothing_else() -> None:
    assert (polarity(True), polarity("Yes"), polarity("no"), polarity("Male")) == (
        True, True, False, None
    )
    assert yes_no_option(["Prefer not to say", "No"], False) == "No"


def test_numbers_render_plainly_and_find_their_range() -> None:
    assert rendered("education.gpa", 3.78, "number", "GPA") == ("3.78", "number_as_text")
    assert rendered("experience.years_professional", 2, "number", "Years") == (
        "2", "number_as_text"
    )
    ranges = {"tag": "select", "options": ["3.9 or above", "3.6-3.8", "Below 3.6"]}
    assert rendered("education.gpa", 3.78, "number", "GPA", ranges) == (
        "3.6-3.8", "number_as_range_option"
    )


def test_text_matches_synonyms_and_lists_join_or_check_each_box() -> None:
    gender = {"tag": "select", "options": ["Cisgender man", "Cisgender woman"]}
    assert rendered("eeo.gender_identity", "Male", "text", "Gender", gender) == (
        "Cisgender man", "text_as_is_option"
    )
    assert rendered("skills.all", ["Python", "Java"], "list", "Languages?") == (
        "Python, Java", "list_as_joined"
    )
    boxes = {"tag": "input", "type": "checkbox", "group_size": 3,
             "options": ["Python", "Go", "TypeScript"]}
    assert rendered("skills.all", ["Python", "Typescript", "Rust"], "list", "Stacks?", boxes) == (
        "Python\nTypeScript", "list_as_options"
    )
    race = {"tag": "select", "options": ["White (Not Hispanic or Latino)", "Asian"]}
    assert rendered("eeo.race_ethnicities", ["White"], "list", "Race", race) == (
        "White (Not Hispanic or Latino)", "list_as_single_option"
    )


def test_a_rendering_longer_than_the_control_allows_is_no_rendering() -> None:
    assert rendered("contact.email", "a" * 30, "text", "Email", {"tag": "input",
                    "max_length": 10}) is None


def test_the_citizenship_option_is_chosen_only_when_asked_for() -> None:
    field = {"tag": "select", "options": [
        "I am authorized due to my nationality", "I have a work permit", "I need sponsorship",
    ]}
    assert rendered(
        "work_auth.us_citizen", True, "boolean", "Authorization to work", field,
        prefer="citizenship_option",
    ) == ("I am authorized due to my nationality", "citizenship_option")


def test_a_citizenship_status_finds_its_option_however_it_is_spelled() -> None:
    from personal_os_worker.jobs.options import choose_synonym

    for options in (["US Citizen", "Lawful Permanent Resident", "None"],
                    ["U.S. Citizen or National", "Permanent Resident", "Other"],
                    ["United States Citizen", "Green Card Holder"]):
        assert choose_synonym(options, "US Citizen") == options[0]


def test_a_city_meets_its_namesakes_with_its_state() -> None:
    context = {"address.state": "Georgia"}
    menu = {"tag": "select", "options": ["Atlanta, TX, US", "Atlanta, GA, US", "Atlanta, IL, US"]}
    assert render("address.city", "Atlanta", "text", field=menu, question="City",
                  context=context) == Rendering("Atlanta, GA, US", "city_with_state_option")
    typeahead = {"tag": "input", "role": "combobox"}
    assert render("address.city", "Atlanta", "text", field=typeahead, question="City",
                  context=context) == Rendering("Atlanta, Georgia", "city_with_state")
    # A plain City box keeps the city alone.
    assert render("address.city", "Atlanta", "text", field=TEXT, question="City",
                  context=context) == Rendering("Atlanta", "text_as_is")


def test_a_date_picks_the_range_option_that_holds_it() -> None:
    menu = {"tag": "select", "options": [
        "Before December 2027", "Between December 2027 and September 2028", "After September 2028",
    ]}
    assert rendered("education.graduation_date", "2027-12-01", "date", "Graduation", menu) == (
        "Between December 2027 and September 2028", "date_as_range_option"
    )
    spans = {"tag": "select", "options": ["Dec 2026 - May 2027", "Jun 2027 - Dec 2027"]}
    assert rendered("education.graduation_date", "2027-12-01", "date", "Graduation", spans) == (
        "Jun 2027 - Dec 2027", "date_as_range_option"
    )


def test_no_is_a_whole_word_and_a_typeahead_reads_yes_and_no_like_a_select() -> None:
    from personal_os_worker.jobs.browser import _pick
    from personal_os_worker.jobs.options import choose_option

    assert choose_option(["Not Applicable", "Google employee"], "No") is None
    assert choose_option(["North Carolina", "No"], "No") == "No"
    assert choose_option(["Yes - previous intern", "No - never"], "Yes") == "Yes - previous intern"
    menu = ["Not Applicable", "Bet employee (Wing, X, Waymo, Other)", "Google employee"]
    assert _pick(menu, "No") == "Not Applicable"  # the only option saying no


def test_starting_within_weeks_follows_the_notice_period() -> None:
    yes_no = {"tag": "select", "options": ["Yes", "No"]}
    assert rendered("availability.notice_period_weeks", 2, "number",
                    "If offered a position, will you be able to start within 3 weeks?",
                    yes_no) == ("Yes", "notice_within_option")
    assert rendered("availability.notice_period_weeks", 2, "number",
                    "Can you start within 10 days?", TEXT) == ("No", "notice_within")


def test_orientation_and_pronouns_find_their_options() -> None:
    from personal_os_worker.jobs.options import choose_synonym

    assert choose_synonym(["Gay", "Heterosexual", "Bisexual"], "Straight") == "Heterosexual"
    assert choose_synonym(["She/Her", "He/Him/His", "They/Them"], "He/Him") == "He/Him/His"


def test_of_several_yes_options_the_open_ended_one_is_chosen() -> None:
    options = ["Yes- Somerville, MA only", "Yes- Devens, MA only",
               "Yes- Either Somerville or Devens works for me", "No- I cannot work"]
    assert yes_no_option(options, True) == "Yes- Either Somerville or Devens works for me"
    assert yes_no_option(options, False) == "No- I cannot work"
    assert yes_no_option(["Yes - Boston only", "Yes - Austin only", "No"], True) is None


@pytest.mark.parametrize(("value", "value_type"), [("", "text"), ([], "list")])
def test_a_none_fact_renders_as_a_deliberate_blank(value: Any, value_type: str) -> None:
    assert rendered("address.line_2", value, value_type, "Address Line 2") == ("", "none_as_blank")
    choices = {"tag": "select", "options": ["AWS", "GCP"]}
    assert rendered("education.certifications", value, value_type, "Certifications", choices) == (
        "", "none_as_blank"
    )


PHONE_COUNTRY = {"country": "United States", "calling_code": "+1"}


@pytest.mark.parametrize(
    "options",
    [
        ["+44", "+1", "+91"],
        ["United Kingdom (+44)", "United States (+1)", "India (+91)"],
        ["\U0001f1e8\U0001f1e6 (+1) Canada", "\U0001f1fa\U0001f1f8 (+1) United States of America",
         "\U0001f1fa\U0001f1f2 (+1) United States Minor Outlying Islands",
         "\U0001f1ec\U0001f1e7 (+44) United Kingdom"],
        ["United States+1", "United Kingdom+44"],
    ],
)
def test_a_menu_of_calling_codes_gets_the_phones_country(options: list[str]) -> None:
    wanted = next(
        option for option in options
        if option == "+1" or ("United States" in option and "Minor" not in option)
    )
    field = {"tag": "select", "options": options}
    assert rendered("contact.phone_country", PHONE_COUNTRY, "object", "Country code", field) == (
        wanted, "calling_code_option"
    )
    # The same menu labelled "Country" beside a phone box asks the country of
    # residence, which is the phone's country.
    assert rendered(
        "address.country", "United States", "text", "Country", field,
        context={"contact.phone_country": PHONE_COUNTRY},
    ) == (wanted, "calling_code_option")


def test_a_country_that_is_not_the_phones_is_matched_by_its_name() -> None:
    field = {"tag": "select", "options": ["United Kingdom (+44)", "United States (+1)"]}
    assert rendered(
        "address.country", "United Kingdom", "text", "Country", field,
        context={"contact.phone_country": PHONE_COUNTRY},
    ) == ("United Kingdom (+44)", "text_as_is_option")


def test_the_phones_country_by_name_or_code_as_the_box_asks() -> None:
    names = {"tag": "select", "options": ["Canada", "United States of America"]}
    assert rendered(
        "contact.phone_country", PHONE_COUNTRY, "object", "Phone number country", names
    ) == ("United States of America", "country_name_option")
    assert rendered("contact.phone_country", PHONE_COUNTRY, "object", "Country code") == (
        "+1", "calling_code"
    )
    assert rendered("contact.phone_country", PHONE_COUNTRY, "object", "Phone number country") == (
        "United States", "country_name"
    )
    # A typeahead is searched by name, since many countries share +1.
    typeahead = {"tag": "input", "type": "text", "role": "combobox"}
    assert rendered(
        "contact.phone_country", PHONE_COUNTRY, "object", "Country Phone Code", typeahead
    ) == ("United States", "country_name")
