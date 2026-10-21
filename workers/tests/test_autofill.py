from typing import Any

from standing_rules import Then, standing

from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    build_autofill_plan,
    follow_up_condition,
    is_long_form_field,
)
from personal_os_worker.jobs.browser import audit_filled_form
from personal_os_worker.jobs.formatters import affirmative_option, source_option
from personal_os_worker.jobs.options import choose_option


class Stub:
    """Answers a field by its question label, as an active pattern would."""

    def __init__(self, answers: dict[str, str] | None = None, default: str | None = None) -> None:
        self.answers = answers or {}
        self.default = default

    def resolve(self, field: dict[str, Any]) -> AnswerResolution | None:
        value = self.answers.get(str(field.get("label") or ""), self.default)
        return AnswerResolution(value, "pattern", "binding:test") if value is not None else None


def test_autofill_affirms_consent_and_leaves_non_exact_answers_empty() -> None:
    fields = [
        {
            "index": 0,
            "tag": "select",
            "type": None,
            "name": "gender",
            "label": "Gender",
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "checkbox",
            "name": "terms",
            "label": "I certify this is true",
            "required": True,
        },
        {
            "index": 2,
            "tag": "textarea",
            "type": None,
            "name": "why",
            "label": "Why do you want this role?",
            "required": True,
        },
        {
            "index": 3,
            "tag": "input",
            "type": "submit",
            "name": "submit",
            "label": "Submit",
            "required": False,
        },
    ]

    plan = build_autofill_plan(
        fields, resolver=Then(Stub({"Why do you want this company?": "No"}))
    )

    # A lone consent checkbox is checked; nothing else here can be answered.
    assert [(fill.index, fill.value) for fill in plan.fills] == [(1, "I certify this is true")]
    assert plan.unanswered_required_questions == ("Gender", "Why do you want this role?")


def test_an_acknowledgment_box_is_consent() -> None:
    # Microsoft names its required attestation by the noun alone.
    fields = [
        {"index": 0, "tag": "input", "type": "checkbox", "label": "Acknowledgment",
         "required": True},
        {"index": 1, "tag": "input", "type": "checkbox", "label": "Acknowledgement",
         "required": True},
    ]

    plan = build_autofill_plan(fields, resolver=standing())

    assert [(fill.index, fill.value) for fill in plan.fills] == [
        (0, "Acknowledgment"), (1, "Acknowledgement")
    ]


def test_consent_controls_take_the_affirmative_option_and_signatures_stay_manual() -> None:
    assert affirmative_option(["Select...", "Yes, I have read and agree", "No"]) == (
        "Yes, I have read and agree"
    )
    assert affirmative_option(["I agree", "I do not agree"]) == "I agree"
    assert affirmative_option(["Accept", "Decline"]) == "Accept"
    assert affirmative_option(["Option A", "Option B"]) is None
    fields = [
        {
            "index": 0,
            "tag": "select",
            "type": None,
            "label": "Terms and Conditions",
            "options": ["Select...", "Yes, I have read and agree to the privacy policy"],
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "text",
            "role": "combobox",
            "label": "Privacy consent",
            "required": True,
        },
        {
            "index": 2,
            "tag": "input",
            "type": "radio",
            "label": "Yes - I consent",
            "group": "Do you consent to text updates?",
            "required": True,
        },
        {
            "index": 3,
            "tag": "input",
            "type": "radio",
            "label": "No - I do not consent",
            "group": "Do you consent to text updates?",
            "required": True,
        },
        {
            "index": 4,
            "tag": "input",
            "type": "text",
            "label": "Electronic signature",
            "required": True,
        },
    ]
    plan = build_autofill_plan(fields, resolver=standing())
    assert [(fill.index, fill.value, fill.chooser) for fill in plan.fills] == [
        (0, "Yes, I have read and agree to the privacy policy", None),
        (1, None, "consent"),
        (2, "Yes - I consent", None),
    ]
    assert plan.unanswered_required_questions == ("Electronic signature",)


