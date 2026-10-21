from typing import Any

from personal_os_worker.jobs.learn import Lesson
from personal_os_worker.jobs.registry.events import value_hash
from personal_os_worker.jobs.replay import (
    Answer,
    Outcome,
    Sighting,
    replay_code,
    replay_pattern,
    same_answer,
    select,
    sightings_of,
    touched_by,
)
from personal_os_worker.jobs.resolve import Knowledge


def expression(pattern: str, fact_key: str) -> dict[str, Any]:
    return {"id": f"e-{fact_key}", "kind": "expression", "expression": pattern,
            "fact_key": fact_key, "resolver_id": f"expression:{fact_key}", "status": "active"}


def knowledge(*patterns: dict[str, Any]) -> Knowledge:
    facts = {
        "identity.legal_name": "Alex Quinn Example",
        "identity.middle_name": "Alexander",
        "contact.email": "alex.example@example.com",
        "work_auth.requires_sponsorship": False,
    }
    return Knowledge.build(
        facts=[{"key": key, "value": value} for key, value in facts.items()],
        career=None, patterns=patterns, company="Acme", seed="application-1",
    )


NAMES = (
    expression(r"^\s*first\s+name\b", "identity.first_name"),
    expression(r"^\s*last\s+name\b", "identity.last_name"),
    expression(r"\bemail\b", "contact.email"),
    expression(r"\bsponsor", "work_auth.requires_sponsorship"),
)


def seen(question: str, answer: str, **fields: Any) -> dict[str, Any]:
    return {"question": question, "question_key": question.casefold(), "kind": "text_entry",
            "attributes": {"tag": "input", "type": "text"}, "control": "text", "options": None,
            "answer": answer, "fact_key": None, "value_hash": None} | fields


ACCEPTED = sightings_of([
    seen("First Name", "Ethan", fact_key="identity.first_name", value_hash=value_hash("Ethan")),
    seen("Last Name", "Example", fact_key="identity.last_name",
         value_hash=value_hash("Example")),
    seen("Company name", "Acme Robotics"),
    seen("Email", "alex.example@example.com", fact_key="contact.email"),
    seen("Will you require sponsorship?", "No", kind="choice_group",
         attributes={"tag": "input", "type": "radio", "group": "single"},
         options=["Yes", "No"], fact_key="work_auth.requires_sponsorship"),
    seen("Resume", "Alex_Example.pdf", kind="file", attributes={"type": "file"}),
])


def test_an_upload_is_never_replayed_and_a_control_is_rebuilt_from_its_shape() -> None:
    assert [sighting.question for sighting in ACCEPTED] == [
        "First Name", "Last Name", "Company name", "Email", "Will you require sponsorship?",
    ]
    choice = ACCEPTED[4].field()
    assert (choice["type"], choice["options"]) == ("radio", ["Yes", "No"])
    bare = Sighting.of(seen("Degree", "BS", kind="native_select", attributes=None,
                            options=["BS", "MS"])).field()
    assert (bare["tag"], bare["options"]) == ("select", ["BS", "MS"])
    # An answer from before fill events kept only the input type it was typed into.
    old = Sighting.of(seen("Phone", "5550100199", kind=None, attributes=None, control="tel"))
    assert (old.field()["tag"], old.field()["type"]) == ("input", "tel")
    radio = Sighting.of(seen("Sponsor?", "No", kind=None, attributes=None, control="radio"))
    assert radio.field()["type"] == "radio"
    assert sightings_of([seen("Why us?", "Because", kind=None, control="textarea")]) == []


def test_an_answer_is_the_same_whatever_its_punctuation_and_case() -> None:
    assert same_answer("(555) 010-0199", "224.716.4791")
    assert same_answer("Computer Science", "computer science ")
    assert not same_answer(None, "Yes") and not same_answer("", "Yes")
    assert same_answer("Python\nGo", "Go, Python", kind="multi_choice_group")
    assert not same_answer("Python", "Go, Python", kind="multi_choice_group")


