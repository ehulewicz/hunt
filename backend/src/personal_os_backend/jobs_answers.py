"""Answers: questions, patterns, prose, the archive, and the Inbox over them.

Knowledge starts empty and is learned (design §0). A question is recorded the
first time a fill event meets it. A pattern connects a short-form question to a
fact; only `active` patterns are read when filling. A model never fills: the
worker checks the fact a model names and saves the pattern active with origin
`model` (R67), and the Inbox lists a pattern only while it waits on you. Prose is
long-form writing saved from submitted applications, reused once approved. The
old answer library is the archive: readable, never written, and kept only for
the facts session — no application reads it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_changes import (
    Action,
    Change,
    Failure,
    Origin,
    Verification,
    latest_failed,
    record_change,
)

FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
Disposition = Literal["unbound", "pattern", "one_off", "long_form"]
PatternStatus = Literal["candidate", "repaired", "validated", "active", "rejected", "retired"]
# A pattern that can still become active, or is.
LIVE_STATUSES = ("candidate", "repaired", "validated", "active")
# Who a new pattern's change is recorded as: an archive adoption is yours.
PATTERN_ORIGINS: dict[str, Origin] = {"inbox": "inbox", "archive": "you", "model": "model"}
STATUS_ACTIONS: dict[str, Action] = {"retired": "retire", "rejected": "reject"}


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


# A group that repeats and holds a repetition of its own, `(\w+\s?)+`: the shape
# whose backtracking can run for minutes on one long label.
NESTED_REPETITION = re.compile(
    r"\((?:[^()\\]|\\.)*(?:[+*]|\{\d*,\d*\})(?:[^()\\]|\\.)*\)(?:[+*]|\{\d*,\d*\})"
)


def runaway_expression(expression: str) -> bool:
    """Whether an expression nests one repetition inside another (catastrophic backtracking)."""
    return NESTED_REPETITION.search(expression) is not None


class PatternRefused(Exception):
    """A pattern a model taught that would undo a decision you made."""


def asks_a_question(label: str, kind: str | None = None) -> bool:
    """Whether a control's label is a question worth recording (R44).

    An upload is answered by a document, never a fact, so a `file` control
    asks nothing the Inbox could bind.
    """
    key = normalize_answer_key(label)
    if not key or kind == "file" or FINGERPRINT.match(label.strip()):
        return False
    return key not in NOT_QUESTIONS and not SECTION_HEADING.fullmatch(key)


def answered_by_expression(label: str, expressions: list[tuple[re.Pattern[str], str]]) -> bool:
    """Whether active expressions decide this label: they match it and name one fact.

    The worker's resolver decides the same way, so the Inbox never asks for a
    question a run already fills. Expressions that name different facts decide
    nothing and leave it waiting.
    """
    question = label.strip()
    return len({fact for regex, fact in expressions if regex.search(question)}) == 1


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


class Pattern(BaseModel):
    id: UUID
    resolver_id: str
    kind: Literal["expression", "binding"]
    expression: str | None
    question_id: UUID | None
    question_key: str | None
    question_label: str | None
    fact_key: str
    formatter: str | None
    origin: str
    archive_ref: str | None
    status: PatternStatus
    created_at: datetime
    updated_at: datetime


class PatternCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["expression", "binding"]
    question_id: UUID | None = None
    # A binding may name its question by label instead: the worker learning from a
    # verdict knows the wording, not the row. It is recorded if it is new.
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    expression: str | None = Field(default=None, min_length=1, max_length=2000)
    fact_key: str = Field(min_length=3, max_length=200)
    formatter: str | None = Field(default=None, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    origin: Literal["inbox", "archive", "model"] = "inbox"
    archive_ref: str | None = Field(default=None, max_length=200)
    status: Literal["candidate", "active"] = "candidate"
    # The failure this pattern answers, and what replaying it found (a learned one).
    failure: Failure | None = None
    verification: Verification | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> PatternCreate:
        if not FACT_KEY.fullmatch(self.fact_key):
            raise ValueError("fact_key is dotted lowercase words, e.g. work_auth.us_citizen")
        if self.verification is not None and self.verification.failed and self.status == "active":
            raise ValueError("a pattern that failed its replay waits in the Inbox, not active")
        if self.kind == "binding" and (
            (self.question_id is None) == (self.question is None) or self.expression is not None
        ):
            raise ValueError("a binding names its question, by id or label, and no expression")
        if self.kind == "expression":
            if self.expression is None or self.question_id is not None or self.question:
                raise ValueError("an expression has a regular expression and no question")
            try:
                re.compile(self.expression, re.IGNORECASE)
            except re.error as error:
                raise ValueError(f"expression must compile: {error}") from None
            # Every Inbox request runs every active expression; one a model wrote
            # must not be able to hang it.
            if self.origin == "model" and runaway_expression(self.expression):
                raise ValueError("a model's expression may not nest one repetition in another")
        return self


class PatternUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["candidate", "active", "rejected", "retired"]
    # Who decided: you in the Library, or you answering the Inbox.
    origin: Literal["you", "inbox"] = "you"


class Prose(BaseModel):
    id: UUID
    question_id: UUID | None
    question_label: str | None
    application_id: UUID | None
    company_name: str | None
    text: str
    approved: bool
    origin: Literal["manual", "model", "archive"]
    created_at: datetime
    updated_at: datetime


class ProseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=20000)
    approved: bool = True


class ProseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=20000)
    approved: bool | None = None

    @model_validator(mode="after")
    def something_changes(self) -> ProseUpdate:
        if self.text is None and self.approved is None:
            raise ValueError("give text, approved, or both")
        return self


class ArchivedAnswer(BaseModel):
    id: UUID
    question: str | None
    normalized_key: str
    answer_text: str
    reuse_policy: str
    approved: bool
    updated_at: datetime


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


class Inbox(BaseModel):
    candidates: list[InboxCandidate]
    questions: list[InboxQuestion]
    prose: list[Prose]


PATTERN_COLUMNS = """
    pattern.id, pattern.resolver_id, pattern.kind, pattern.expression, pattern.question_id,
    question.normalized_key as question_key, question.sample_label as question_label,
    pattern.fact_key, pattern.formatter, pattern.origin, pattern.archive_ref, pattern.status,
    pattern.created_at, pattern.updated_at