def test_autofill_does_not_guess_between_multiple_resume_uploads() -> None:
    fields = [
        {
            "index": 0,
            "tag": "input",
            "type": "file",
            "name": "resume_primary",
            "label": "Resume",
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "file",
            "name": "resume_secondary",
            "label": "Resume attachment",
            "required": True,
        },
    ]

    plan = build_autofill_plan(fields)

    assert plan.fills == ()
    assert plan.unanswered_required_questions == ("Resume", "Resume attachment")


def test_autofill_blocks_consent_and_signature_but_reuses_demographic_answers() -> None:
    fields = [
        {
            "index": 0,
            "tag": "textarea",
            "type": None,
            "name": "pronouns",
            "label": "Pronouns",
            "group": "Identity",
            "required": True,
        },
        {
            "index": 1,
            "tag": "select",
            "type": None,
            "name": "latino",
            "label": "Hispanic or Latino",
            "group": "Demographics",
            "required": True,
        },
        {
            "index": 2,
            "tag": "input",
            "type": "text",
            "name": "signature",
            "label": "Your name",
            "group": "Electronic signature",
            "required": True,
        },
        {
            "index": 3,
            "tag": "textarea",
            "type": None,
            "name": "motivation",
            "label": "Why this role?",
            "group": "Application questions",
            "required": True,
        },
    ]
    plan = build_autofill_plan(fields, resolver=Stub(default="approved value"))

    assert [(item.index, item.value) for item in plan.fills] == [
        (0, "approved value"),
        (1, "approved value"),
        (3, "approved value"),
    ]
    assert plan.unanswered_required_questions == ("Your name",)


def test_autofill_checks_the_radio_whose_option_matches_the_saved_group_answer() -> None:
    fields = [
        {
            "index": 0,
            "tag": "input",
            "type": "radio",
            "name": "source",
            "label": "LinkedIn",
            "value": "linkedin",
            "group": "How did you hear about us?",
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "radio",
            "name": "source",
            "label": "Referral",
            "value": "referral",
            "group": "How did you hear about us?",
            "required": True,
        },
    ]
    plan = build_autofill_plan(fields, resolver=Stub({"How did you hear about us?": "LinkedIn"}))

    assert [(item.index, item.value) for item in plan.fills] == [(0, "LinkedIn")]
    assert plan.unanswered_required_questions == ()


def test_how_heard_uses_the_first_available_standing_source_preference() -> None:
    question = "How did you hear about us?"

    def chosen(options: list[str]) -> str | None:
        fields = [
            {
                "index": index,
                "tag": "input",
                "type": "checkbox",
                "label": option,
                "group": question,
                "required": True,
            }
            for index, option in enumerate(options)
        ]
        plan = build_autofill_plan(fields, resolver=standing())
        return plan.fills[0].value if plan.fills else None

    assert chosen(["LinkedIn", "Other", "Other Job Boards", "Company Website"]) == (
        "Company Website"
    )
    assert chosen(["LinkedIn", "Other", "Online Job Board"]) == "Online Job Board"
    assert chosen(["LinkedIn", "Other (please specify)"]) == "Other (please specify)"
    # Nothing ranks: the standing source fits no option, so it is left for you.
    assert chosen(["LinkedIn", "Referral"]) is None
    # The employer's own site under its own name still counts as the company website.
    assert chosen(["LinkedIn", "Handshake", "DV Website", "Other"]) == "DV Website"
    assert chosen(["LinkedIn", "Careers page", "Other"]) == "Careers page"
    # The site named by the company, as Nintendo's menu offers it.
    assert chosen(
        ["Handshake", "LinkedIn", "Nintendo Careers", "University Job Board", "Other"]
    ) == "Nintendo Careers"
    # A school's careers site is not the employer's.
    assert chosen(["University Careers", "Other"]) == "Other"
    assert chosen(["LinkedIn", "Job site", "Other"]) == "Other"
    # A career fair or a campus career office is an event or a place, not the site.
    assert chosen(["Career Fair", "Career Office", "Point72 Website", "Other"]) == (
        "Point72 Website"
    )
    assert chosen(["Career Fair", "Non-campus Job Board", "Other"]) == "Non-campus Job Board"


