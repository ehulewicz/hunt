"""Patterns: bindings and expressions to facts, what a model may not undo, and activation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_os_backend.database import StoreBase
from personal_os_backend.jobs_changes import (
    Action,
    Failure,
    Origin,
    Verification,
    record_change,
)
from personal_os_backend.jobs_questions import upsert_question

FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
PatternStatus = Literal["candidate", "repaired", "validated", "active", "rejected", "retired"]
# A pattern that can still become active, or is.
LIVE_STATUSES = ("candidate", "repaired", "validated", "active")
# Who a new pattern's change is recorded as: an archive adoption is yours.
PATTERN_ORIGINS: dict[str, Origin] = {"inbox": "inbox", "archive": "you", "model": "model"}
STATUS_ACTIONS: dict[str, Action] = {"retired": "retire", "rejected": "reject"}


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
    # The model-taught binding this one corrects (a wrong answer check).
    replaces: UUID | None = None
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
        if self.replaces is not None and (
            self.kind != "binding" or self.origin != "model"
        ):
            raise ValueError("only a binding a model taught can be replaced, by a model")
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


async def active_patterns(connection: AsyncConnection[Any], user_id: UUID) -> list[Pattern]:
    cursor = await connection.execute(
        f"select {PATTERN_COLUMNS} {PATTERN_FROM} "
        "where pattern.user_id = %s and pattern.status = 'active' order by pattern.created_at",
        (user_id,),
    )
    return [Pattern.model_validate(row) for row in await cursor.fetchall()]


class PatternsStore(Protocol):
    async def list_patterns(
        self, user_id: UUID, statuses: list[PatternStatus]
    ) -> list[Pattern]: ...

    async def create_pattern(self, user_id: UUID, item: PatternCreate) -> Pattern | None: ...

    async def update_pattern(
        self, user_id: UUID, pattern_id: UUID, item: PatternUpdate
    ) -> Pattern | None: ...


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
        select id, origin from app.jobs_patterns
        where user_id = %s and question_id = %s and kind = 'binding' and status = 'active'
        """,
        (user_id, item.question_id),
    )
    active = await cursor.fetchone()
    if item.replaces is not None:
        if (
            active is None
            or active["id"] != item.replaces
            or active["origin"] != "model"
        ):
            return (
                "only the binding a model taught, and only the one named, "
                "can be replaced"
            )
    elif active is not None:
        return "the question is bound already, and a model never replaces a binding"
    cursor = await connection.execute(
        """
        select status from app.jobs_patterns
        where user_id = %s and question_id = %s and kind = 'binding'
          and fact_key = %s and status in ('rejected', 'retired') limit 1
        """,
        (user_id, item.question_id, item.fact_key),
    )
    if await cursor.fetchone() is not None:
        return "you rejected or retired this binding"
    return None


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


class PatternsMixin(StoreBase):
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
