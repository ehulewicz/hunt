"""Engineering focus takes what the posting names, else draws from the set."""

from __future__ import annotations

from personal_os_worker.jobs.focus import choose_focus, focus_text
from personal_os_worker.jobs.formatters import render
from personal_os_worker.jobs.resolve import Knowledge

INTERESTS = [
    "Backend Engineering",
    "Machine Learning / AI Engineering",
    "Full-Stack Engineering",
    "Forward Deployed Engineering",
    "Simulation & Modeling Engineering",
    "Scientific Computing",
    "Algorithms Engineering",
    "Robotics & Autonomous Systems",
    "Research Engineering",
    "Applied Software Engineering",
    "Systems Engineering",
]
SEED = "app-1"


def _choose(
    options: list[str], *, title: str = "", seed: str = SEED, question: str = "",
    multiple: bool = False,
) -> list[str]:
    return choose_focus(
        options, INTERESTS, title=title, seed=seed, question=question, multiple=multiple
    )


def test_the_option_the_title_names_wins() -> None:
    options = ["Mobile", "Security", "Backend", "Full Stack", "Web"]
    assert _choose(options, title="Backend Software Engineer Intern") == ["Backend"]


def test_a_named_option_outside_the_interests_wins() -> None:
    assert _choose(["iOS", "Android"], title="Software Engineer Intern, iOS") == ["iOS"]


def test_an_offered_interest_is_drawn_when_the_title_is_silent() -> None:
    options = ["Frontend Engineer", "Backend Engineer", "Mobile Engineer", "Platform Engineer"]
    seen = {_choose(options, title="Software Engineer Intern", seed=str(n))[0] for n in range(50)}
    assert seen == {"Backend Engineer", "Platform Engineer"}


def test_the_same_seed_draws_the_same_answer() -> None:
    options = ["Frontend Engineer", "Backend Engineer", "Mobile Engineer", "Platform Engineer"]
    first = _choose(options, seed="seed-a")
    assert first == _choose(options, seed="seed-a")


def test_first_and_second_choice_draw_from_the_same_ranking() -> None:
    options = ["Frontend Engineer", "Backend Engineer", "Mobile Engineer", "Platform Engineer"]
    first = _choose(options, question="Please select your first choice of interest.")
    second = _choose(options, question="Please select your second choice of interest.")
    assert first != second
    assert set(first) | set(second) <= {"Backend Engineer", "Platform Engineer"}


def test_placeholder_options_are_never_chosen() -> None:
    options = ["Design (UX/UI)", "Client Services", "Other"]
    seen = {_choose(options, seed=str(n))[0] for n in range(50)}
    assert "Other" not in seen


def test_an_option_sharing_one_word_is_not_named() -> None:
    options = ["Full Stack", "Backend"]
    seen = {
        _choose(options, title="Software Engineer Intern (Full-Time)", seed=str(n))[0]
        for n in range(50)
    }
    assert seen == {"Full Stack", "Backend"}


def test_a_dashes_placeholder_is_not_a_real_option() -> None:
    options = ["-- No answer --", "Backend", "Mobile"]
    assert _choose(options, title="Software Engineer Intern") == ["Backend"]


def test_fitting_the_posting_beats_the_interests() -> None:
    frontend = "Frontend Engineer (React, TypeScript)"
    options = [frontend, "Backend Engineer"]
    assert _choose(options, title="Frontend Engineer Intern") == [frontend]


def test_multi_select_takes_every_interest_match_in_page_order() -> None:
    options = ["iOS", "Backend", "Full Stack", "Other"]
    assert _choose(options, multiple=True) == ["Backend", "Full Stack"]


def test_free_text_names_the_matching_interest() -> None:
    assert (
        focus_text(INTERESTS, title="Robotics Software Intern", seed=SEED)
        == "Robotics & Autonomous Systems"
    )


def test_free_text_draws_an_interest_when_the_title_is_silent() -> None:
    picked = focus_text(INTERESTS, title="Software Engineer Intern", seed=SEED)
    assert picked in INTERESTS
    assert picked == focus_text(INTERESTS, title="Software Engineer Intern", seed=SEED)


def test_render_uses_the_focus_formatter() -> None:
    found = render(
        "preferences.engineering_focus",
        INTERESTS,
        "list",
        field={
            "tag": "select",
            "type": "",
            "options": ["Mobile", "Security", "Backend", "Full Stack", "Web"],
        },
        question="Which engineering area are you most interested in?",
        seed=SEED,
        context={"posting.title": "Backend Software Engineer Intern"},
    )
    assert found is not None
    assert found.formatter == "focus_choice"
    assert found.value == "Backend"


def test_posting_title_is_a_derived_fact() -> None:
    knowledge = Knowledge.build(facts=[], career=None, title="Platform Intern 2027")
    assert knowledge.facts["posting.title"] == "Platform Intern 2027"
