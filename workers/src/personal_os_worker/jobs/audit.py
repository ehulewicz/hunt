"""The audit: what is still wrong with a filled form, and which lane fixes it.

The audit runs after every tier has had its turn on a page step. It looks at two
things — questions with no answer, and answers that disagree with a fact — and
turns each into a `Finding` naming the cause. The cause decides the repair:

| cause           | what it means                               | repair                |
|-----------------|---------------------------------------------|-----------------------|
| `fact_gap`      | no fact answers it, and we know which fact  | set the fact          |
| `pattern_gap`   | nothing connects the question to a fact     | the model names one   |
| `format_error`  | the fact is known, no option fits it        | other renderings      |
| `control_error` | the value is right, the widget refused it   | other strategies      |
| `wrong_answer`  | a fact says the answer on the page is wrong | bind it to that fact  |
| `unknown_control` | a widget no control kind captures         | code (`jobs/fix/`)    |
| `wall`          | the run cannot reach the form or its next page | navigation, then code |
| `posting_not_found` | the page is not the posting, and no search found it | a route, in code |

An error the form itself shows on a field the audit thought fine takes the lane
of the cause it reads as: a `format_error` for a field filled from a fact, a
`draft_error` for long-form, otherwise a `control_error`. The page's words go in
the note, so they reach the fixer's prompt.

A model only ever names a fact; what it names is saved as a pattern and the
pattern pass fills the field (R67). A question a pattern answers that is still
empty is a `control_error`: the value was right and did not stay.

This module is pure: it classifies, it never touches a page or the API. The
session performs the repairs and counts them against a budget, so one
pathological control cannot spend a session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from personal_os_worker.jobs.options import normalize_answer_question

Cause = Literal[
    "fact_gap", "pattern_gap", "format_error", "control_error", "wrong_answer",
    "detection_error", "draft_error", "model_error", "unknown_control", "wall",
    "posting_not_found",
]
# Repaired on the page by the existing repair lane, which stores a candidate
# rule that `os hunt validate` can promote.
REPAIRABLE_CAUSES = frozenset({"format_error", "control_error"})


CAPTCHA_FRAME = re.compile(r"recaptcha|hcaptcha|turnstile", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the audit found, and everything the repair needs to know."""

    question: str
    cause: Cause
    required: bool
    note: str
    fact_key: str | None = None
    formatter: str | None = None
    # The words a `no_fact` verdict used for the fact that would answer it.
    missing_fact: str | None = None
    # For a wrong answer: what the page holds, and who put it there.
    answer: str | None = None
    source: str | None = None
    intended_value: str | None = None
    # What a fix needs to find the thing again: an unknown control's role,
    # selector, frame and markup, or what a wall offered.
    evidence: dict[str, Any] | None = None


@dataclass
class Budget:
    """How many page operations the audit may spend, per question and per run."""

    per_question: int = 25
    per_run: int = 150
    spent: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def allows(self, question: str) -> bool:
        return self.total < self.per_run and self.spent.get(question, 0) < self.per_question

    def charge(self, question: str, cost: int = 1) -> None:
        self.spent[question] = self.spent.get(question, 0) + cost
        self.total += cost

    def exhausted(self, question: str) -> str | None:
        """Why this question may not be worked on, when it may not."""
        if self.total >= self.per_run:
            return f"the run's repair budget of {self.per_run} operations is spent"
        if self.spent.get(question, 0) >= self.per_question:
            return f"this question has used its {self.per_question} repair operations"
        return None