"""
PATTERN_FROM = """
    from app.jobs_patterns pattern
    left join app.jobs_questions question on question.id = pattern.question_id
"""
QUESTION_COLUMNS = """
    id, normalized_key, sample_label, form, disposition, pattern_id, first_seen_at, last_seen_at
"""
PROSE_SELECT = """
    select prose.id, prose.question_id, question.sample_label as question_label,
           prose.application_id, company.name as company_name, prose.text, prose.approved,
           prose.origin, prose.created_at, prose.updated_at
    from app.jobs_prose prose
    left join app.jobs_questions question on question.id = prose.question_id
    left join app.jobs_applications application on application.id = prose.application_id
    left join app.jobs_postings posting on posting.id = application.posting_id
    left join app.jobs_companies company on company.id = posting.company_id
"""


async def active_patterns(connection: AsyncConnection[Any], user_id: UUID) -> list[Pattern]:
    cursor = await connection.execute(
        f"select {PATTERN_COLUMNS} {PATTERN_FROM} "
        "where pattern.user_id = %s and pattern.status = 'active' order by pattern.created_at",
        (user_id,),
    )
    return [Pattern.model_validate(row) for row in await cursor.fetchall()]


async def approved_prose(connection: AsyncConnection[Any], user_id: UUID) -> list[Prose]:
    cursor = await connection.execute(
        f"{PROSE_SELECT} where prose.user_id = %s and prose.approved "
        "order by prose.updated_at desc",
        (user_id,),
    )
    return [Prose.model_validate(row) for row in await cursor.fetchall()]


async def archived_answers(
    connection: AsyncConnection[Any], user_id: UUID
) -> list[ArchivedAnswer]:
    cursor = await connection.execute(
        """
        select id, normalized_question as question, normalized_key, answer_text,
               reuse_policy, approved, updated_at
        from app.jobs_archive_answers where user_id = %s
        order by updated_at desc, id
        """,
        (user_id,),
    )
    return [ArchivedAnswer.model_validate(row) for row in await cursor.fetchall()]


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


class AnswerStore(Protocol):
    async def list_questions(
        self, user_id: UUID, disposition: Disposition | None
    ) -> list[Question]: ...

    async def update_question(
        self, user_id: UUID, question_id: UUID, item: QuestionUpdate
    ) -> Question | None: ...

    async def list_patterns(
        self, user_id: UUID, statuses: list[PatternStatus]
    ) -> list[Pattern]: ...

    async def create_pattern(self, user_id: UUID, item: PatternCreate) -> Pattern | None: ...

    async def update_pattern(
        self, user_id: UUID, pattern_id: UUID, item: PatternUpdate
    ) -> Pattern | None: ...

    async def list_prose(self, user_id: UUID) -> list[Prose]: ...

    async def create_prose(self, user_id: UUID, item: ProseCreate) -> Prose: ...

    async def update_prose(
        self, user_id: UUID, prose_id: UUID, item: ProseUpdate
    ) -> Prose | None: ...

    async def delete_prose(self, user_id: UUID, prose_id: UUID) -> bool: ...

    async def list_archive(self, user_id: UUID) -> list[ArchivedAnswer]: ...

    async def inbox(self, user_id: UUID) -> Inbox: ...


async def _activate(
    connection: AsyncConnection[Any],
    user_id: UUID,
    pattern_id: UUID,
    *,
    origin: Origin,
    failure: Failure | None = None,
) -> None:
    """Make a pattern the one that answers; a binding replaces its question's previous one,
    and the binding it replaces is recorded retired by whoever replaced it."""
    cursor = await connection.execute(
        "select kind, question_id from app.jobs_patterns where id = %s and user_id = %s",
        (pattern_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return
    if row["kind"] == "binding":
        cursor = await connection.execute(
            f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.user_id = %s "
            "and pattern.question_id = %s and pattern.kind = 'binding' "
            "and pattern.status = 'active' and pattern.id <> %s",
            (user_id, row["question_id"], pattern_id),
        )
        displaced = [Pattern.model_validate(item) for item in await cursor.fetchall()]
        await connection.execute(
            """
            update app.jobs_patterns set status = 'retired'
            where user_id = %s and question_id = %s and kind = 'binding'
              and status = 'active' and id <> %s
            """,
            (user_id, row["question_id"], pattern_id),
        )
        for old in displaced:
            await _record_pattern(
                connection, user_id, "retire", old, old.model_copy(update={"status": "retired"}),
                origin=origin, failure=failure,
            )
    await connection.execute(
        "update app.jobs_patterns set status = 'active' where id = %s and user_id = %s",
        (pattern_id, user_id),
    )
    if row["kind"] == "binding":
        await connection.execute(
            """
            update app.jobs_questions set disposition = 'pattern', pattern_id = %s
            where id = %s and user_id = %s
            """,
            (pattern_id, row["question_id"], user_id),
        )


async def _model_refusal(
    connection: AsyncConnection[Any], user_id: UUID, item: PatternCreate
) -> str | None:
    """Why a pattern a model taught may not be saved: it would undo a decision you made.

    You rejected or retired the same pattern, the question is bound already, or
    you said the question is a one-off or wants prose. An expression already
    waiting in the Inbox is not saved twice.
    """
    if item.kind == "expression":
        cursor = await connection.execute(
            "select status from app.jobs_patterns where user_id = %s and kind = 'expression' "
            "and expression = %s and fact_key = %s "
            "and status in ('rejected', 'retired', 'candidate', 'repaired') limit 1",
            (user_id, item.expression, item.fact_key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if row["status"] in ("candidate", "repaired"):
            return "this expression already waits in the Inbox"
        return "you rejected or retired this expression"
    cursor = await connection.execute(
        "select disposition from app.jobs_questions where id = %s and user_id = %s",
        (item.question_id, user_id),
    )
    question = await cursor.fetchone()
    if question is not None and question["disposition"] in ("one_off", "long_form"):
        return f"you marked this question {question['disposition'].replace('_', '-')}"
    cursor = await connection.execute(
        """
        select status from app.jobs_patterns
        where user_id = %s and question_id = %s and kind = 'binding'
          and (status = 'active' or (fact_key = %s and status in ('rejected', 'retired')))
        """,
        (user_id, item.question_id, item.fact_key),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row["status"] == "active":
        return "the question is bound already, and a model never replaces a binding"
    return "you rejected or retired this binding"


async def _record_pattern(
    connection: AsyncConnection[Any],
    user_id: UUID,
    action: Action,
    before: Pattern | None,
    after: Pattern | None,
    *,
    origin: Origin,
    failure: Failure | None = None,
    verification: Verification | None = None,
) -> None:
    """Record a pattern change; one that changed nothing, and was not checked, is not one."""
    subject = after or before
    if subject is None:
        return
    shown = {"updated_at"}
    if (
        before is not None and after is not None and verification is None
        and before.model_dump(exclude=shown) == after.model_dump(exclude=shown)
    ):
        return
    await record_change(
        connection,
        user_id,
        subject="pattern",
        subject_id=str(subject.id),
        action=action,
        before=before.model_dump(mode="json") if before is not None else None,
        after=after.model_dump(mode="json") if after is not None else None,
        origin=origin,
        failure=failure,
        verification=verification,
    )


async def _pattern(
    connection: AsyncConnection[Any], user_id: UUID, pattern_id: UUID
) -> Pattern | None:
    cursor = await connection.execute(
        f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.id = %s and pattern.user_id = %s",
        (pattern_id, user_id),
    )
    row = await cursor.fetchone()
    return Pattern.model_validate(row) if row else None


class PostgresAnswerStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def list_questions(
        self, user_id: UUID, disposition: Disposition | None
    ) -> list[Question]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {QUESTION_COLUMNS} from app.jobs_questions where user_id = %s "
                "and (%s::text is null or disposition = %s) order by last_seen_at desc",
                (user_id, disposition, disposition),
            )
            return [Question.model_validate(row) for row in await cursor.fetchall()]

    async def update_question(
        self, user_id: UUID, question_id: UUID, item: QuestionUpdate
    ) -> Question | None:
        """Disposition a question; leaving `pattern` retires the binding that answered it."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            await connection.execute(
                """
                update app.jobs_patterns set status = 'retired'
                where user_id = %s and question_id = %s and kind = 'binding' and status = 'active'
                """,
                (user_id, question_id),
            )
            cursor = await connection.execute(
                f"""
                update app.jobs_questions set disposition = %s, pattern_id = null
                where id = %s and user_id = %s
                returning {QUESTION_COLUMNS}
                """,
                (item.disposition, question_id, user_id),
            )
            row = await cursor.fetchone()
        return Question.model_validate(row) if row else None

    async def list_patterns(
        self, user_id: UUID, statuses: list[PatternStatus]
    ) -> list[Pattern]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.user_id = %s "
                "and pattern.status = any(%s) order by pattern.status, pattern.updated_at desc",
                (user_id, list(statuses)),
            )
            return [Pattern.model_validate(row) for row in await cursor.fetchall()]

    async def create_pattern(self, user_id: UUID, item: PatternCreate) -> Pattern | None:
        """Create a pattern, or reuse the binding already made for that question and fact.

        None when the binding's question is not this user's, or its label asks
        nothing. A pattern a model taught that would undo your decision raises
        `PatternRefused`.
        """
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            if item.question is not None:
                question_id = await upsert_question(
                    connection, user_id=user_id, label=item.question
                )
                if question_id is None:
                    return None
                item = item.model_copy(update={"question_id": question_id, "question": None})
            elif item.question_id is not None:
                cursor = await connection.execute(
                    "select 1 from app.jobs_questions where id = %s and user_id = %s",
                    (item.question_id, user_id),
                )
                if await cursor.fetchone() is None:
                    return None
            if item.origin == "model":
                refusal = await _model_refusal(connection, user_id, item)
                if refusal is not None:
                    raise PatternRefused(refusal)
            before: Pattern | None = None
            if item.kind == "binding":
                cursor = await connection.execute(
                    f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.user_id = %s "
                    "and pattern.question_id = %s and pattern.fact_key = %s "
                    "and pattern.kind = 'binding'",
                    (user_id, item.question_id, item.fact_key),
                )
                existing = await cursor.fetchone()
                before = Pattern.model_validate(existing) if existing else None
            cursor = await connection.execute(
                """
                with new as (select gen_random_uuid() as id)
                insert into app.jobs_patterns (
                  id, user_id, resolver_id, kind, expression, question_id, fact_key,
                  formatter, origin, archive_ref, status
                )
                select new.id, %s, %s || ':' || new.id::text, %s, %s, %s, %s, %s, %s, %s,
                       'candidate'
                from new
                on conflict (user_id, question_id, fact_key) where kind = 'binding'
                do update set formatter = coalesce(excluded.formatter, app.jobs_patterns.formatter),
                              status = case when app.jobs_patterns.status in ('rejected', 'retired')
                                            then 'candidate' else app.jobs_patterns.status end
                returning id
                """,
                (
                    user_id, item.kind, item.kind, item.expression, item.question_id,
                    item.fact_key, item.formatter, item.origin, item.archive_ref,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Pattern insert returned no id")
            pattern_id = UUID(str(row["id"]))
            origin = PATTERN_ORIGINS[item.origin]
            if item.status == "active":
                await _activate(
                    connection, user_id, pattern_id, origin=origin, failure=item.failure
                )
            after = await _pattern(connection, user_id, pattern_id)
            await _record_pattern(
                connection, user_id, "create" if before is None else "update", before, after,
                origin=origin, failure=item.failure, verification=item.verification,
            )
            return after

    async def update_pattern(
        self, user_id: UUID, pattern_id: UUID, item: PatternUpdate
    ) -> Pattern | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            current = await _pattern(connection, user_id, pattern_id)
            if current is None:
                return None
            if item.status == "active":
                await _activate(connection, user_id, pattern_id, origin=item.origin)
            else:
                await connection.execute(
                    "update app.jobs_patterns set status = %s where id = %s and user_id = %s",
                    (item.status, pattern_id, user_id),
                )
                # A binding that stops answering hands its question back to the Inbox.
                await connection.execute(
                    """
                    update app.jobs_questions set disposition = 'unbound', pattern_id = null
                    where user_id = %s and pattern_id = %s
                    """,
                    (user_id, pattern_id),
                )
            after = await _pattern(connection, user_id, pattern_id)
            await _record_pattern(
                connection, user_id, STATUS_ACTIONS.get(item.status, "update"), current, after,
                origin=item.origin,
            )
            return after

    async def list_prose(self, user_id: UUID) -> list[Prose]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.user_id = %s "
                "order by prose.approved desc, prose.updated_at desc",
                (user_id,),
            )
            return [Prose.model_validate(row) for row in await cursor.fetchall()]

    async def create_prose(self, user_id: UUID, item: ProseCreate) -> Prose:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            question_id = await upsert_question(
                connection, user_id=user_id, label=item.question, form="long"
            )
            cursor = await connection.execute(
                """
                insert into app.jobs_prose (user_id, question_id, text, approved, origin)
                values (%s, %s, %s, %s, 'manual') returning id
                """,
                (user_id, question_id, item.text.strip(), item.approved),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Prose insert returned no id")
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.id = %s", (row["id"],)
            )
            created = await cursor.fetchone()
        return Prose.model_validate(created)

    async def update_prose(
        self, user_id: UUID, prose_id: UUID, item: ProseUpdate
    ) -> Prose | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.jobs_prose
                set text = coalesce(%s, text), approved = coalesce(%s, approved)
                where id = %s and user_id = %s returning id
                """,
                (item.text.strip() if item.text else None, item.approved, prose_id, user_id),
            )
            if await cursor.fetchone() is None:
                return None
            cursor = await connection.execute(f"{PROSE_SELECT} where prose.id = %s", (prose_id,))
            row = await cursor.fetchone()
        return Prose.model_validate(row) if row else None

    async def delete_prose(self, user_id: UUID, prose_id: UUID) -> bool:
        """Prose that should not be reused; the answer itself stays in the application history."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                "delete from app.jobs_prose where id = %s and user_id = %s", (prose_id, user_id)
            )
        return bool(cursor.rowcount > 0)

    async def list_archive(self, user_id: UUID) -> list[ArchivedAnswer]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await archived_answers(connection, user_id)

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
            cursor = await connection.execute(
                f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.user_id = %s "
                "and pattern.status in ('candidate', 'repaired') order by pattern.created_at",
                (user_id,),
            )
            waiting = [Pattern.model_validate(row) for row in await cursor.fetchall()]
            checks = await latest_failed(
                connection, user_id, [str(pattern.id) for pattern in waiting]
            )
            candidates = [
                InboxCandidate(
                    pattern=pattern,
                    answers=await _answers_for(connection, user_id, pattern.question_id)
                    if pattern.question_id
                    else [],
                    check=checks.get(str(pattern.id)),
                )
                for pattern in waiting
            ]
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
            questions = [
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
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.user_id = %s and not prose.approved "
                "order by prose.created_at",
                (user_id,),
            )
            prose = [Prose.model_validate(row) for row in await cursor.fetchall()]
        return Inbox(candidates=candidates, questions=questions, prose=prose)