def test_a_referred_application_names_the_referral_as_its_source() -> None:
    options = ["Company Website", "Employee Referral", "LinkedIn", "Other"]
    assert source_option(options) == "Company Website"
    assert source_option(options, referred=True) == "Employee Referral"
    assert source_option(["Not referred", "Website"], referred=True) == "Website"
    fields = [{"index": 0, "tag": "select", "label": "How did you hear about us?",
               "options": options}]
    plan = build_autofill_plan(fields, resolver=standing(referral={"referred": True}))
    assert [fill.value for fill in plan.fills] == ["Employee Referral"]


def test_how_heard_typeahead_without_visible_options_is_chosen_from_the_live_menu() -> None:
    fields = [
        {
            "index": 0,
            "tag": "input",
            "type": "text",
            "role": "combobox",
            "label": "How did you hear about DV Trading?",
            "required": True,
        }
    ]
    plan = build_autofill_plan(fields, resolver=standing())
    assert [(fill.index, fill.value, fill.chooser) for fill in plan.fills] == [(0, None, "source")]
    # A pattern you accepted for the question wins over the standing preference.
    plan = build_autofill_plan(
        fields, resolver=Then(Stub({"How did you hear about DV Trading?": "Referral"}))
    )
    assert [(f.index, f.value, f.chooser) for f in plan.fills] == [(0, "Referral", None)]


def test_autofill_does_not_upload_resume_inside_sensitive_group() -> None:
    field = {
        "index": 0,
        "tag": "input",
        "type": "file",
        "name": "signature_attachment",
        "label": "Resume",
        "group": "Electronic signature and consent",
        "required": True,
    }

    plan = build_autofill_plan([field])

    assert plan.fills == ()
    assert plan.unanswered_required_questions == ("Resume",)


def test_extension_audit_reports_presence_without_copying_values() -> None:
    audit = audit_filled_form(
        [
            {
                "index": 0,
                "type": "text",
                "label": "Email",
                "required": True,
                "filled": True,
            },
            {
                "index": 1,
                "type": "text",
                "label": "Portfolio",
                "required": True,
                "filled": False,
            },
            {
                "index": 2,
                "type": "radio",
                "label": "Veteran status",
                "required": False,
                "filled": True,
            },
            {
                "index": 3,
                "type": "submit",
                "label": "Submit application",
                "required": False,
                "filled": True,
            },
        ]
    )

    assert audit == {
        "filled_fields": ["Email", "Veteran status"],
        "required_unfilled": ["Portfolio"],
        "optional_unfilled": [],
    }


def test_audit_reports_a_choice_group_once_by_its_question() -> None:
    audit = audit_filled_form(
        [
            {
                "index": 0,
                "type": "radio",
                "label": "Yes",
                "group": "Authorized?",
                "required": True,
                "filled": False,
            },
            {
                "index": 1,
                "type": "radio",
                "label": "No",
                "group": "Authorized?",
                "required": True,
                "filled": True,
            },
            {
                "index": 2,
                "type": "radio",
                "label": "Yes",
                "group": "Worked here before?",
                "required": True,
                "filled": False,
            },
            {
                "index": 3,
                "type": "radio",
                "label": "No",
                "group": "Worked here before?",
                "required": True,
                "filled": False,
            },
            {
                "index": 4,
                "type": "checkbox",
                "label": "Yes",
                "group": "I certify",
                "required": True,
                "filled": False,
            },
        ]
    )
    assert audit["filled_fields"] == ["Authorized?"]
    assert audit["required_unfilled"] == ["Worked here before?", "I certify"]


