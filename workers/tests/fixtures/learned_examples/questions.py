from personal_os_worker.jobs.registry.api import Example, QuestionReader

# Radios with no fieldset: their question is the label sibling ahead of them.
READER = QuestionReader(
    name="lead_in_label",
    group_selector='div[role="radiogroup"]',
    question_selector="p.question",
    sites=("fixture.example",),
    structure="odd_radios",
    proven=False,
    examples=(
        Example(
            fixture="group.html",
            selector="#r-yes",
            value="",
            expected="Are you eligible to work here?",
        ),
    ),
)