def test_only_a_change_for_the_worse_fails() -> None:
    accepted = Sighting.of(seen("Middle Name", "Alexander"))
    right, wrong, other = Answer("Alexander"), Answer("Alex"), Answer("A.")
    assert not Outcome(accepted, None, right).failed  # newly right
    assert Outcome(accepted, None, other).failed  # newly wrong
    assert Outcome(accepted, right, None).failed  # a right answer taken away
    assert not Outcome(accepted, wrong, None).failed  # a wrong answer taken away
    assert Outcome(accepted, right, other).failed  # right turned wrong
    assert Outcome(accepted, wrong, other).failed  # wrong in a new way
    assert not Outcome(accepted, wrong, Answer("alex")).failed  # unchanged, if wrong
    assert not Outcome(accepted, None, other, before_known=False).failed
    # The fact can be written as you accepted it, though this control picked otherwise.
    assert not Outcome(accepted, None, Answer("A.", ("Alexander",))).failed


def test_a_deliberately_broad_expression_fails_its_replay() -> None:
    broad = Lesson("Your name", "identity.first_name", "expression", r"\bname\b").pattern()

    replay = replay_pattern(ACCEPTED, knowledge(*NAMES), broad)

    assert not replay.passed
    failed = {outcome.sighting.question: outcome.now for outcome in replay.failures}
    # "Last Name" is no longer decided (two facts match), and "Company name" gets yours.
    assert failed == {"Last Name": None, "Company name": "Ethan"}
    assert replay.verification() == {"sampled": 5, "failed": 2}
    assert "2 of 5 accepted answer(s) would change" in replay.summary()


def test_a_narrow_expression_and_a_binding_that_agree_with_you_pass() -> None:
    accepted = [*ACCEPTED, *sightings_of([seen("Middle Name", "Alexander")])]
    middle = Lesson("Middle Name", "identity.middle_name", "expression", r"\bmiddle\s+name\b")
    assert replay_pattern(accepted, knowledge(*NAMES), middle.pattern()).passed
    bound = middle.bound("")
    assert replay_pattern(accepted, knowledge(*NAMES), bound.pattern()).passed
    # Bound to a fact your accepted answer contradicts, the same question fails.
    wrong = Lesson("Middle Name", "identity.legal_name", "binding")
    assert not replay_pattern(accepted, knowledge(*NAMES), wrong.pattern()).passed


def test_a_change_replays_what_it_touches_then_the_newest_and_a_seeded_few() -> None:
    many = sightings_of([seen(f"Question {number}", "x") for number in range(60)])
    touches = touched_by(expression(r"question 5\d", "a.b"))
    chosen = select(many, touches, seed="s", recent=5, sample=5)
    assert [sighting.question for sighting in chosen[:10]] == [
        f"Question {number}" for number in range(50, 60)
    ]
    assert chosen[10:15] == many[:5]
    assert len(chosen) == 20 == len({id(sighting) for sighting in chosen})
    assert chosen == select(many, touches, seed="s", recent=5, sample=5)


def test_code_is_replayed_against_what_was_filled_then() -> None:
    accepted = sightings_of([
        # Filled then with the answer you accepted: it must stay so.
        seen("Email", "alex.example@example.com", fact_key="contact.email",
             value_hash=value_hash("alex.example@example.com")),
        # Filled then with something you corrected: wrong before, never this code's doing.
        seen("Middle Name", "Alexander", value_hash=value_hash("A")),
        # Left to you then: a code change that now fills it wrongly fails.
        seen("Company name", "Acme Robotics"),
        # From before fill events: what the worker did then is unknown, so it never fails.
        seen("Your name", "Ethan", kind=None, attributes=None),
    ])
    regressed = (expression(r"\bemail\b", "identity.middle_name"),)
    replay = replay_code(accepted, knowledge(*regressed, expression(r"\bmiddle\b",
                                                                    "identity.legal_name")))
    assert [outcome.sighting.question for outcome in replay.failures] == ["Email"]
    broad = replay_code(accepted, knowledge(expression(r"name", "identity.middle_name"),
                                            expression(r"\bemail\b", "contact.email")))
    assert [outcome.sighting.question for outcome in broad.failures] == ["Company name"]
    assert replay_code(accepted, knowledge(*NAMES)).passed
