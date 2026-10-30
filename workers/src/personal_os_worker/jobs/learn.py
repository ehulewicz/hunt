"""Learning: a model verdict becomes a pattern, never a value (design §8, R67).

A model pass names the fact that answers a question, and may propose one new
branch of an expression for the wording of it. Nothing it says reaches the
form. The worker checks what it proposes against every question already met,
saves the pattern active with origin `model`, and the ordinary pattern pass
fills the field from it in the same run.

An expression is kept only when it compiles, nests no repetition in another,
matches the question or its label without trailing marks, is anchored when the
stripped label is four words or fewer, decides the question as that fact among
the active patterns, and answers every other recorded question it matches
either from that fact or from nothing yet. Otherwise the question is bound on
its own. A model never replaces a binding, except the one a model taught when
an answer check names it wrong; a pattern you made is never overridden for a
question it answers.

Before it is saved, a lesson is replayed against the answers you accepted
(`jobs/replay.py`). An expression that would change one for the worse falls
back to binding the question alone, replayed in turn; a lesson whose replay
still fails is saved as a candidate, and waits in the Inbox as a pattern check.

This module is pure: it decides what to learn, and the session saves it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from personal_os_worker.jobs.options import (
    expression_matches,
    normalize_answer_question,
    stripped_label,
)
from personal_os_worker.jobs.resolve import Knowledge, Resolver

# A proposed expression longer than this is not a wording, it is a program.
EXPRESSION_LIMIT = 300
# A group that repeats and holds a repetition of its own, `(\w+\s?)+`: the shape
# whose backtracking can hang on one long label. The API refuses it too.
NESTED_REPETITION = re.compile(
    r"\((?:[^()\\]|\\.)*(?:[+*]|\{\d*,\d*\})(?:[^()\\]|\\.)*\)(?:[+*]|\{\d*,\d*\})"
)


@dataclass(frozen=True, slots=True)
class Lesson:
    """A pattern to save: an expression for every wording, or a binding for this one."""

    question: str
    fact_key: str
    kind: Literal["expression", "binding"]
    expression: str | None = None
    # Why a proposed expression was not kept, when the lesson fell back to a binding.
    note: str = ""
    # The model-taught binding this one replaces (a wrong answer correction).
    replaces: str | None = None

    def shape(self) -> dict[str, Any]:
        """What the API saves: the expression, or the question by its wording."""
        if self.kind == "expression":
            return {"kind": "expression", "expression": self.expression}
        shape: dict[str, Any] = {"kind": "binding", "question": self.question}
        if self.replaces is not None:
            shape["replaces"] = self.replaces
        return shape

    def pattern(self) -> dict[str, Any]:
        """The active pattern this lesson would become, as a resolver reads one."""
        shape = (
            {"kind": "expression", "expression": self.expression}
            if self.kind == "expression"
            else {"kind": "binding", "question_key": self.question}
        )
        return shape | {
            "id": "proposed", "fact_key": self.fact_key, "status": "active", "origin": "model",
        }

    def bound(self, note: str) -> Lesson:
        """The question alone, bound to the same fact."""
        return Lesson(self.question, self.fact_key, "binding", note=note)


def _fact(pattern: dict[str, Any] | None) -> str | None:
    return str(pattern["fact_key"]) if pattern is not None else None


def _resolver(patterns: Iterable[dict[str, Any]]) -> Resolver:
    return Resolver(Knowledge(patterns=tuple(patterns)))


def expression_refusal(
    expression: str,
    question: str,
    fact_key: str,
    *,
    patterns: list[dict[str, Any]],
    questions: Iterable[str],
) -> str | None:
    """Why this expression may not be kept, or None when it may."""
    if len(expression) > EXPRESSION_LIMIT:
        return f"it runs past {EXPRESSION_LIMIT} characters"
    if NESTED_REPETITION.search(expression):
        return "it nests one repetition inside another"
    try:
        compiled = re.compile(expression, re.IGNORECASE | re.DOTALL)
    except re.error as error:
        return f"it does not compile ({error})"
    if not expression_matches(compiled, question):
        return "it does not match the question"
    if (
        len(re.findall(r"[A-Za-z0-9]+", stripped_label(question))) <= 4
        and not (expression.startswith("^") and expression.endswith("$"))
    ):
        return "a label of four words or fewer needs an anchored expression"
    proposed = Lesson(question, fact_key, "expression", expression).pattern()
    before, after = _resolver(patterns), _resolver([*patterns, proposed])
    if _fact(after.decision(question)) != fact_key:
        return "the active patterns would still not decide the question as that fact"
    own = normalize_answer_question(question)
    for label in questions:
        if normalize_answer_question(label) == own or not expression_matches(
            compiled, label
        ):
            continue
        answered = _fact(before.decision(label))
        if answered is not None and answered != fact_key:
            return f"it matches {label.strip()!r}, which is answered from {answered}"
    return None


def binding_refusal(question: str, *, patterns: list[dict[str, Any]]) -> str | None:
    """Why the question may not be bound by a model, or None when it may.

    A binding's origin says who proposed it, not who accepted it, so a model
    replaces none of them.
    """
    if normalize_answer_question(question) in _resolver(patterns).bindings:
        return "the question is bound already, and a model never replaces a binding"
    return None


def override_refusal(question: str, *, patterns: list[dict[str, Any]]) -> str | None:
    """Why a model may not change the fact a question is answered from, or None.

    Only a pattern a model taught may be overridden for one question; one you
    made is yours to change.
    """
    decision = _resolver(patterns).decision(question)
    if decision is not None and decision.get("origin") != "model":
        return "a pattern you made answers it, and a model never overrides yours"
    return None


def lesson_for(
    question: str,
    fact_key: str,
    expression: str | None,
    *,
    patterns: list[dict[str, Any]],
    questions: Iterable[str],
    overriding: bool = False,
) -> tuple[Lesson | None, str]:
    """What to learn from a verdict, and why nothing can be when it is None.

    `overriding` is for a question a pattern already answers wrongly: only a
    pattern a model taught may give way.
    """
    if overriding:
        refusal = override_refusal(question, patterns=patterns)
        if refusal is not None:
            return None, refusal
        binding = _resolver(patterns).bindings.get(normalize_answer_question(question))
        if binding is not None and binding.get("origin") == "model":
            return Lesson(
                question, fact_key, "binding", replaces=str(binding.get("id"))
            ), ""
    note = ""
    if expression:
        refusal = expression_refusal(
            expression, question, fact_key, patterns=patterns, questions=list(questions)
        )
        if refusal is None:
            return Lesson(question, fact_key, "expression", expression), ""
        note = f"expression not kept: {refusal}"
    refusal = binding_refusal(question, patterns=patterns)
    if refusal is not None:
        return None, refusal
    return Lesson(question, fact_key, "binding", note=note), ""
