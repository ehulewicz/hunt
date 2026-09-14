from personal_os_worker.drafts import similar_answers


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