def test_fallback_answers_fill_text_and_choice_groups_by_prefix() -> None:
    fields = [
        {"index": 0, "tag": "input", "type": "text", "label": "Desired salary", "required": True},
        {
            "index": 1,
            "tag": "input",
            "type": "radio",
            "label": "Yes - I am a previous intern",
            "group": "Have you ever worked here?",
            "required": True,
        },
        {
            "index": 2,
            "tag": "input",
            "type": "radio",
            "label": "Yes - I am a previous contractor",
            "group": "Have you ever worked here?",
            "required": True,
        },
        {
            "index": 3,
            "tag": "input",
            "type": "radio",
            "label": "No - I have never worked here",
            "group": "Have you ever worked here?",
            "required": True,
        },
        {
            "index": 4,
            "tag": "input",
            "type": "radio",
            "label": "Yes",
            "group": "Authorized?",
            "required": True,
        },
        {"index": 5, "tag": "input", "type": "radio", "label": "No", "group": "Authorized?"},
    ]
    answers = {
        "Desired salary": "Open",
        "Have you ever worked here?": "No",
        "Authorized?": "Yes - I have US work authorization as a Citizen",
    }
    plan = build_autofill_plan(fields, resolver=Stub(answers))
    assert [(fill.index, fill.value) for fill in plan.fills] == [
        (0, "Open"),
        (3, "No"),
        (4, "Yes - I have US work authorization as a Citizen"),
    ]
    assert plan.unanswered_required_questions == ()


def test_fallback_yes_is_ambiguous_against_several_yes_options() -> None:
    fields = [
        {
            "index": 0,
            "tag": "input",
            "type": "radio",
            "label": "Yes - citizen",
            "group": "Authorized?",
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "radio",
            "label": "Yes - visa",
            "group": "Authorized?",
            "required": True,
        },
        {
            "index": 2,
            "tag": "input",
            "type": "radio",
            "label": "No",
            "group": "Authorized?",
            "required": True,
        },
    ]
    plan = build_autofill_plan(fields, resolver=Stub(default="Yes"))
    assert plan.fills == ()
    assert plan.unanswered_required_questions == ("Authorized?",)


def test_filled_controls_and_answered_groups_are_never_planned_again() -> None:
    fields = [
        {"index": 0, "tag": "input", "type": "text", "label": "First name", "filled": True},
        {"index": 1, "tag": "input", "type": "text", "label": "Email", "filled": False},
        {"index": 2, "tag": "input", "type": "file", "label": "Resume", "filled": True},
        {
            "index": 3,
            "tag": "input",
            "type": "radio",
            "label": "Yes",
            "group": "Authorized?",
            "required": True,
            "filled": False,
        },
        {
            "index": 4,
            "tag": "input",
            "type": "radio",
            "label": "No",
            "group": "Authorized?",
            "required": True,
            "filled": True,
        },
        {
            "index": 5,
            "tag": "input",
            "type": "text",
            "role": "combobox",
            "label": "Location (City)",
            "required": True,
            "filled": True,
        },
    ]
    plan = build_autofill_plan(
        fields, resolver=Stub({"Email": "ethan@example.com"}, default="Yes")
    )
    assert [(fill.index, fill.kind, fill.value) for fill in plan.fills] == [
        (1, "text", "ethan@example.com")
    ]
    assert plan.unanswered_required_questions == ()


def test_a_number_picks_the_single_range_that_contains_it() -> None:
    gpa = ["3.9 or above", "3.6-3.8", "Below 3.6"]
    assert choose_option(gpa, "3.78") == "3.6-3.8"
    assert choose_option(gpa, "3.95") == "3.9 or above"
    assert choose_option(gpa, "2.0") == "Below 3.6"
    assert choose_option(gpa, "3.85") is None  # falls between ranges
    years = ["0-1 years", "2-4 years", "5+ years"]
    assert choose_option(years, "3") == "2-4 years"
    assert choose_option(years, "7 years") == "5+ years"
    assert choose_option(["Yes", "No"], "3.78") is None


