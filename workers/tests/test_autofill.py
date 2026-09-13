from personal_os_worker.autofill import build_autofill_plan
from personal_os_worker.browser import audit_filled_form


def test_autofill_uses_profile_resume_and_exact_approved_long_form_answers() -> None:
    fields = [
        {
            "index": 0,
            "tag": "input",
            "type": "email",
            "name": "email",
            "label": "Email",
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "text",
            "name": "city",
            "label": "City",
            "required": True,
        },
        {
            "index": 2,
            "tag": "input",
            "type": "file",
            "name": "resume",
            "label": "Resume",
            "required": True,
        },
        {
            "index": 3,
            "tag": "textarea",
            "type": None,
            "name": "why",
            "label": "Why this team?",
            "required": True,
        },
    ]

    plan = build_autofill_plan(
        fields,
        profile={"email": "ethan@example.com", "city": "Atlanta"},
        approved_answers=[
            {"normalized_question": "  Why this team? ", "answer_text": "Because the work matters."}
        ],
    )

    assert [(item.index, item.kind, item.value) for item in plan.fills] == [
        (0, "text", "ethan@example.com"),
        (1, "text", "Atlanta"),
        (2, "resume", None),
        (3, "text", "Because the work matters."),
    ]


def test_autofill_matches_the_api_answer_shape_and_harmless_punctuation() -> None:
    fields = [
        {
            "index": 0,
            "tag": "textarea",
            "type": None,
            "name": "goals",
            "label": "What are your career goals",
            "required": True,
        }
    ]
    plan = build_autofill_plan(
        fields,
        profile={},
        approved_answers=[
            {
                "question": "What are your career goals?",
                "answer_text": "Build useful systems.",
                "approved": True,
            }
        ],
    )

    assert [(item.index, item.value) for item in plan.fills] == [(0, "Build useful systems.")]
    assert plan.unanswered_required_questions == ()


def test_autofill_leaves_unknown_consent_and_non_exact_answers_empty() -> None:
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
        fields,
        profile={"eeo_gender_identity": "Man"},
        approved_answers=[
            {
                "normalized_question": "Why do you want this company?",
                "answer_text": "Related category, but not an exact question.",
            }
        ],
    )

    assert plan.fills == ()
    assert plan.unanswered_required_questions == (
        "Gender",
        "I certify this is true",
        "Why do you want this role?",
    )


def test_autofill_maps_boolean_application_facts_without_using_answer_rows() -> None:
    fields = [
        {
            "index": 0,
            "tag": "select",
            "type": None,
            "name": "sponsor",
            "label": "Will you now or in the future require sponsorship?",
            "required": True,
        },
        {
            "index": 1,
            "tag": "select",
            "type": None,
            "name": "relocate",
            "label": "Are you willing to relocate?",
            "required": True,
        },
    ]

    plan = build_autofill_plan(
        fields,
        profile={"sponsorship_required": False, "willing_to_relocate": True},
        approved_answers=[],
    )

    assert [(item.index, item.value) for item in plan.fills] == [(0, "No"), (1, "Yes")]
    assert plan.unanswered_required_questions == ()


def test_autofill_uses_profile_earliest_start_text_then_exact_date() -> None:
    field = {
        "index": 0,
        "tag": "input",
        "type": "text",
        "name": "start_date",
        "label": "What is your earliest available start date?",
        "required": True,
    }

    with_note = build_autofill_plan(
        [field],
        profile={
            "earliest_start_date": "2027-05-15",
            "earliest_start_text": "Two weeks after offer acceptance",
        },
        approved_answers=[],
    )
    with_date_only = build_autofill_plan(
        [field],
        profile={"earliest_start_date": "2027-05-15", "earliest_start_text": None},
        approved_answers=[],
    )

    assert [(item.index, item.value) for item in with_note.fills] == [
        (0, "Two weeks after offer acceptance")
    ]
    assert [(item.index, item.value) for item in with_date_only.fills] == [(0, "2027-05-15")]


