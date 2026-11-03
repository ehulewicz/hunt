from personal_os_worker.jobs.session.writing import similar_answers


def test_similar_answers_rank_near_questions_exact_match_first() -> None:
    answers = [
        {"question": "Why do you want to work at Vercel?", "answer_text": "exact"},
        {"question": "Why do you want to work here?", "answer_text": "near"},
        {"question": "Why are you interested in this role?", "answer_text": "role"},
        {"question": "What is your GitHub URL?", "answer_text": "github"},
        {"question": "Why do you want to work at Acme?", "answer_text": ""},
    ]
    ranked = similar_answers("Why do you want to work at Vercel?", answers)
    texts = [item["answer_text"] for item in ranked]
    assert texts[0] == "exact"  # the best reference there is, at ratio 1.0
    assert "github" not in texts
    assert "" not in texts