def test_gender_answer_matches_the_forms_wording() -> None:
    assert choose_option(["Man", "Woman", "Non-binary", "Prefer not to say"], "Male") == "Man"
    assert choose_option(["Cisgender man", "Cisgender woman", "Transgender man"], "Male") == (
        "Cisgender man"
    )
    assert choose_option(["Man (he/him)", "Woman (she/her)", "Non-binary (they/them)"], "Male") == (
        "Man (he/him)"
    )
    assert choose_option(["Male", "Female", "Decline To Self Identify"], "Male") == "Male"
    assert choose_option(["Man", "Male"], "Male") == "Male"  # exact wins over the synonym
    assert choose_option(["Transgender man", "Woman"], "Male") is None
    assert choose_option(["Man", "Woman"], "Prefer not to say") is None


def test_non_answer_choice_is_left_for_the_user_even_when_saved() -> None:
    fields = [
        {
            "index": 0,
            "tag": "select",
            "type": "select-one",
            "label": "Disability status",
            "required": True,
            "options": ["Yes", "No", "I do not wish to answer"],
        }
    ]
    plan = build_autofill_plan(fields, resolver=Stub(default="I do not wish to answer"))
    assert plan.fills == ()
    assert plan.unanswered_required_questions == ("Disability status",)


def test_a_comma_joined_answer_checks_every_matching_box_in_a_checkbox_group() -> None:
    group = "I have experience with the following technology stacks:"
    fields = [
        {"index": i, "tag": "input", "type": "checkbox", "label": label, "group": group}
        for i, label in enumerate(["Python", "Go", "Typescript", "React", "Google Cloud"])
    ]
    plan = build_autofill_plan(
        fields, resolver=Stub(default="Python, Java, TypeScript, React.js, Docker")
    )
    assert [fill.index for fill in plan.fills] == [0, 2, 3]  # Go is not a prefix match for Google
    assert plan.unanswered_required_questions == ()


def test_greenhouse_short_answer_input_asking_for_sentences_is_long_form() -> None:
    """Greenhouse renders an essay prompt as input[type=text], not a textarea."""
    field = {
        "tag": "input",
        "type": "text",
        "label": (
            "Please share 3-5 sentences explaining your interest in the "
            "Blockchain/Web3 industry."
        ),
        "max_length": 255,
        "required": True,
    }
    assert is_long_form_field(field) is True


def test_ordinary_short_inputs_are_not_long_form() -> None:
    for label in ("First Name", "LinkedIn Profile", "Website", "How did you hear about this job?"):
        field = {"tag": "input", "type": "text", "label": label, "max_length": 255}
        assert is_long_form_field(field) is False, label


def test_a_textarea_is_still_long_form_and_a_consent_box_never_is() -> None:
    assert is_long_form_field({"tag": "textarea", "type": None, "label": "Why us?"}) is True
    assert is_long_form_field(
        {"tag": "input", "type": "checkbox", "label": "Tell us why you agree to our terms"}
    ) is False


def test_options_chosen_by_the_formatters_check_each_named_box() -> None:
    group = "Which offices would you consider?"
    fields = [
        {"index": i, "tag": "input", "type": "checkbox", "label": label, "group": group}
        for i, label in enumerate(["New York, NY", "Austin, TX", "Chicago, IL"])
    ]
    plan = build_autofill_plan(fields, resolver=Stub(default="New York, NY\nChicago, IL"))
    assert [fill.index for fill in plan.fills] == [0, 2]


