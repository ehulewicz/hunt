from pathlib import Path

from personal_os_worker.drafts import (
    DIVIDER,
    draft_slug,
    parse_draft,
    render_draft,
    similar_answers,
    write_draft,
)


def test_similar_answers_rank_near_questions_and_skip_the_exact_match() -> None:
    answers = [
        {"question": "Why do you want to work at Vercel?", "answer_text": "exact"},
        {"question": "Why do you want to work here?", "answer_text": "near"},
        {"question": "Why are you interested in this role?", "answer_text": "role"},
        {"question": "What is your GitHub URL?", "answer_text": "github"},
        {"question": "Why do you want to work at Acme?", "answer_text": ""},
    ]
    ranked = similar_answers("Why do you want to work at Vercel?", answers)
    texts = [item["answer_text"] for item in ranked]
    assert texts[0] == "near"
    assert "exact" not in texts
    assert "github" not in texts
    assert "" not in texts


def test_parse_draft_takes_only_the_text_above_the_divider() -> None:
    text = render_draft(
        seed="I like edge networks.\n\nSecond paragraph.",
        question="Why Vercel?",
        company="Vercel",
        role="Intern",
        source_note="library answer",
        similar=[{"question": "Why here?", "answer_text": "ignored"}],
    )
    assert DIVIDER in text
    assert parse_draft(text) == "I like edge networks.\n\nSecond paragraph."
    assert parse_draft("only an answer, divider deleted\n") == "only an answer, divider deleted"
    assert parse_draft(f"\n\n{DIVIDER}\nQuestion: x") == ""


def test_write_draft_names_files_by_field_and_question(tmp_path: Path) -> None:
    draft = write_draft(
        tmp_path / "run",
        field_index=3,
        question="Tell us about a project you're proud of!",
        seed="",
        seed_source="empty",
        company="Viam",
        role="SWE Intern",
        source_note="no library match",
        similar=[],
    )
    assert draft.path.name == "03-tell-us-about-a-project-you-re-proud-of.md"
    assert draft.path.read_text().startswith(f"\n\n{DIVIDER}")
    assert "Similar library answers: none" in draft.path.read_text()
    assert draft_slug("???") == "question"