async def get_answer_store(request: Request) -> AnswerStore:
    settings: Settings = request.app.state.settings
    return PostgresAnswerStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs", tags=["answers"])
Store = Annotated[AnswerStore, Depends(get_answer_store)]


@router.get("/inbox", response_model=Inbox)
async def get_inbox(principal: CurrentPrincipal, store: Store) -> Inbox:
    return await store.inbox(principal.user_id)


@router.get("/questions", response_model=list[Question])
async def list_questions(
    principal: CurrentPrincipal,
    store: Store,
    disposition: Annotated[Disposition | None, Query()] = None,
) -> list[Question]:
    return await store.list_questions(principal.user_id, disposition)


@router.patch("/questions/{question_id}", response_model=Question)
async def update_question(
    question_id: UUID, item: QuestionUpdate, principal: CurrentPrincipal, store: Store
) -> Question:
    question = await store.update_question(principal.user_id, question_id, item)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return question


@router.get("/patterns", response_model=list[Pattern])
async def list_patterns(
    principal: CurrentPrincipal,
    store: Store,
    statuses: Annotated[list[PatternStatus] | None, Query(alias="status")] = None,
) -> list[Pattern]:
    return await store.list_patterns(principal.user_id, statuses or ["active", "candidate"])


@router.post("/patterns", response_model=Pattern, status_code=status.HTTP_201_CREATED)
async def create_pattern(item: PatternCreate, principal: CurrentPrincipal, store: Store) -> Pattern:
    try:
        pattern = await store.create_pattern(principal.user_id, item)
    except PatternRefused as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from None
    if pattern is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return pattern


