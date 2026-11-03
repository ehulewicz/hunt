"""The Inbox: learned patterns, questions, prose, facts, gaps, and failures waiting on you."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from personal_os_backend.config import StoreBase
from personal_os_backend.jobs_changes import CHANGE_SELECT, Change, latest_failed
from personal_os_backend.jobs_patterns import (
    FACT_KEY,
    LIVE_STATUSES,
    PATTERN_COLUMNS,
    PATTERN_FROM,
    Pattern,
)
from personal_os_backend.jobs_prose import PROSE_SELECT, Prose
from personal_os_backend.jobs_questions import (
    QUESTION_COLUMNS,
    Question,
    answered_by_expression,
    asks_a_question,
)


class AnswerSeen(BaseModel):
    """One answer recorded for a question on an application."""

    company_name: str
    title: str
    answer_text: str
    source: str | None
    submitted: bool
    created_at: datetime


class InboxCandidate(BaseModel):
    pattern: Pattern
    answers: list[AnswerSeen]
    # A pattern check: the learned change that failed its replay, and so waits on you.
    check: Change | None = None


class InboxQuestion(BaseModel):
    question: Question
    answers: list[AnswerSeen]


class InboxFact(BaseModel):
    """A fact waiting on you: never verified, named missing by a run, or set to a
    value the answers you accepted disagree with."""

    reason: Literal["unverified", "missing", "contradicted"]
    key: str | None
    label: str | None
    value: Any = None
    value_type: str | None = None
    # For a missing fact: the question that needed it, and what the model called it.
    question: str | None = None
    missing_fact: str | None = None
    issue_id: UUID | None = None
    # For a contradicted fact: the replay that found the disagreement.
    check: Change | None = None


class InboxUnsolved(BaseModel):
    """A failure no lane and no code fix settled; what the fixer tried is in its notes."""

    issue_id: UUID
    category: str
    question: str | None
    control_kind: str | None
    note: str | None
    occurrence_count: int
    last_seen_at: datetime


class ProseGap(BaseModel):
    """A long-form question the library covers only partly or not at all."""

    id: UUID
    question_id: UUID
    question: str | None
    application_id: UUID | None
    company_name: str | None
    title: str | None
    family: str
    coverage: Literal["partial", "missing"]
    missing_detail: str
    created_at: datetime
    resolved_at: datetime | None


class Inbox(BaseModel):
    # Learned patterns waiting on you; `checks` are the ones whose replay failed.
    candidates: list[InboxCandidate]
    checks: list[InboxCandidate] = Field(default_factory=list)
    questions: list[InboxQuestion]
    prose: list[Prose]
    facts: list[InboxFact] = Field(default_factory=list)
    unsolved: list[InboxUnsolved] = Field(default_factory=list)
    # Open prose gaps, filled by `jobs_gaps.open_gaps`.
    gaps: list[ProseGap] = Field(default_factory=list)


async def _answers_for(
    connection: AsyncConnection[Any], user_id: UUID, question_id: UUID, *, limit: int = 3
) -> list[AnswerSeen]:
    cursor = await connection.execute(
        """
        select company.name as company_name, posting.title, answer.answer_text,
               answer.answer_source as source, answer.status = 'submitted' as submitted,
               answer.created_at
        from app.jobs_application_questions answer
        join app.jobs_applications application on application.id = answer.application_id
        join app.jobs_postings posting on posting.id = application.posting_id
        join app.jobs_companies company on company.id = posting.company_id
        where answer.user_id = %s and answer.question_id = %s and answer.event_kind = 'final'
          and coalesce(answer.answer_text, '') <> ''
        order by answer.created_at desc
        limit %s
        """,
        (user_id, question_id, limit),
    )
    return [AnswerSeen.model_validate(row) for row in await cursor.fetchall()]


class InboxStore(Protocol):
    async def inbox(self, user_id: UUID) -> Inbox: ...


class InboxMixin(StoreBase):
    async def inbox(self, user_id: UUID) -> Inbox:
        """Everything waiting on the user: candidate patterns, unbound questions, new prose.

        An unbound question an active expression already decides, or one that
        asks nothing, is not waiting on anyone.
        """
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            candidates, failed_checks = await _waiting_patterns(connection, user_id)
            expressions = await _active_expressions(connection, user_id)
            questions = await _unbound_questions(connection, user_id, expressions)
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.user_id = %s and not prose.approved "
                "order by prose.created_at",
                (user_id,),
            )
            prose = [Prose.model_validate(row) for row in await cursor.fetchall()]
            facts = await _facts_waiting(connection, user_id)
            unsolved = await _unsolved(connection, user_id)
            # Imported here: jobs_gaps imports this module for its models.
            from personal_os_backend.jobs_gaps import open_gaps

            gaps = await open_gaps(connection, user_id)
        return Inbox(
            candidates=candidates, checks=failed_checks, questions=questions, prose=prose,
            facts=facts, unsolved=unsolved, gaps=gaps,
        )


async def _waiting_patterns(
    connection: AsyncConnection[Any], user_id: UUID
) -> tuple[list[InboxCandidate], list[InboxCandidate]]:
    """Candidate and repaired patterns with their answers: plain candidates, then failed checks."""
    cursor = await connection.execute(
        f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.user_id = %s "
        "and pattern.status in ('candidate', 'repaired') order by pattern.created_at",
        (user_id,),
    )
    waiting = [Pattern.model_validate(row) for row in await cursor.fetchall()]
    checks = await latest_failed(
        connection, user_id, [str(pattern.id) for pattern in waiting]
    )
    waiting_on_you = [
        InboxCandidate(
            pattern=pattern,
            answers=await _answers_for(connection, user_id, pattern.question_id)
            if pattern.question_id
            else [],
            check=checks.get(str(pattern.id)),
        )
        for pattern in waiting
    ]
    candidates = [item for item in waiting_on_you if item.check is None]
    failed_checks = [item for item in waiting_on_you if item.check is not None]
    return candidates, failed_checks


async def _active_expressions(
    connection: AsyncConnection[Any], user_id: UUID
) -> list[tuple[re.Pattern[str], str]]:
    """The active expressions that compile, each with the fact it names."""
    cursor = await connection.execute(
        "select expression, fact_key from app.jobs_patterns where user_id = %s "
        "and kind = 'expression' and status = 'active'",
        (user_id,),
    )
    expressions: list[tuple[re.Pattern[str], str]] = []
    for row in await cursor.fetchall():
        try:
            regex = re.compile(str(row["expression"]), re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        expressions.append((regex, str(row["fact_key"])))
    return expressions


async def _unbound_questions(
    connection: AsyncConnection[Any],
    user_id: UUID,
    expressions: list[tuple[re.Pattern[str], str]],
) -> list[InboxQuestion]:
    """The unbound short questions waiting on you, each with the answers recorded for it."""
    # `uploads`: met on a file control, for rows recorded before
    # `upsert_question` stopped recording uploads.
    cursor = await connection.execute(
        f"""
        select {QUESTION_COLUMNS}, exists (
          select 1 from app.jobs_fill_events event
          join app.jobs_controls control on control.id = event.control_id
          where event.question_id = question.id and control.kind = 'file'
        ) as uploads
        from app.jobs_questions question
        where question.user_id = %s and question.disposition = 'unbound'
          and question.form = 'short'
          and not exists (
            select 1 from app.jobs_patterns pattern
            where pattern.question_id = question.id and pattern.status = any(%s)
          )
        order by question.last_seen_at desc
        """,
        (user_id, list(LIVE_STATUSES)),
    )
    return [
        InboxQuestion(
            question=question,
            answers=await _answers_for(connection, user_id, question.id),
        )
        for question in (
            Question.model_validate(row)
            for row in await cursor.fetchall()
            if asks_a_question(row["sample_label"], "file" if row["uploads"] else None)
            and not answered_by_expression(row["sample_label"], expressions)
        )
    ]


async def _facts_waiting(connection: AsyncConnection[Any], user_id: UUID) -> list[InboxFact]:
    """Facts never verified, facts a run named missing, and facts a replay contradicts."""
    cursor = await connection.execute(
        "select key, label, value, value_type from app.jobs_facts "
        "where user_id = %s and verified_at is null order by key",
        (user_id,),
    )
    facts = [
        InboxFact(reason="unverified", key=row["key"], label=row["label"], value=row["value"],
                  value_type=row["value_type"])
        for row in await cursor.fetchall()
    ]
    cursor = await connection.execute(
        """
        select issue.id, issue.field_label, latest.dom_signals
        from app.jobs_autofill_failure_issues issue
        left join lateral (
          select occurrence.dom_signals from app.jobs_autofill_failure_occurrences occurrence
          where occurrence.issue_id = issue.id order by occurrence.created_at desc limit 1
        ) latest on true
        where issue.user_id = %s and issue.category = 'fact_gap'
          and issue.status in ('open', 'unsolved')
        order by issue.last_seen_at desc
        """,
        (user_id,),
    )
    for row in await cursor.fetchall():
        signals = dict(row["dom_signals"] or {})
        named = str(signals.get("fact_key") or signals.get("missing_fact") or "")
        facts.append(InboxFact(
            reason="missing", key=named if FACT_KEY.fullmatch(named) else None, label=None,
            question=row["field_label"], missing_fact=signals.get("missing_fact"),
            issue_id=row["id"],
        ))
    cursor = await connection.execute(
        f"""
        select distinct on (change.subject_id) {CHANGE_SELECT}, fact.label, fact.value,
               fact.value_type
        from app.jobs_changes change
        join app.jobs_facts fact on fact.user_id = change.user_id and fact.key = change.subject_id
        where change.user_id = %s and change.subject = 'fact'
        order by change.subject_id, change.created_at desc
        """,
        (user_id,),
    )
    for row in await cursor.fetchall():
        change = Change.model_validate(row)
        if change.status == "failed":
            facts.append(InboxFact(
                reason="contradicted", key=change.subject_id, label=row["label"],
                value=row["value"], value_type=row["value_type"], check=change,
            ))
    return facts


async def _unsolved(connection: AsyncConnection[Any], user_id: UUID) -> list[InboxUnsolved]:
    """Issues the fixer gave up on, newest first."""
    cursor = await connection.execute(
        """
        select id as issue_id, category, field_label as question, control_kind,
               verification_notes as note, occurrence_count, last_seen_at
        from app.jobs_autofill_failure_issues
        where user_id = %s and status = 'unsolved'
        order by last_seen_at desc
        """,
        (user_id,),
    )
    return [InboxUnsolved.model_validate(row) for row in await cursor.fetchall()]
