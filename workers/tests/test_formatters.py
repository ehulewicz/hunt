"""Formatters: one fact rendered for each control kind it meets (design §8)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from personal_os_worker.jobs.registry.formats import Rendering, render
from personal_os_worker.jobs.registry.formats.choices import polarity, yes_no_option

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


def test_a_fractional_number_rounds_down_into_whole_only_options() -> None:
    whole = {"tag": "select", "options": ["1", "2", "3", "4", "5+"]}
    assert rendered("experience.years_professional", 3.5, "number", "Years", whole) == (
        "3", "number_rounded_down"
    )
    bands = {"tag": "select", "options": ["0-1", "1-2", "2-3", "3-5"]}
    assert rendered("experience.years_professional", 2.5, "number", "Years", bands) == (
        "2-3", "number_as_range_option"
    )
    assert rendered(
        "experience.years_professional", 3.5, "number", "Years",
        {"tag": "input", "type": "text"},
    ) == ("3.5", "number_as_text")


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
    from personal_os_worker.jobs.options import choose_option
    from personal_os_worker.jobs.registry.strategies.combobox import _pick

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
def test_a_none_fact_writes_na_or_remains_unrepresentable(value: Any, value_type: str) -> None:
    assert rendered("address.line_2", value, value_type, "Address Line 2") == ("N/A", "none_as_na")
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


def test_consent_takes_the_agreeing_option_and_a_typeahead_its_live_menu() -> None:
    menu = {"tag": "select", "options": ["Select...", "Yes, I have read and agree", "No"]}
    assert rendered("consent.agrees", True, "boolean", "Terms", menu) == (
        "Yes, I have read and agree", "affirmative_option"
    )
    assert rendered("consent.agrees", False, "boolean", "Terms", menu) is None
    # A typeahead nobody has opened: the planner chooses against its live menu.
    typeahead = {"tag": "input", "type": "text", "role": "combobox"}
    assert rendered("consent.agrees", True, "boolean", "Terms", typeahead) == (
        "Yes", "affirmative_option"
    )
    # A consent box you type into is never answered for you.
    assert rendered("consent.agrees", True, "boolean", "Type I agree") is None


def test_the_standing_source_ranks_the_menu_and_names_a_referral() -> None:
    menu = {"tag": "select", "options": ["LinkedIn", "Employee Referral", "Acme Website"]}
    assert rendered("preferences.application_source", "Company website", "text",
                    "How did you hear about us?", menu) == ("Acme Website", "application_source")
    referred = {"referral.referred": True}
    assert rendered("preferences.application_source", "Company website", "text",
                    "How did you hear about us?", menu, context=referred) == (
        "Employee Referral", "application_source"
    )
    assert rendered("preferences.application_source", "Company website", "text",
                    "How did you hear about us?") == ("Company website", "application_source")
    assert rendered("preferences.application_source", "Company website", "text",
                    "How did you hear about us?", context=referred) == (
        "Employee referral", "application_source"
    )


def test_a_shared_year_month_range_holds_its_date() -> None:
    from personal_os_worker.jobs.registry.formats.dates import choose_date_range

    options = [
        "Already graduated", "Jan - April 2027", "May - Aug 2027",
        "Sept - Dec 2027", "Jan - April 2028", "May - Aug 2028",
    ]
    assert choose_date_range(options, date(2027, 12, 1)) == "Sept - Dec 2027"
    assert choose_date_range(options, date(2027, 2, 15)) == "Jan - April 2027"
    for dash in ("Jan \u2013 April 2027", "Jan \u2014 April 2027", "Jan-April 2027"):
        assert choose_date_range([dash, "Other"], date(2027, 2, 15)) == dash


def test_a_november_or_december_date_also_reads_as_winter() -> None:
    options = {
        "tag": "select",
        "options": [
            "Winter 2027", "Spring/Summer 2028",
            "Flexible between Winter 2027 and Spring/Summer 2028", "Other",
        ],
    }
    assert rendered(
        "education.graduation_date", "2027-12-01", "date", "Graduation term", options
    ) == ("Winter 2027", "date_as_winter_term_option")


def test_a_yes_no_question_naming_a_date_range_is_checked_against_the_date() -> None:
    question = "Is your expected graduation date between June 2027 and June 2028?"
    options = {"tag": "select", "options": ["Yes", "No"]}
    assert rendered(
        "education.graduation_date", "2027-12-01", "date", question, options
    ) == ("Yes", "date_in_range_option")
    assert rendered(
        "education.graduation_date", "2029-01-15", "date", question, options
    ) == ("No", "date_in_range_option")


@pytest.mark.parametrize(
    ("options", "answer", "expected"),
    [
        (
            ["<2.74", "2.75—2.99", "3.00—3.24", "3.25—3.49", "3.50—3.74", "3.75—4.0"],
            "3.78",
            "3.75—4.0",
        ),
        (["3.75", "3.4 - 3.70", "Below 3.4"], "3.78", "3.75"),
        (["less than 25k", "26-30k", "81-85k", "86-90k"], "83200", "81-85k"),
        (["less than 25k", "26-30k", "81-85k", "86-90k"], "27500", "26-30k"),
        (["$80K - $90K", "100k+"], "85000", "$80K - $90K"),
        (["$80K - $90K", "100k+"], "120000", "100k+"),
        (["≤3.0", "3.1 - 3.5", "≥3.6"], "3.6", "≥3.6"),
        (["≤3.0", "3.1 - 3.5", "≥3.6"], "3.0", "≤3.0"),
    ],
)
def test_numeric_ranges_read_dashes_inequalities_and_thousands(
    options: list[str], answer: str, expected: str
) -> None:
    from personal_os_worker.jobs.options import choose_numeric_range

    assert choose_numeric_range(options, answer) == expected


def test_money_finds_its_currency_in_a_currency_menu() -> None:
    options = {
        "tag": "select",
        "label": "Desired Salary (currency)",
        "options": [
            "US Dollar ($)", "Canadian Dollar ($)", "Euro (€)",
            "United Arab Emirates Dirham (د.إ.\u200f)",
        ],
    }
    assert rendered(
        "compensation.desired", MINIMUM, "object", "Desired Salary (currency)", options
    ) == ("US Dollar ($)", "money_currency_option")


def test_money_finds_its_period_in_a_period_menu() -> None:
    options = {
        "tag": "select",
        "label": "Desired Salary (period)",
        "options": ["Hourly", "Weekly", "Monthly", "Yearly"],
    }
    assert rendered(
        "compensation.desired", MINIMUM, "object", "Desired Salary (period)", options
    ) == ("Hourly", "money_period_option")


def test_hourly_pay_lands_in_the_annual_band_its_magnitude_matches() -> None:
    bands = ["less than 25k"] + [f"{low}-{low + 4}k" for low in range(26, 90, 5)]
    options = {"tag": "select", "options": bands}
    # $40/hour is $83,200 a year: "81-85k", never "less than 25k".
    assert rendered(
        "compensation.desired", MINIMUM, "object", "Target Compensation", options
    ) == ("81-85k", "money_as_annual_option")


def test_a_state_name_finds_its_code_in_a_code_menu() -> None:
    options = {
        "tag": "select",
        "options": [
            "Abreviations only", "AL", "AK", "AZ", "AR", "CA", "CO", "CT",
            "DE", "DC", "FL", "GA", "HI", "ID", "IL",
        ],
    }
    assert rendered(
        "address.state", "Georgia", "text", "Current State", options
    ) == ("GA", "state_code_option")


def test_a_state_code_finds_its_name_in_a_menu_of_names() -> None:
    options = {
        "tag": "select",
        "options": ["Alabama", "Florida", "Georgia", "New York"],
    }
    assert rendered(
        "address.state", "GA", "text", "Current State", options
    ) == ("Georgia", "state_name_option")


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Are you a resident of California?", ("No", "named_state_option")),
        (
            "Are you currently a legal resident of Ohio? "
            "(This position is restricted to OH residents only.)",
            ("No", "named_state_option"),
        ),
        ("Are you a resident of Georgia?", ("Yes", "named_state_option")),
    ],
)
def test_a_question_naming_a_state_is_checked_against_yours(
    question: str, expected: tuple[str, str]
) -> None:
    options = {"tag": "select", "options": ["Yes", "No"]}
    assert rendered("address.state", "Georgia", "text", question, options) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Are you currently attending Northeastern University?", "No"),
        ("Are you currently attending Northestern University?", "No"),
        ("Are you a current or former student of Northeastern University?", "No"),
        ("Are you eligible for Northeastern University's Co-op Program?", "No"),
        ("Are you currently attending Example University?", "Yes"),
    ],
)
def test_a_question_naming_a_school_is_checked_against_yours(
    question: str, expected: str
) -> None:
    options = {"tag": "select", "options": ["Yes", "No"]}
    found = rendered(
        "education.school_name", "Example Institute of Technology", "text", question, options
    )
    assert found == (expected, "named_school_option")


def test_a_vague_university_question_is_no_comparison() -> None:
    options = {"tag": "select", "options": ["Yes", "No"]}
    assert rendered(
        "education.school_name", "Example Institute of Technology", "text",
        "Are you currently a University student in the Atlanta area "
        "pursuing an Engineering degree?",
        options,
    ) is None


def test_a_question_naming_your_country_is_yes() -> None:
    options = {"tag": "select", "options": ["Yes", "No", "Unsure"]}
    assert rendered(
        "address.country", "United States", "text",
        "Will you reside in the US during the full period of the internship "
        "(June 7, 2027 - August 13, 2027)?",
        options,
    ) == ("Yes", "named_country_option")


def test_a_citizenship_menu_for_another_country_takes_the_none_option() -> None:
    options = {
        "tag": "select",
        "options": [
            "Singaporean Citizen", "Singaporean Permanent Resident",
            "Personalized Employment Pass (PEP)", "Employment Pass",
            "Dependent Pass", "None",
        ],
    }
    assert rendered(
        "work_auth.citizenship_country", "United States", "text",
        "What is your citizenship status in Singapore?", options,
    ) == ("None", "named_country_option")


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "I'm a current student aiming to complete my degree in either "
            "Fall 2027 or Spring 2028.",
            "Yes",
        ),
        ("Are you returning to school in Fall 2027?", "Yes"),
        ("Are you returning to school in Spring 2028?", "No"),
    ],
)
def test_a_question_naming_terms_is_checked_against_the_graduation(
    question: str, expected: str
) -> None:
    options = {"tag": "select", "options": ["Yes", "No", "Unsure"]}
    found = rendered(
        "education.graduation_date", "2027-12-01", "date", question, options
    )
    assert found == (expected, "named_term_option")


def test_years_professional_floors_only_where_the_control_cannot_take_a_half() -> None:
    box = {"tag": "input", "type": "number"}
    key = "experience.years_professional"
    assert rendered(key, 3.5, "number", "Years", box) == ("3", "number_rounded_down")
    assert rendered(key, 3.5, "number", "Years", {**box, "step": "1"}) == (
        "3", "number_rounded_down"
    )
    assert rendered(key, 3.5, "number", "Years", {**box, "step": "0.5"}) == (
        "3.5", "number_as_text"
    )
    assert rendered(key, 3.5, "number", "Years", {**box, "step": "any"}) == (
        "3.5", "number_as_text"
    )
    # GPA keeps its value in a stepless number input, and free text keeps it too.
    assert rendered("education.gpa", 3.78, "number", "GPA", box) == (
        "3.78", "number_as_text"
    )
    assert rendered(key, 3.5, "number", "Years", {"tag": "input", "type": "text"}) == (
        "3.5", "number_as_text"
    )

