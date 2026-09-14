from personal_os_worker.patterns import resolve_pattern_answer, resume_organizations

PROFILE = {
    "preferred_name": "Ethan",
    "linkedin_url": "https://www.linkedin.com/in/alex-example/",
    "github_url": "https://github.com/ehulewicz/",
    "school_name": "Example Institute of Technology",
    "degree_type": "Bachelor's",
    "field_of_study": "Computer Science",
    "city": "Atlanta",
    "region": "Georgia",
    "country": "United Stat",
    "work_authorization_status": "Yes",
    "visa_status": "Citizen",
    "sponsorship_required": False,
    "eeo_veteran_status": "No",
    "eeo_race_ethnicities": ["White"],
}
RESUME = {"experience": [{"org": "Northwind"}, {"org": "Example Public Schools"}]}
RULES = [
    {
        "reuse_policy": "pattern",
        "normalized_question": r"consider me for other (job )?opportunities",
        "answer_text": "Yes",
    },
    {"reuse_policy": "pattern", "normalized_question": r"full.time during", "answer_text": "Yes"},
    {"reuse_policy": "pattern", "normalized_question": r"during the", "answer_text": "Maybe"},
    {"reuse_policy": "exact", "normalized_question": "full-time during", "answer_text": "ignored"},
]


def ask(
    question: str, *, rules: list[dict[str, object]] = RULES, company: str = "RV Tech"
) -> str | None:
    return resolve_pattern_answer(
        question, rules=rules, profile=PROFILE, resume=RESUME, company=company
    )


def test_single_library_pattern_answers_case_insensitively() -> None:
    assert ask("I authorize RV Tech to CONSIDER ME FOR OTHER job opportunities.") == "Yes"


def test_two_library_patterns_are_ambiguous_and_answer_nothing() -> None:
    assert ask("Will you be able to work full-time during the listed dates?") is None


def test_builtin_rules_answer_from_profile_facts() -> None:
    assert ask("Preferred first name") == "Ethan"
    assert ask("LinkedIn URL") == "https://www.linkedin.com/in/alex-example/"
    assert ask("Other website") == "https://github.com/ehulewicz/"
    assert ask("What school are you currently attending?") == "Example Institute of Technology"
    assert ask("What degree are you currently pursuing?") == "Bachelor's"
    assert ask("Current or most recent employer") == "Northwind"
    assert ask("Location") == "Atlanta, Georgia, United States"
    assert ask("Location (City)") == "Atlanta, Georgia, United States"
    assert (
        ask("Are you currently authorized to work for any employer in the United States?") == "Yes"
    )
    assert (
        ask("Will you now or in the future require sponsorship for employment visa status?") == "No"
    )
    assert ask("Are you a veteran?") == "No"
    assert ask("Are you Hispanic or Latino?") == "No"
    assert ask("Are you willing to work onsite in the SF office 5 times per week?") == "Yes"


def test_citizenship_questions_are_told_apart() -> None:
    assert ask("Are you a U.S. citizen?") == "Yes"
    assert ask("What is your country of citizenship?") == "United States"
    sanctioned = (
        "Is your most recent country of (1) citizenship or (2) permanent residency one of "
        "the following countries: North Korea; Syria; Iran; or Cuba?"
    )
    assert ask(sanctioned) == "No"


def test_worked_before_is_no_unless_the_company_is_on_the_resume() -> None:
    question = "Have you ever worked for RV Tech and/or Rivian, in any capacity?"
    assert ask(question, rules=[]) == "No"
    assert ask(question, rules=[], company="Northwind") is None
    assert ask("Have you previously been employed by Acme?", rules=[], company="Acme") == "No"
    assert ask("Why do you want to work here?", rules=[]) is None


def test_unknown_facts_answer_nothing() -> None:
    empty = resolve_pattern_answer(
        "Preferred first name", rules=[], profile={}, resume={}, company="Acme"
    )
    assert empty is None


def test_resume_organizations_reads_every_entry_org() -> None:
    resume = {
        "experience": [{"org": "Northwind"}],
        "research": [{"org": "Sample Lab, UIC"}],
        "projects": [{"org": ""}],
    }
    assert resume_organizations(resume) == frozenset({"merchlogix", "dream lab, uic"})


def test_technology_questions_answer_with_the_whole_skills_section() -> None:
    resume = {
        "skills": [
            {"category": "Languages", "items": "Python, Java, TypeScript"},
            {"category": "Tools", "items": "Docker, Python"},
        ]
    }
    skills = "Python, Java, TypeScript, Docker"

    def answer(question: str) -> str | None:
        return resolve_pattern_answer(question, rules=[], profile={}, resume=resume, company="A")

    for question in (
        "I have experience with the following technology stacks from a previous internship:",
        "Which programming languages are you proficient in?",
        "Are you comfortable with the following technologies?",
    ):
        assert answer(question) == skills, question
    for question in ("Years of professional experience", "Why do you want this internship?"):
        assert answer(question) is None, question
