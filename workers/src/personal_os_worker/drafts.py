"""Reference material for long-form answers.

A textarea is seeded directly on the page with its best answer. These helpers
rank the library answers whose question resembles the one being drafted so a
Claude draft can lean on them.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from personal_os_worker.autofill import normalize_answer_question

SIMILAR_THRESHOLD = 0.6
SIMILAR_LIMIT = 5


def question_similarity(left: str, right: str) -> float:
    return SequenceMatcher(
        None, normalize_answer_question(left), normalize_answer_question(right)
    ).ratio()


def similar_answers(
    question: str,
    answers: list[dict[str, Any]],
    *,
    threshold: float = SIMILAR_THRESHOLD,
    limit: int = SIMILAR_LIMIT,
) -> list[dict[str, Any]]:
    """Rank library answers whose question resembles this one, best first.

    Exact matches are excluded because the caller already handles them as the
    seed; this list is the reference material below the divider.
    """
    key = normalize_answer_question(question)
    scored: list[tuple[float, dict[str, Any]]] = []
    for answer in answers:
        other = str(answer.get("question") or answer.get("normalized_question") or "")
        if not other or not str(answer.get("answer_text") or "").strip():
            continue
        if normalize_answer_question(other) == key:
            continue
        score = question_similarity(question, other)
        if score >= threshold:
            scored.append((score, answer))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [answer for _, answer in scored[:limit]]