@router.patch("/patterns/{pattern_id}", response_model=Pattern)
async def update_pattern(
    pattern_id: UUID, item: PatternUpdate, principal: CurrentPrincipal, store: Store
) -> Pattern:
    pattern = await store.update_pattern(principal.user_id, pattern_id, item)
    if pattern is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pattern not found")
    return pattern


@router.get("/prose", response_model=list[Prose])
async def list_prose(principal: CurrentPrincipal, store: Store) -> list[Prose]:
    return await store.list_prose(principal.user_id)


@router.post("/prose", response_model=Prose, status_code=status.HTTP_201_CREATED)
async def create_prose(item: ProseCreate, principal: CurrentPrincipal, store: Store) -> Prose:
    return await store.create_prose(principal.user_id, item)


@router.patch("/prose/{prose_id}", response_model=Prose)
async def update_prose(
    prose_id: UUID, item: ProseUpdate, principal: CurrentPrincipal, store: Store
) -> Prose:
    prose = await store.update_prose(principal.user_id, prose_id, item)
    if prose is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prose not found")
    return prose


@router.delete("/prose/{prose_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prose(prose_id: UUID, principal: CurrentPrincipal, store: Store) -> Response:
    if not await store.delete_prose(principal.user_id, prose_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prose not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/archive/answers", response_model=list[ArchivedAnswer])
async def list_archive(principal: CurrentPrincipal, store: Store) -> list[ArchivedAnswer]:
    return await store.list_archive(principal.user_id)