def test_autofill_derives_name_parts_and_us_authorization_from_profile_facts() -> None:
    fields = [
        {
            "index": 0,
            "tag": "input",
            "type": "text",
            "name": "first_name",
            "label": "First name",
            "required": True,
        },
        {
            "index": 1,
            "tag": "input",
            "type": "text",
            "name": "last_name",
            "label": "Last name",
            "required": True,
        },
        {
            "index": 2,
            "tag": "select",
            "type": None,
            "name": "authorized",
            "label": "Are you authorized to work in the United States?",
            "required": True,
        },
    ]

    plan = build_autofill_plan(
        fields,
        profile={"legal_name": "Alex Example", "work_authorization_status": "US Citizen"},
        approved_answers=[],
    )

    assert [(item.index, item.value) for item in plan.fills] == [
        (0, "Ethan"),
        (1, "Example"),
        (2, "Yes"),
    ]


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

    plan = build_autofill_plan(fields, profile={}, approved_answers=[])

    assert plan.fills == ()
    assert plan.unanswered_required_questions == ("Resume", "Resume attachment")


def test_autofill_never_turns_negative_or_unknown_authorization_into_yes() -> None:
    field = {
        "index": 0,
        "tag": "select",
        "type": None,
        "name": "authorized",
        "label": "Are you authorized to work in the United States?",
        "required": True,
    }

    negative = build_autofill_plan(
        [field], profile={"work_authorization_status": "Not authorized"}, approved_answers=[]
    )
    unknown = build_autofill_plan(
        [field], profile={"work_authorization_status": "Ask me"}, approved_answers=[]
    )

    assert [(item.index, item.value) for item in negative.fills] == [(0, "No")]
    assert unknown.fills == ()
    assert negative.unanswered_required_questions == ()
    assert unknown.unanswered_required_questions == (field["label"],)


def test_autofill_never_uses_answers_for_missing_profile_facts() -> None:
    field = {
        "index": 0,
        "tag": "select",
        "type": None,
        "name": "authorized",
        "label": "Are you authorized to work in the United States?",
        "required": True,
    }

    plan = build_autofill_plan(
        [field],
        profile={"work_authorization_status": "Ask me"},
        approved_answers=[
            {
                "normalized_question": field["label"],
                "answer_text": "Yes",
            }
        ],
    )

    assert plan.fills == ()
    assert plan.unanswered_required_questions == (field["label"],)


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
    answers = [
        {"normalized_question": field["label"], "answer_text": "approved value"} for field in fields
    ]

    plan = build_autofill_plan(fields, profile={}, approved_answers=answers)

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
    answers = [{"normalized_question": "How did you hear about us?", "answer_text": "LinkedIn"}]

    plan = build_autofill_plan(fields, profile={}, approved_answers=answers)

    assert [(item.index, item.value) for item in plan.fills] == [(0, "LinkedIn")]
    assert plan.unanswered_required_questions == ()


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

    plan = build_autofill_plan([field], profile={}, approved_answers=[])

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
        "sensitive_filled": [],
    }


def test_autofill_leaves_duplicate_exact_answers_empty_as_ambiguous() -> None:
    field = {
        "index": 0,
        "tag": "textarea",
        "type": None,
        "name": "motivation",
        "label": "Why this role?",
        "group": "Application questions",
        "required": True,
    }

    plan = build_autofill_plan(
        [field],
        profile={},
        approved_answers=[
            {"normalized_question": "Why this role?", "answer_text": "First answer"},
            {"normalized_question": " why this role? ", "answer_text": "Second answer"},
        ],
    )

    assert plan.fills == ()
    assert plan.unanswered_required_questions == ("Why this role?",)


def test_ashby_style_labels_map_to_profile() -> None:
    """Bestow's Ashby form labels these exactly 'Name' and 'Address Line 1'."""
    from personal_os_worker.autofill import PROFILE_LABELS

    assert PROFILE_LABELS["name"] == "legal_name"
    assert PROFILE_LABELS["address line 1"] == "address_line_1"


def test_exact_matching_still_refuses_to_guess() -> None:
    """Matching is exact, so a qualified label must not borrow the bare mapping."""
    from personal_os_worker.autofill import PROFILE_LABELS

    for label in ("company name", "reference name", "name of school"):
        assert label not in PROFILE_LABELS


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
