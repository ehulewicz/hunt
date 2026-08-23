from personal_os_worker.answers import (
    answer_match,
    changed_word_ratio,
    clean_answer_text,
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
