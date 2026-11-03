import pytest

from personal_os_worker.jobs.answers import (
    answer_match,
    changed_word_ratio,
    clean_answer_text,
    draft_contextual_answer,
    valid_generated_answer,
)


def test_answer_match_is_deterministic_and_refuses_ambiguity() -> None:
    question = "Why this company?"
    one = [{"normalized_key": "why this company", "answer_text": "Because."}]
    assert answer_match(question, one) == one[0]
    assert answer_match(question, [*one, dict(one[0])]) is None


def test_twenty_percent_guard_allows_small_adaptation_and_rejects_rewrite() -> None:
    base = "I build reliable software systems for customers and internal engineering teams."
    small = "I build reliable backend systems for customers and internal engineering teams."
    rewrite = "My goal is creating entirely new products with a different focus."
    assert changed_word_ratio(base, small) <= 0.20
    assert changed_word_ratio(base, rewrite) > 0.20


def test_generated_answer_hygiene_removes_unicode_artifacts_and_blocks_new_numbers() -> None:
    assert (
        clean_answer_text("I\u200b build\u00a0tools — carefully.") == "I build tools - carefully."
    )
    assert valid_generated_answer(
        "I supported 12 customers.", base=None, fact_text="Supported 12 customers"
    )
    assert not valid_generated_answer(
        "I supported 50 customers.", base=None, fact_text="Supported 12 customers"
    )
    assert not valid_generated_answer("I delve into systems.", base=None, fact_text="")


def test_generated_answer_hygiene_rejects_leaked_tool_call_markup() -> None:
    # `claude -p --json-schema` occasionally leaks its own tool-call framing into
    # the schema's string field instead of only the drafted prose; a leaked
    # closing tag left in a form field would be visible to the reader.
    leaked = (
        "I'd bring the same first-principles approach to this role.</answer>\n</invoke>"
    )
    assert not valid_generated_answer(leaked, base=None, fact_text="")


def test_draft_prompt_carries_the_whole_career_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off-resume entries and the record's last moreBullets item reach the prompt."""
    from personal_os_worker.jobs import model

    prompts: list[str] = []
    monkeypatch.setattr(
        model, "run",
        lambda argv, **_: (prompts.append(argv[-1]), '{"answer": "ok"}')[1],
    )
    resume = {
        "experience": [{"title": "SWE", "org": "Acme", "bullets": ["x" * 31000]}],
        "activities": [
            {"title": "OFF RESUME CLUB", "org": "Tech", "onResume": False,
             "bullets": [], "moreBullets": ["TAIL MARKER"]},
        ],
    }
    draft_contextual_answer(
        question="Why us?", base=None,
        posting={"company": "Acme", "role": "SWE", "description": ""},
        resume=resume, facts={}, references=[], attempts=1,
    )
    assert len(prompts) == 1
    assert "OFF RESUME CLUB" in prompts[0]
    assert "TAIL MARKER" in prompts[0]
