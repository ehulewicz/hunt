"""Questions: whether a label asks one, its key, how expressions decide it, and recording it."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

Disposition = Literal["unbound", "pattern", "one_off", "long_form"]


def normalize_answer_key(value: str) -> str:
    """Stable key for deterministic question matching across harmless punctuation."""
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).split())


# Labels a page puts on controls that ask nothing: a choice option standing
# alone, a document viewer's own tools, a site search box, the event fallback.
NOT_QUESTIONS = frozenset({"yes", "no", "search", "page number", "zoom level", "unlabeled"})
# A section heading taken for a control's question, e.g. "Job specific questions".
SECTION_HEADING = re.compile(r"(?:[a-z0-9+#]+ ){0,3}questions")
# An unlabeled control's fingerprint, `tag|type|name|label|role`, standing in for a label.
FINGERPRINT = re.compile(r"^[a-z][a-z0-9-]*\|[a-z-]*\|")


def asks_a_question(label: str, kind: str | None = None) -> bool:
    """Whether a control's label is a question worth recording (R44).

    An upload is answered by a document, never a fact, so a `file` control
    asks nothing the Inbox could bind.
    """
    key = normalize_answer_key(label)
    if not key or kind == "file" or FINGERPRINT.match(label.strip()):
        return False
    return key not in NOT_QUESTIONS and not SECTION_HEADING.fullmatch(key)


# Marks a form puts on a label that are not part of the question; kept
# identical to the worker's `jobs/options.py` copy so both sides match the
# same labels.
TRAILING_MARKS = re.compile(r"(?:\s*(?:[*\u2731:?]|\([^()]*\)))+\s*$")


def stripped_label(label: str) -> str:
    """The label without its trailing marks; itself when only marks remain."""
    stripped = label.strip()
    while True:
        shortened = TRAILING_MARKS.sub("", stripped).strip()
        if shortened == stripped:
            return shortened or label.strip()
        stripped = shortened


def label_forms(label: str) -> tuple[str, ...]:
    """The label as written, then without trailing marks when that differs."""
    as_written = label.strip()
    bare = stripped_label(label)
    return (as_written,) if as_written == bare else (as_written, bare)


def expression_matches(regex: re.Pattern[str], label: str) -> bool:
    """Whether an expression matches the label or its stripped form."""
    return any(regex.search(form) for form in label_forms(label))


def answered_by_expression(label: str, expressions: list[tuple[re.Pattern[str], str]]) -> bool:
    """Whether active expressions decide this label: they match it and name one fact.

    The worker's resolver decides the same way, so the Inbox never asks for a
    question a run already fills. Expressions that name different facts decide
    nothing and leave it waiting.
    """
    question = label.strip()
    return len(
        {fact for regex, fact in expressions if expression_matches(regex, question)}
    ) == 1


async def upsert_question(
    connection: AsyncConnection[Any],
    *,
    user_id: UUID,
    label: str,
    form: Literal["short", "long"] | None = None,
    kind: str | None = None,
) -> UUID | None:
    """The question's id, recording it the first time it is met (R44).

    A later sighting refreshes `last_seen_at`; a long-form sighting marks it
    long, since the control that shows it as prose is the better evidence. A
    label that asks nothing is never recorded.
    """
    key = normalize_answer_key(label)
    if not asks_a_question(label, kind):
        return None
    cursor = await connection.execute(
        """
        insert into app.jobs_questions (user_id, normalized_key, sample_label, form)
        values (%s, %s, %s, %s)
        on conflict (user_id, normalized_key) do update set
          last_seen_at = now(),
          form = case when excluded.form = 'long' then 'long' else app.jobs_questions.form end
        returning id
        """,
        (user_id, key, label.strip()[:1000], form or "short"),
    )
    row = await cursor.fetchone()
    return UUID(str(row["id"])) if row else None


class Question(BaseModel):
    id: UUID
    normalized_key: str
    sample_label: str
    form: Literal["short", "long"]
    disposition: Disposition
    pattern_id: UUID | None
    first_seen_at: datetime
    last_seen_at: datetime


class QuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `pattern` is reached by accepting a binding, not set directly.
    disposition: Literal["unbound", "one_off", "long_form"]
    origin: Literal["you", "inbox"] = "inbox"


QUESTION_COLUMNS = """
    id, normalized_key, sample_label, form, disposition, pattern_id, first_seen_at, last_seen_at
"""