def test_the_audit_lists_optional_fields_left_empty_after_required_ones() -> None:
    audit = audit_filled_form(
        [
            {"index": 0, "type": "text", "label": "Pronouns", "filled": False},
            {"index": 1, "type": "text", "label": "Email", "required": True, "filled": False},
            {"index": 2, "type": "file", "label": "Other documents", "filled": False},
            {"index": 3, "type": "checkbox", "label": "I agree", "group": "Terms", "filled": False},
            {"index": 4, "type": "radio", "label": "Yes", "group": "Relocate?", "filled": False},
            {"index": 5, "type": "radio", "label": "No", "group": "Relocate?", "filled": False},
        ]
    )
    assert audit["required_unfilled"] == ["Email"]
    # Files are uploaded by the engine or left to you; consent is the engine's.
    assert audit["optional_unfilled"] == ["Pronouns", "Relocate?"]



def test_two_questions_worded_alike_are_two_radio_groups() -> None:
    """Lever repeats a card: same question, different names, Yes/No in different cases."""
    question = "Will you be able to start within 3 weeks?"
    fields = [
        {"index": 0, "tag": "input", "type": "radio", "name": "a", "label": "yes",
         "group": question, "required": True},
        {"index": 1, "tag": "input", "type": "radio", "name": "a", "label": "no",
         "group": question, "required": True},
        {"index": 2, "tag": "input", "type": "radio", "name": "b", "label": "Yes",
         "group": question, "required": True},
        {"index": 3, "tag": "input", "type": "radio", "name": "b", "label": "No",
         "group": question, "required": True},
    ]
    plan = build_autofill_plan(fields, resolver=Stub({question: "Yes"}))
    assert sorted(fill.index for fill in plan.fills) == [0, 2]
    assert plan.unanswered_required_questions == ()



def test_how_heard_as_free_text_gets_the_standing_source() -> None:
    fields = [{"index": 0, "tag": "input", "type": "text",
               "label": "How did you hear about this job?", "required": False}]
    plan = build_autofill_plan(fields, resolver=standing())
    [fill] = plan.fills
    assert fill.value == "Company website"
    assert fill.resolution is not None
    assert (fill.resolution.resolver_id, fill.resolution.fact_key) == (
        "expression:standing-source", "preferences.application_source"
    )
    # Referred, the same box names the referral.
    plan = build_autofill_plan(fields, resolver=standing(referral={"referred": True}))
    assert [fill.value for fill in plan.fills] == ["Employee referral"]


def test_a_follow_up_the_answer_above_rules_out_is_not_applicable() -> None:
    question = "Are you a U.S. person under export control laws?"
    follow_up = "If you answered “No” to the above, please provide your citizenship."
    fields = [
        {"index": 0, "tag": "input", "type": "radio", "name": "q", "label": "Yes",
         "group": question, "required": True},
        {"index": 1, "tag": "input", "type": "radio", "name": "q", "label": "No",
         "group": question, "required": True},
        {"index": 2, "tag": "input", "type": "text", "label": follow_up, "required": True},
    ]
    plan = build_autofill_plan(fields, resolver=Stub({question: "Yes"}))
    assert [(fill.index, fill.value) for fill in plan.fills] == [(0, "Yes"), (2, "N/A")]
    # When the follow-up does apply, it is yours.
    plan = build_autofill_plan(fields, resolver=Stub({question: "No"}))
    assert [(fill.index, fill.value) for fill in plan.fills] == [(1, "No")]
    assert follow_up in plan.unanswered_required_questions


def test_an_optional_follow_up_the_answer_above_rules_out_is_not_applicable() -> None:
    """Healthpeak (JazzHR): every Yes/No box has its own name, and the name box is optional."""
    question = "Were you referred to Healthpeak by a current employee?"
    follow_up = "If Yes, please provide the name of the Healthpeak team member who referred you."
    fields = [
        {"index": 0, "tag": "input", "type": "checkbox", "name": "c-1", "label": "Yes",
         "value": "Yes", "group": question, "required": True},
        {"index": 1, "tag": "input", "type": "checkbox", "name": "c-2", "label": "No",
         "value": "No", "group": question, "required": True},
        {"index": 2, "tag": "input", "type": "text", "label": follow_up, "required": False},
    ]
    plan = build_autofill_plan(fields, resolver=Stub({question: "No"}))
    assert [(fill.index, fill.value) for fill in plan.fills] == [(1, "No"), (2, "N/A")]