def classify_empty(
    question: str,
    *,
    required: bool,
    needed: dict[str, str],
    misses: dict[str, Any],
    failures: dict[str, dict[str, Any]],
    resolved: dict[str, Any] | None = None,
) -> Finding:
    """Why this question has no answer, from what the fill already learned.

    A recorded failure is the most specific evidence, then what the current
    resolver answers it with (`resolved`, keyed like `misses`), then the model's
    named missing fact, then the resolver's miss. With none of them, nothing
    connected the question to a fact at all, which is a pattern gap.
    """
    key = normalize_answer_question(question)
    failure = failures.get(key)
    if failure is not None and failure.get("stopped_at") in {"format", "strategy"}:
        stopped = str(failure["stopped_at"])
        return Finding(
            question,
            "format_error" if stopped == "format" else "control_error",
            required,
            str(failure.get("error_message") or "")[:200]
            or ("the fact fits no option" if stopped == "format" else "the widget refused it"),
            fact_key=failure.get("fact_key"),
            formatter=failure.get("formatter"),
            intended_value=failure.get("intended_value"),
        )
    resolution = (resolved or {}).get(key)
    if resolution is not None:
        return Finding(
            question,
            "control_error",
            required,
            "a pattern answers it, but the value did not stay",
            fact_key=getattr(resolution, "fact_key", None),
            formatter=getattr(resolution, "formatter", None),
            intended_value=getattr(resolution, "value", None),
        )
    if question in needed:
        return Finding(
            question,
            "fact_gap",
            required,
            f"needs a fact: {needed[question]}",
            missing_fact=needed[question],
        )
    miss = misses.get(key)
    stage = getattr(miss, "stage", None)
    if stage == "format":
        return Finding(
            question,
            "format_error",
            required,
            getattr(miss, "note", "") or "the fact fits none of the options",
            fact_key=getattr(miss, "fact_key", None),
            formatter=getattr(miss, "formatter", None),
        )
    if stage == "pattern":
        fact_key = getattr(miss, "fact_key", None)
        return Finding(
            question,
            "fact_gap",
            required,
            getattr(miss, "note", "") or f"no fact {fact_key} yet",
            fact_key=fact_key,
        )
    return Finding(question, "pattern_gap", required, "no pattern answers it yet")


def classify_unknown(
    controls: list[dict[str, Any]], captured: set[str]
) -> list[Finding]:
    """An empty widget no kind captures is an unknown control, once per question.

    `captured` holds the normalized questions `inspect_fields` already sees, so
    a custom widget that mirrors a captured input is not reported twice.
    """
    findings: dict[str, Finding] = {}
    for control in controls:
        # A captcha's own box is yours to tick; no code ever operates it.
        frame_url = str(control.get("frame_url") or "")
        if control.get("filled") is True or CAPTCHA_FRAME.search(frame_url):
            continue
        role = str(control.get("role") or "control")
        text = str(control.get("question") or "").strip()
        question = text or f"An unlabelled {role}"
        key = normalize_answer_question(question)
        if key in captured or key in findings:
            continue
        evidence = {
            name: control.get(name)
            for name in ("role", "tag", "selector", "options", "frame_index",
                         "frame_url", "fingerprint", "html")
        }
        if not text:
            # No learned file type can give a widget a question, so a kind kept
            # for it could never fill it: reported, never submitted for a fix.
            evidence["unlabelled"] = True
        findings[key] = Finding(
            question,
            "unknown_control",
            control.get("required") is True,
            f"a {role} that no control kind operates yet",
            evidence=evidence,
        )
    return list(findings.values())


def order(findings: list[Finding]) -> list[Finding]:
    """Required questions first, then the causes a repair can act on."""
    rank = {
        "control_error": 0,
        "format_error": 1,
        "wrong_answer": 2,
        "pattern_gap": 3,
        "fact_gap": 4,
        "unknown_control": 5,
    }
    return sorted(
        findings, key=lambda item: (not item.required, rank.get(item.cause, 9), item.question)
    )


def summarize(findings: list[Finding]) -> str:
    """One line: how many of each cause remain."""
    counts: dict[str, int] = {}
    for item in findings:
        counts[item.cause] = counts.get(item.cause, 0) + 1
    return ", ".join(
        f"{count} {cause.replace('_', ' ')}" for cause, count in sorted(counts.items())
    )