def test_a_follow_up_reads_an_answer_already_on_the_page() -> None:
    """A re-run, or a pass over revealed questions, finds the choice above already made."""
    question = "Do you have any relatives working at Acme?"
    fields = [
        {"index": 0, "tag": "input", "type": "radio", "name": "r", "label": "Yes",
         "group": question, "required": True, "filled": False},
        {"index": 1, "tag": "input", "type": "radio", "name": "r", "label": "No",
         "group": question, "required": True, "filled": True},
        {"index": 2, "tag": "input", "type": "text", "label": "If yes, please list them.",
         "required": True},
    ]
    plan = build_autofill_plan(fields, resolver=Stub())
    assert [(fill.index, fill.value) for fill in plan.fills] == [(2, "N/A")]


def test_a_follow_up_is_judged_by_the_question_just_above_it() -> None:
    """A yes further up says nothing about a follow-up to the question in between."""
    authorized = "Are you authorized to work in the US?"
    fields = [
        {"index": 0, "tag": "input", "type": "radio", "name": "a", "label": "Yes",
         "group": authorized, "required": True},
        {"index": 1, "tag": "input", "type": "radio", "name": "a", "label": "No",
         "group": authorized, "required": True},
        {"index": 2, "tag": "input", "type": "text", "label": "Current employer",
         "required": True},
        {"index": 3, "tag": "input", "type": "text",
         "label": "If no, please explain why you are not currently employed.", "required": True},
    ]
    plan = build_autofill_plan(
        fields, resolver=Stub({authorized: "Yes", "Current employer": "Acme"})
    )
    assert [fill.index for fill in plan.fills] == [0, 2]


def test_a_follow_up_names_the_answer_it_needs() -> None:
    assert follow_up_condition("If Yes, please provide the name of the referrer.") is True
    assert follow_up_condition("If you answered “No” to the above, explain.") is False
    assert follow_up_condition("If so, please describe the arrangement.") is True
    assert follow_up_condition("If not, please explain.") is False
    assert follow_up_condition("If not already provided, list your skills.") is None
    assert follow_up_condition("Please provide your LinkedIn profile.") is None


class Blank:
    """A none fact answering every question: blank on purpose."""

    def resolve(self, field: dict[str, Any]) -> AnswerResolution | None:
        return AnswerResolution(
            "", "pattern", "expression:e1", fact_key="education.certifications",
            formatter="none_as_blank", pattern_id="e1",
        )


def test_a_none_fact_plans_one_deliberate_blank_per_question() -> None:
    question = "Which certifications do you hold?"
    fields = [
        {"index": 0, "tag": "input", "type": "text", "label": "Address Line 2", "required": True},
        {"index": 1, "tag": "input", "type": "checkbox", "name": "c", "label": "AWS",
         "group": question, "required": False, "group_size": 2},
        {"index": 2, "tag": "input", "type": "checkbox", "name": "c", "label": "GCP",
         "group": question, "required": False, "group_size": 2},
        {"index": 3, "tag": "input", "type": "text", "label": "If yes, which ones?",
         "required": True},
    ]
    plan = build_autofill_plan(fields, resolver=Blank())

    # Nothing is operated, nothing is unanswered, and a blank never rules a follow-up out.
    assert [(fill.index, fill.kind, fill.value) for fill in plan.fills] == [
        (0, "blank", None), (1, "blank", None), (3, "blank", None),
    ]
    assert plan.unanswered_required_questions == ()
    assert plan.reasons[2] == "its group stays blank on purpose"
    assert "is none, so it stays blank on purpose" in plan.reasons[0]
